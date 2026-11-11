"""광고 서버 실행 방식(gunicorn gthread)과 DB 연결 재사용을 검증한다(11/11).

daphne(ASGI)는 요청마다 작업 스레드를 새로 만들어 스레드별 DB 연결을 재사용하지 못한다. WSGI 고정 스레드에서
CONN_MAX_AGE가 켜져 있으면 같은 스레드의 다음 요청이 같은 연결을 쓴다. 실제 MySQL 없이 sqlite 파일로 같은
Django 연결 수명 규칙(request_started/finished의 close_old_connections)을 확인한다.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ops.mysql import service_runtime

ROOT = Path(__file__).resolve().parents[2]

# 별도 프로세스에서 최소 Django를 띄워 WSGIHandler를 직접 부른다(테스트 러너의 설정·Client 신호 해제와 격리).
PROBE = r'''
import json, os, sys, threading
import django
from django.conf import settings
settings.configure(
    DEBUG=False, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__, SECRET_KEY="x", MIDDLEWARE=[],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": sys.argv[1],
                           "CONN_MAX_AGE": int(sys.argv[2]), "CONN_HEALTH_CHECKS": True}})
django.setup()
from django.db import connection
from django.http import HttpResponse
from django.urls import path
from django.core.handlers.wsgi import WSGIHandler

def probe(request):
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return HttpResponse(str(id(connection.connection)))

urlpatterns = [path("probe", probe)]
app = WSGIHandler()

def call():
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/probe", "SERVER_NAME": "t", "SERVER_PORT": "80",
               "wsgi.url_scheme": "http", "wsgi.input": __import__("io").BytesIO(b""), "wsgi.errors": sys.stderr}
    out = []
    body = b"".join(app(environ, lambda status, headers: out.append(status)))
    return body.decode()

mode = sys.argv[3]
if mode == "same_thread":          # gunicorn gthread: 고정 스레드가 요청을 이어서 받는다
    ids = [call() for _ in range(3)]
else:                              # daphne/ASGI 모사: 요청마다 새 스레드
    ids = []
    for _ in range(3):
        t = threading.Thread(target=lambda: ids.append(call())); t.start(); t.join()
print(json.dumps(ids))
'''


def _probe(max_age, mode):
    with tempfile.TemporaryDirectory() as folder:
        result = subprocess.run([sys.executable, "-c", PROBE, str(Path(folder) / "db.sqlite3"), str(max_age), mode],
                                capture_output=True, text=True, cwd=folder, timeout=60)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])


class ConnectionReuse(unittest.TestCase):
    def test_fixed_thread_reuses_connection(self):
        ids = _probe(60, "same_thread")
        self.assertEqual(len(set(ids)), 1, ids)

    def test_max_age_zero_reconnects_every_request(self):
        ids = _probe(0, "same_thread")
        self.assertEqual(len(set(ids)), 3, ids)

    def test_thread_per_request_cannot_reuse(self):
        """daphne처럼 요청마다 스레드가 바뀌면 CONN_MAX_AGE를 켜도 새 연결이다(gunicorn으로 바꾼 이유)."""
        ids = _probe(60, "thread_per_request")
        self.assertEqual(len(ids), 3)
        self.assertGreater(len(set(ids)), 1, ids)


class AdsSettings(unittest.TestCase):
    def _settings(self, env):
        code = ("import json, ads.project.settings as s; d=s.DATABASES['default'];"
                "print(json.dumps([d['CONN_MAX_AGE'], d['CONN_HEALTH_CHECKS']]))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT), "DJANGO_SECRET_KEY": "test-only", "PALDO_ADS_TOKEN_KEY": "test-only", **env}, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_default_and_override(self):
        env = {k: v for k, v in os.environ.items() if k != "PALDO_ADS_DB_CONN_MAX_AGE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self._settings({}), [60, True])
            self.assertEqual(self._settings({"PALDO_ADS_DB_CONN_MAX_AGE": "0"}), [0, True])


class AdsRuntime(unittest.TestCase):
    def _run(self, config, env=None):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "profile.json"
            profile.write_text(json.dumps({"source": folder, "environment": {}, **config}), encoding="utf-8")
            clean = {k: v for k, v in os.environ.items() if k != "PALDO_ADS_SERVER"} | (env or {})
            with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(service_runtime.os, "chdir"), \
                    mock.patch.object(service_runtime.os, "execv") as execv:
                service_runtime.main(["--profile", str(profile), "ads"])
                return execv.call_args[0][1]

    def test_default_is_gunicorn_single_process_fixed_threads(self):
        argv = self._run({"bind": "10.0.0.5"})
        self.assertEqual(argv[1:3], ["-m", "gunicorn"])
        self.assertEqual(argv[argv.index("--worker-class") + 1], "gthread")
        self.assertEqual(argv[argv.index("--workers") + 1], "1")
        self.assertEqual(argv[argv.index("--threads") + 1], str(service_runtime.ADS_THREADS))
        self.assertEqual(argv[argv.index("--bind") + 1], "10.0.0.5:8001")
        self.assertEqual(argv[-1], "ads.project.wsgi:application")

    def test_rollback_to_daphne_by_profile_or_env(self):
        self.assertEqual(self._run({"ads_server": "daphne"})[-5:],
                         ["-b", "127.0.0.1", "-p", "8001", "ads.project.asgi:application"])
        self.assertEqual(self._run({}, {"PALDO_ADS_SERVER": "daphne"})[-1], "ads.project.asgi:application")

    def test_unknown_server_rejected(self):
        with self.assertRaises(SystemExit):
            self._run({"ads_server": "uwsgi"})

    def test_other_roles_ignore_ads_server(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "profile.json"
            profile.write_text(json.dumps({"source": folder, "environment": {}, "ads_server": "uwsgi"}), encoding="utf-8")
            with mock.patch.object(service_runtime.os, "chdir"), mock.patch.object(service_runtime.os, "execv") as execv:
                service_runtime.main(["--profile", str(profile), "refund"])
                self.assertIn("ad_worker", execv.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
