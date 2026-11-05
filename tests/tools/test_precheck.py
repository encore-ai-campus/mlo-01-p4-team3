"""tools/precheck.py: 판정 함수와 단계 조립(가짜 실행기)."""
from __future__ import annotations

import unittest

from tools import precheck
from tools.precheck import StepResult, count_tests, run_precheck, summarize, test_rc_ok


class Rules(unittest.TestCase):
    def test_rc(self):
        self.assertTrue(test_rc_ok(0))
        self.assertTrue(test_rc_ok(5))  # 테스트 0개 폴더
        self.assertFalse(test_rc_ok(1))

    def test_count(self):
        self.assertEqual(count_tests("...\nRan 82 tests in 3.1s\n\nOK\nRan 1 test in 0s\n"), 83)
        self.assertEqual(count_tests("no tests"), 0)

    def test_summary_pass(self):
        rc, line = summarize([StepResult("구조", True), StepResult("테스트", True, tests=12)], "abc1234", 31.4)
        self.assertEqual(rc, 0)
        self.assertEqual(line, "precheck: 통과 (구조·테스트, 테스트 12개, base abc1234, 31초)")

    def test_summary_fail(self):
        rc, line = summarize([StepResult("구조", True), StepResult("비밀", False)], None, 2)
        self.assertEqual(rc, 1)
        self.assertEqual(line, "precheck: 실패 (비밀; base 없음, 2초)")


class FakeRunner:
    def __init__(self, fail=(), dirs=("assets", "contract", "tools")):
        self.calls: list[tuple[list[str], dict | None]] = []
        self.fail = set(fail)
        self.dirs = list(dirs)

    def run(self, args, env=None):
        self.calls.append((args, env))
        key = args[2] if args[1] == "-m" else args[0]
        if key == "unittest":
            folder = args[args.index("-s") + 1]
            return (1 if folder in self.fail else 0), "Ran 3 tests in 0.1s\n\nOK\n"
        return (1 if key in self.fail else 0), f"{key} out"

    def test_dirs(self):
        return self.dirs


def _modules(runner):
    return [a[2] if a[1] == "-m" else a[0] for a, _ in runner.calls]


class Flow(unittest.TestCase):
    def test_order_and_assets_skipped(self):
        r = FakeRunner()
        results = run_precheck(r, "b" * 40)
        self.assertEqual([x.name for x in results], ["구조", "비밀", "테스트", "문서", "증빙"])
        self.assertTrue(all(x.ok for x in results))
        folders = [a[a.index("-s") + 1] for a, _ in r.calls if "-s" in a]
        self.assertEqual(folders, ["tests/contract", "tests/tools"])
        self.assertEqual(results[2].tests, 6)
        evidence = r.calls[-1][0]
        self.assertEqual(evidence[2:], ["tools.check_evidence", "--changed", "b" * 40])

    def test_with_assets(self):
        r = FakeRunner()
        run_precheck(r, "b" * 40, with_assets=True)
        self.assertIn("tests/assets", [a[a.index("-s") + 1] for a, _ in r.calls if "-s" in a])

    def test_failures_keep_running(self):
        r = FakeRunner(fail={"tools.check_secrets", "tests/tools"})
        results = run_precheck(r, "b" * 40)
        self.assertEqual([x.name for x in results if not x.ok], ["비밀", "테스트"])
        self.assertIn("tools.check_evidence", _modules(r))  # 실패 뒤에도 끝까지

    def test_missing_base_fails_docs_and_evidence(self):
        r = FakeRunner()
        results = run_precheck(r, None)
        self.assertEqual([x.name for x in results if not x.ok], ["문서", "증빙"])
        self.assertNotIn("tools.check_docs", _modules(r))

    def test_pr_body_passed_by_env(self):
        r = FakeRunner()
        run_precheck(r, "b" * 40, pr_body="Task: E-05-02\n\nRefs #95\n")
        args, env = r.calls[-1]
        self.assertEqual(args[2:5], ["tools.check_evidence", "--pr-body-env", precheck.PR_BODY_ENV])
        self.assertEqual(env, {precheck.PR_BODY_ENV: "Task: E-05-02\n\nRefs #95\n"})


class Cli(unittest.TestCase):
    def test_main_rc_and_summary(self):
        class R(FakeRunner):
            def merge_base(self, ref):
                return "c" * 40

            def resolve(self, ref):
                return None

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = precheck.main([], runner=R())
        self.assertEqual(rc, 0)
        self.assertTrue(buf.getvalue().rstrip().splitlines()[-1].startswith("precheck: 통과"))
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = precheck.main(["--base", "nope"], runner=R())
        self.assertEqual(rc, 1)
        self.assertIn("base 없음", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
