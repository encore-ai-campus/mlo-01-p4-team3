"""광고 serve 단계별 시간 수집기(ads/infra/timing.py)를 검증한다(11/11 지연 원인 분리).

실제 MySQL 없이 별도 프로세스의 최소 Django(sqlite 파일)에서 연결 상태 표시·쿼리 수·단계 누적을 확인한다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROBE = r'''
import json, os, sys, threading
import django
from django.conf import settings
settings.configure(SECRET_KEY=os.urandom(16).hex(),
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": sys.argv[1], "CONN_MAX_AGE": 60}})
django.setup()
from django.db import connection
from ads.infra import timing

out = {}
# 수집기 밖에서는 아무것도 하지 않는다(작업자·시험 경로).
with timing.phase("outside"):
    pass
timing.record("outside", 1.0)

with timing.collect() as first:
    with timing.phase("campaign_lock"):
        with connection.cursor() as c:
            c.execute("SELECT 1")
    with timing.phase("campaign_lock"):
        with connection.cursor() as c:
            c.execute("SELECT 1"); c.execute("SELECT 2")
    timing.record("commit", 0.002)
out["first"] = first.fields()

with timing.collect() as second:          # 같은 스레드·연결 유지
    pass
out["second"] = second.fields()

def other():
    with timing.collect() as third:      # 새 스레드: 스레드 첫 연결
        pass
    out["third"] = third.fields()
t = threading.Thread(target=other, name="worker-x"); t.start(); t.join()
print(json.dumps(out))
'''


class ServeTimingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"} | {"PYTHONPATH": str(ROOT)}
        done = subprocess.run([sys.executable, "-c", PROBE, str(Path(folder.name) / "t.sqlite3")],
                              capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        self.out = json.loads(done.stdout.strip().splitlines()[-1])

    def test_phases_accumulate_with_counts_and_queries(self):
        first = self.out["first"]
        self.assertEqual(first["campaign_lock_n"], 2)
        self.assertGreaterEqual(first["campaign_lock_ms"], 0)
        self.assertAlmostEqual(first["commit_ms"], 2.0, places=3)
        self.assertEqual(first["db_queries"], 3)
        self.assertIn("connect_ms", first)
        self.assertNotIn("outside_ms", first)

    def test_connection_flags_show_new_and_reused_connections(self):
        self.assertTrue(self.out["first"]["new_connection"])
        self.assertTrue(self.out["first"]["thread_first_connection"])
        self.assertFalse(self.out["second"]["new_connection"])
        self.assertFalse(self.out["second"]["thread_first_connection"])
        self.assertEqual(self.out["second"]["db_queries"], 0)
        self.assertTrue(self.out["third"]["new_connection"])
        self.assertTrue(self.out["third"]["thread_first_connection"])
        self.assertEqual(self.out["third"]["thread"], "worker-x")

    def test_only_numbers_flags_and_thread_name(self):
        for fields in self.out.values():
            for key, value in fields.items():
                self.assertTrue(key == "thread" or isinstance(value, (int, float, bool)), (key, value))


if __name__ == "__main__":
    unittest.main()
