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
            folder = args[args.index("-s") + 1] if "-s" in args else " ".join(args[3:])
            return (1 if folder in self.fail else 0), "Ran 3 tests in 0.1s\n\nOK\n"
        return (1 if key in self.fail else 0), f"{key} out"

    def test_dirs(self):
        return self.dirs


def _call(runner, module):
    return next((a, e) for a, e in runner.calls if a[1] == "-m" and a[2] == module)


def _modules(runner):
    return [a[2] if a[1] == "-m" else a[0] for a, _ in runner.calls]


class Flow(unittest.TestCase):
    def test_order_and_assets_skipped(self):
        r = FakeRunner()
        results = run_precheck(r, "b" * 40)
        self.assertEqual([x.name for x in results], ["구조", "비밀", "테스트", "문서", "증빙"])
        self.assertTrue(all(x.ok for x in results))
        folders = [a[a.index("-s") + 1] for a, _ in r.calls if "-s" in a]
        self.assertEqual(sorted(folders), ["tests/contract", "tests/tools"])
        self.assertEqual(results[2].tests, 6)
        evidence = _call(r, "tools.check_evidence")[0]
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
        args, env = _call(r, "tools.check_evidence")
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


class Quick(unittest.TestCase):
    """빠른 검사(--quick): 고른 테스트 모듈만, 통과 기록 없음, PR에 못 붙이는 요약 줄. 전체 검사는 그대로."""

    class R(FakeRunner):
        def __init__(self, changed):
            super().__init__(dirs=("assets", "contract", "engine", "tools"))
            self.changed = changed
            self.recorded = False

        def merge_base(self, ref, other="HEAD"):
            return "c" * 40

        def resolve(self, ref):
            return "a" * 40

        def tree_clean(self):
            return True

        def git_common_dir(self):
            return None

        def changed_files(self, base):
            return self.changed

        def python_sources(self):
            return {"engine/__init__.py": "", "engine/core.py": "",
                    "tests/engine/test_core.py": "import engine.core\n",
                    "tests/tools/test_x.py": "", "tests/contract/test_y.py": ""}

    def _main(self, args, runner):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(precheck, "record_pass") as rec:
            rc = precheck.main(args, runner=runner)
        return rc, buf.getvalue().rstrip().splitlines(), rec

    def _folders(self, r):
        return sorted(a[a.index("-s") + 1] for a, _ in r.calls if "-s" in a)

    def _modules_run(self, r):
        return sorted(m for a, _ in r.calls if a[1:3] == ["-m", "unittest"] and "discover" not in a for m in a[3:])

    def test_summary_quick_line(self):
        rc, line = summarize([StepResult("테스트", True, tests=4)], "abc1234", 9.2, "tests/engine")
        self.assertEqual((rc, line), (0, "precheck(빠른): 통과 (테스트, 테스트 4개, base abc1234, 대상 tests/engine, 9초)"))
        rc, line = summarize([StepResult("테스트", False)], "abc1234", 3, "tests/engine")
        self.assertTrue(line.startswith("precheck(빠른): 실패 (테스트; base abc1234, 대상 tests/engine"))

    def test_quick_runs_selected_modules_and_records_nothing(self):
        r = self.R(["engine/core.py"])
        rc, lines, rec = self._main(["--quick"], r)
        self.assertEqual(rc, 0)
        self.assertEqual(self._folders(r), [])
        self.assertEqual(self._modules_run(r), ["tests.engine.test_core"])
        self.assertTrue(lines[-1].startswith("precheck(빠른): 통과"))
        self.assertIn("대상 tests/engine 1개", lines[-1])
        rec.assert_not_called()
        self.assertIn("tools.check_evidence", _modules(r))  # 문서·증빙은 그대로

    def test_quick_falls_back_to_full(self):
        r = self.R(["content/bundle_v1.json"])
        rc, lines, rec = self._main(["--quick"], r)
        self.assertEqual(self._folders(r), ["tests/contract", "tests/engine", "tests/tools"])  # assets는 기본 제외
        self.assertIn("대상 전체(코드가 아닌 파일 content/bundle_v1.json)", lines[-1])
        rec.assert_not_called()

    def test_default_runs_selected_and_records(self):
        """11/9 사용자 결정: 기본 검사(PR 직전·훅)도 고른 모듈만 돌리고 통과 기록을 남긴다."""
        r = self.R(["engine/core.py"])
        rc, lines, rec = self._main([], r)
        self.assertEqual(rc, 0)
        self.assertEqual(self._folders(r), [])
        self.assertEqual(self._modules_run(r), ["tests.engine.test_core"])
        self.assertEqual(lines[-1], "precheck: 통과 (구조·비밀·테스트·문서·증빙, 테스트 3개(선택: tests/engine 1개), "
                                    "base ccccccc, 0초)")
        rec.assert_called_once()
        self.assertTrue(rec.call_args.kwargs["selected"])

    def test_default_falls_back_to_full_without_selection_label(self):
        for changed in ([], ["content/bundle_v1.json"]):
            r = self.R(changed)
            rc, lines, rec = self._main([], r)
            self.assertEqual(self._folders(r), ["tests/contract", "tests/engine", "tests/tools"], changed)
            self.assertNotIn("선택", lines[-1])
            self.assertFalse(rec.call_args.kwargs["selected"])

    def test_with_assets_always_runs_asset_folder(self):
        r = self.R(["engine/core.py"])
        rc, lines, rec = self._main(["--with-assets"], r)
        self.assertEqual(self._folders(r), ["tests/assets"])
        self.assertEqual(self._modules_run(r), ["tests.engine.test_core"])
        self.assertIn("선택: tests/assets 전체·tests/engine 1개", lines[-1])

    def test_full_tests_runs_all_folders(self):
        r = self.R(["engine/core.py"])
        rc, lines, rec = self._main(["--full-tests"], r)
        self.assertEqual(self._folders(r), ["tests/contract", "tests/engine", "tests/tools"])
        self.assertIn("[테스트] 대상: 전체(--full-tests)", lines)
        self.assertNotIn("선택", lines[-1])
        self.assertFalse(rec.call_args.kwargs["selected"])

    def test_summary_selected_line(self):
        rc, line = summarize([StepResult("테스트", True, tests=4)], "abc1234", 9.2, selected="tests/engine 1개")
        self.assertEqual(line, "precheck: 통과 (테스트, 테스트 4개(선택: tests/engine 1개), base abc1234, 9초)")
        rc, line = summarize([StepResult("테스트", False)], "abc1234", 3, selected="tests/engine 1개")
        self.assertEqual(line, "precheck: 실패 (테스트; base abc1234, 선택 tests/engine 1개, 3초)")

    def test_quick_rejects_full_options(self):
        import io
        from contextlib import redirect_stderr
        for flag in ("--full-tests", "--all-slow"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                precheck.main(["--quick", flag], runner=self.R([]))

    def test_only_tests_keeps_assets_rule_and_whole_folders(self):
        r = FakeRunner()
        run_precheck(r, "b" * 40, only_tests={"assets": ["tests.assets.test_a"], "tools": ["tests.tools.test_b"],
                                              "contract": None})
        self.assertEqual(self._modules_run(r), ["tests.tools.test_b"])
        self.assertEqual(self._folders(r), ["tests/contract"])

    def test_check_pass_rejects_quick(self):
        import io
        from contextlib import redirect_stderr
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            precheck.main(["--check-pass", "a" * 40, "--quick"], runner=self.R([]))


if __name__ == "__main__":
    unittest.main()


class GitEnvIsolation(unittest.TestCase):
    """훅이 넘긴 GIT_DIR 등이 하위 명령에 새지 않는다(11/5 사고 재발 방지)."""

    def test_clean_env_drops_repository_vars(self):
        env = {"GIT_DIR": "x", "GIT_INDEX_FILE": "y", "GIT_CONFIG_KEY_0": "user.name",
               "GIT_CONFIG_VALUE_0": "t", "GIT_AUTHOR_NAME": "a", "PATH": "p"}
        self.assertEqual(precheck.clean_env(env), {"GIT_AUTHOR_NAME": "a", "PATH": "p"})

    def test_runner_does_not_see_inherited_git_dir(self):
        import os
        import subprocess
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as victim, tempfile.TemporaryDirectory() as work:
            subprocess.run(["git", "init", "-q", victim], check=True)
            old = os.environ.get("GIT_DIR")
            os.environ["GIT_DIR"] = str(Path(victim) / ".git")
            try:
                rc, _ = precheck.SubprocessRunner(Path(work)).run(["git", "init", "-q"])
                self.assertEqual(rc, 0)
                rc, _ = precheck.SubprocessRunner(Path(work)).run(
                    ["git", "config", "user.name", "t"])
                self.assertEqual(rc, 0)
            finally:
                if old is None:
                    os.environ.pop("GIT_DIR")
                else:
                    os.environ["GIT_DIR"] = old
            victim_cfg = (Path(victim) / ".git" / "config").read_text(encoding="utf-8")
            self.assertNotIn("name = t", victim_cfg)
            self.assertTrue((Path(work) / ".git").is_dir())


class Slow(unittest.TestCase):
    """무거운 테스트(11/9): 전체 검사에서도 바뀐 파일과 이어질 때만 돌리고, 뺀 개수를 요약 줄에 적는다."""

    class R(Quick.R):
        def run(self, args, env=None):
            if args[1:3] == ["-m", "tools.run_tests"]:
                self.calls.append((args, env))
                return 0, f"precheck-skip: {args.count('--skip')}\nRan 3 tests in 0.1s\n\nOK\n"
            return super().run(args, env)

    def _main(self, args, changed):
        r = self.R(changed)
        rc, lines, _ = Quick._main(self, args, r)
        skips = {a[a.index("-s") + 1]: [a[i + 1] for i, x in enumerate(a) if x == "--skip"]
                 for a, _ in r.calls if a[1:3] == ["-m", "tools.run_tests"]}
        return rc, lines, skips

    def test_rules(self):
        self.assertEqual(precheck.count_skipped("precheck-skip: 2\nRan 3 tests\nprecheck-skip: 1\n"), 3)
        rc, line = summarize([StepResult("테스트", True, tests=5, slow_skipped=2)], "abc1234", 7)
        self.assertEqual(line, "precheck: 통과 (테스트, 테스트 5개, 무거운 테스트 2개 생략, base abc1234, 7초)")

    def test_unrelated_slow_tests_skipped(self):
        from tools.precheck_scope import SLOW_TESTS
        rc, lines, skips = self._main(["--full-tests"], ["engine/core.py"])
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(skips), ["tests/engine", "tests/tools"])
        self.assertEqual(sum(len(v) for v in skips.values()), len(SLOW_TESTS))
        self.assertIn(f"무거운 테스트 {len(SLOW_TESTS)}개 생략", lines[-1])
        self.assertTrue(lines[-1].startswith("precheck: 통과"))

    def test_all_slow_no_change_or_shared_file_runs_everything(self):
        for args, changed, why in ((["--all-slow"], ["engine/core.py"], None),
                                   (["--full-tests"], [], "바뀐 파일 없음"),
                                   (["--full-tests"], ["tools/precheck.py"], "공용 파일 tools/precheck.py")):
            rc, lines, skips = self._main(args, changed)
            self.assertEqual(skips, {}, changed)
            self.assertNotIn("생략", lines[-1])
            if why:
                self.assertIn(f"[무거운 테스트] 모두 실행({why})", lines)

    def test_check_pass_rejects_all_slow(self):
        import io
        from contextlib import redirect_stderr
        for flag in ("--all-slow", "--full-tests"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                precheck.main(["--check-pass", "a" * 40, flag], runner=FakeRunner())

    def test_selected_run_keeps_slow_tests_of_selected_modules(self):
        rc, lines, skips = self._main([], ["engine/core.py"])  # 고른 실행은 run_tests를 쓰지 않는다
        self.assertEqual(skips, {})
        self.assertNotIn("생략", lines[-1])
