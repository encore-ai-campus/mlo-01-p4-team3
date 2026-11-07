"""tools/precheck.py 통과 기록: 판정 함수, 기록 파일, 기록·생략 흐름(가짜 실행기·임시 저장소)."""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from tools import precheck
from tools.precheck import (PassRecord, PassStore, check_pass, format_record, may_record, parse_record,
                            record_pass, skip_decision)

A = "a" * 40
B = "b" * 40
C = "c" * 40


class Rules(unittest.TestCase):
    def test_record_round_trip(self):
        rec = PassRecord(A, B, "pr-body", "2026-11-07T01:02:03Z")
        self.assertEqual(parse_record(format_record(rec)), rec)

    def test_parse_rejects_broken(self):
        self.assertIsNone(parse_record("head=xyz\nbase=" + B))
        self.assertIsNone(parse_record("base=" + B))
        self.assertIsNone(parse_record(""))

    def test_skip_same_sha_same_base(self):
        skip, why = skip_decision(PassRecord(A, B, "plain", "t"), A, B, False)
        self.assertTrue(skip)
        self.assertIn("검사 생략", why)

    def test_full_when_base_changed(self):
        skip, why = skip_decision(PassRecord(A, B, "plain", "t"), A, C, False)
        self.assertFalse(skip)
        self.assertIn("비교 기준이 바뀜", why)

    def test_full_when_no_record_or_other_sha_or_no_base(self):
        self.assertFalse(skip_decision(None, A, B, False)[0])
        self.assertFalse(skip_decision(PassRecord(C, B, "plain", "t"), A, B, False)[0])
        self.assertFalse(skip_decision(PassRecord(A, B, "plain", "t"), A, None, False)[0])

    def test_full_when_disabled(self):
        skip, why = skip_decision(PassRecord(A, B, "plain", "t"), A, B, True)
        self.assertFalse(skip)
        self.assertIn(precheck.NO_CACHE_ENV, why)

    def test_may_record(self):
        self.assertTrue(may_record(0, True, True, True, A, A, B))
        self.assertFalse(may_record(1, True, True, True, A, A, B))     # 실패
        self.assertFalse(may_record(0, True, True, False, A, A, B))    # 끝날 때 더러운 트리
        self.assertFalse(may_record(0, True, False, True, A, A, B))    # 시작 때 더러웠다가 되돌림
        self.assertFalse(may_record(0, False, True, True, A, A, B))    # push 때 증빙 검사 실패
        self.assertFalse(may_record(0, True, True, True, A, C, B))     # 실행 중 HEAD 바뀜
        self.assertFalse(may_record(0, True, True, True, None, None, B))
        self.assertFalse(may_record(0, True, True, True, A, A, None))  # 기준 없음


class Store(unittest.TestCase):
    def test_write_read_prune(self):
        with tempfile.TemporaryDirectory() as d:
            store = PassStore(Path(d) / "precheck-pass")
            self.assertIsNone(store.read(A))
            store.write(PassRecord(A, B, "plain", "t"))
            self.assertEqual(store.read(A), PassRecord(A, B, "plain", "t"))
            self.assertIsNone(store.read("../x"))
            old = time.time() - timedelta(days=20).total_seconds()
            os.utime(store.dir / A, (old, old))
            store.prune(time.time())
            self.assertIsNone(store.read(A))


class FakeRunner:
    def __init__(self, head=A, clean=True, evidence_rc=0, merge_base=B):
        self.head, self.clean, self.evidence_rc, self.mb = head, clean, evidence_rc, merge_base
        self.calls: list[list[str]] = []

    def run(self, args, env=None):
        self.calls.append(args)
        return self.evidence_rc, ""

    def tree_clean(self):
        return self.clean

    def resolve(self, ref):
        return self.head

    def merge_base(self, ref, other="HEAD"):
        return self.mb

    def git_common_dir(self):
        return None


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PassStore(Path(self.tmp.name) / "precheck-pass")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_pass_records_and_hook_skips(self):
        r = FakeRunner()
        self.assertTrue(record_pass(r, 0, B, A, True, False, None, store=self.store))
        self.assertEqual(r.calls, [])  # PR 본문이 없으면 증빙 검사를 다시 돌리지 않는다
        self.assertEqual(self.store.read(A).mode, "plain")
        self.assertTrue(check_pass(r, A, {}, store=self.store)[0])

    def test_pr_body_reruns_push_evidence_check(self):
        r = FakeRunner()
        self.assertTrue(record_pass(r, 0, B, A, True, True, "Task: P-10", store=self.store))
        self.assertEqual(r.calls[0][-3:], ["tools.check_evidence", "--changed", B])
        self.assertEqual(self.store.read(A).mode, "with-assets,pr-body")
        r2 = FakeRunner(evidence_rc=1)
        self.assertFalse(record_pass(r2, 0, B, C, True, False, "Task: P-10", store=self.store))
        self.assertIsNone(self.store.read(C))

    def test_failure_or_dirty_tree_records_nothing(self):
        self.assertFalse(record_pass(FakeRunner(), 1, B, A, True, False, None, store=self.store))
        self.assertFalse(record_pass(FakeRunner(clean=False), 0, B, A, True, False, None, store=self.store))
        self.assertFalse(record_pass(FakeRunner(), 0, B, A, False, False, None, store=self.store))
        self.assertIsNone(self.store.read(A))

    def test_hook_full_check_when_base_moved_or_disabled(self):
        record_pass(FakeRunner(), 0, B, A, True, False, None, store=self.store)
        self.assertFalse(check_pass(FakeRunner(merge_base=C), A, {}, store=self.store)[0])
        skip, why = check_pass(FakeRunner(), A, {precheck.NO_CACHE_ENV: "1"}, store=self.store)
        self.assertFalse(skip)
        self.assertIn(precheck.NO_CACHE_ENV, why)


def _git(cwd, *args):
    env = precheck.clean_env(dict(os.environ))
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class RealRepo(unittest.TestCase):
    """임시 저장소에서 기록 위치(공용 git 폴더, worktree 공유)와 생략 판정을 확인한다."""

    def test_record_shared_across_worktrees(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            (repo / "f.txt").write_text("x", encoding="utf-8")
            _git(repo, "add", "f.txt")
            _git(repo, "commit", "-q", "-m", "c1")
            _git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
            head = _git(repo, "rev-parse", "HEAD")
            runner = precheck.SubprocessRunner(repo)
            self.assertTrue(runner.tree_clean())
            self.assertTrue(record_pass(runner, 0, runner.merge_base("origin/dev"), head, True, False, None))
            wt = Path(d) / "wt"
            _git(repo, "worktree", "add", "-q", "--detach", str(wt), head)
            skip, why = check_pass(precheck.SubprocessRunner(wt), head, {})
            self.assertTrue(skip, why)
            self.assertTrue((repo / ".git" / precheck.PASS_DIR_NAME / head).is_file())
            (repo / "g.txt").write_text("y", encoding="utf-8")  # 추적 안 하는 파일 = 더러운 트리
            self.assertFalse(runner.tree_clean())


if __name__ == "__main__":
    unittest.main()
