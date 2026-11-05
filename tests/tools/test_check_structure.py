"""디렉터리 트리 검사(tools/check_structure.py) 테스트. 규칙은 가짜 경로 목록으로, 연결은 임시 git 저장소로 본다."""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import check_structure as cs
from tools.gitrepo import GitRepo

ROOT = Path(__file__).resolve().parents[2]

DOC = """# 구조

## 6. 허용 폴더(검사 기준)

설명 # 이건 본문

```
engine/
engine/domain/        # 도메인
docs/**
```

## 7. 다음 절
"""


class ParsePatterns(unittest.TestCase):
    def test_reads_fenced_block_and_strips_comments(self):
        self.assertEqual(cs.parse_patterns(DOC), ["engine/", "engine/domain/", "docs/**"])

    def test_missing_section(self):
        with self.assertRaises(cs.PatternError):
            cs.parse_patterns("# 구조\n\n```\nengine/\n```\n")

    def test_section_without_fence(self):
        with self.assertRaises(cs.PatternError):
            cs.parse_patterns("## 허용 폴더(검사 기준)\n\n본문\n\n## 다음\n\n```\nx/\n```\n")

    def test_bad_pattern(self):
        for bad in ("engine", "engine/*", "/engine/", "a b/"):
            with self.subTest(bad=bad), self.assertRaises(cs.PatternError):
                cs.parse_patterns(f"## 허용 폴더(검사 기준)\n```\n{bad}\n```\n")


class Matching(unittest.TestCase):
    P = ["engine/", "engine/domain/", "docs/**", "server/accounts/**"]

    def test_exact_pattern_allows_only_that_folder(self):
        self.assertTrue(cs.is_allowed("engine", self.P))
        self.assertTrue(cs.is_allowed("engine/domain", self.P))
        self.assertFalse(cs.is_allowed("engine/domain/extra", self.P))
        self.assertFalse(cs.is_allowed("engine/sim", self.P))

    def test_double_star_allows_folder_and_below(self):
        for d in ("docs", "docs/a", "docs/a/b/c", "server/accounts/migrations"):
            self.assertTrue(cs.is_allowed(d, self.P), d)
        self.assertFalse(cs.is_allowed("docsx", self.P))
        self.assertFalse(cs.is_allowed("server", self.P))  # 상위는 따로 적어야 한다

    def test_root_always_allowed(self):
        self.assertTrue(cs.is_allowed("", []))

    def test_dirs_of_includes_ancestors(self):
        self.assertEqual(cs.dirs_of(["README.md", "a/b/c.py", "a/d.py"]), {"a", "a/b"})

    def test_check_dirs_reports_top_bad_folder_with_hint(self):
        errors = cs.check_dirs(cs.dirs_of(["engine/sim/x/y.py", "engine/domain/a.py", "misc/z.txt"]), self.P)
        self.assertEqual(len(errors), 2, errors)  # engine/sim/x는 engine/sim에 묶인다
        self.assertIn("engine/sim/", errors[0])
        self.assertIn("`engine/`", errors[0])
        self.assertIn("misc/", errors[1])
        self.assertIn("루트에 새 폴더", errors[1])

    def test_nearest_pattern_prefers_deepest_overlap(self):
        self.assertEqual(cs.nearest_pattern("engine/domain/extra", self.P), "engine/domain/")
        self.assertIsNone(cs.nearest_pattern("misc", self.P))


class FakeRepo:
    def __init__(self, files, doc=DOC, changed=(), existed=()):
        self.files, self.doc, self.changed, self.existed = list(files), doc, list(changed), set(existed)

    def read_text(self, path):
        return self.doc if path == cs.CONTRACT else None

    def tracked_files(self):
        return self.files

    def changed_files(self, base):
        return self.changed

    def dir_existed(self, base, d):
        return d in self.existed


class Application(unittest.TestCase):
    def test_check_all(self):
        self.assertEqual(cs.check_all(FakeRepo(["engine/domain/a.py", "docs/x/y.md"])), [])
        self.assertEqual(len(cs.check_all(FakeRepo(["engine/bad/a.py"]))), 1)

    def test_check_diff_ignores_folders_that_existed_at_base(self):
        repo = FakeRepo([], changed=[("A", "legacy/new.py"), ("A", "engine/sim/a.py"), ("M", "other/x.py")],
                        existed={"legacy"})
        errors = cs.check_diff("main", repo)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("engine/sim/", errors[0])

    def test_unreadable_contract_fails_main(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cs.main(["--all"], repo=FakeRepo(["a/b.py"], doc="# 없음\n"))
        self.assertEqual(code, 1)
        self.assertIn("허용 패턴을 읽지 못함", out.getvalue())


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class GitIntegration(unittest.TestCase):
    """임시 git 저장소: --diff는 새 폴더만, 커밋 전·추적 안 된 파일도 본다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        self.write(cs.CONTRACT, DOC)
        self.write("engine/domain/a.py", "")
        self.write(".gitignore", "out/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "checkout", "-q", "-b", "pr")
        self._log = os.environ.get("PALDO_LOG_DIR")
        os.environ["PALDO_LOG_DIR"] = str(self.root / "out" / "logs")

    def tearDown(self):
        if self._log is None:
            os.environ.pop("PALDO_LOG_DIR", None)
        else:
            os.environ["PALDO_LOG_DIR"] = self._log
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cs.main(list(argv), repo=GitRepo(self.root))
        return code, out.getvalue()

    def test_untracked_new_folder_fails_diff(self):
        self.write("engine/scratch/x.py", "")
        code, out = self.run_main("--diff", "main")
        self.assertEqual(code, 1, out)
        self.assertIn("engine/scratch/", out)

    def test_committed_allowed_folder_passes(self):
        self.write("docs/new/x.md", "")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "ok")
        self.assertEqual(self.run_main("--diff", "main")[0], 0)
        self.assertEqual(self.run_main("--all")[0], 0)

    def test_bad_base_fails(self):
        self.assertEqual(self.run_main("--diff", "no-such-ref")[0], 1)


class RealRepository(unittest.TestCase):
    """실제 저장소의 추적 파일이 structure.md 6절을 지킨다."""

    def test_all_passes(self):
        self.assertEqual(cs.check_all(GitRepo(ROOT)), [])

    def test_contract_patterns_cover_tree_roots(self):
        patterns = cs.parse_patterns((ROOT / cs.CONTRACT).read_text(encoding="utf-8"))
        for top in ("engine/", "server/", "ads/", "intent/", "ml/", "llm/", "pipeline/", "ops/",
                    "content/**", "contracts/", "fixtures/**", "tools/", "tests/**", "docs/**", "requirements/"):
            self.assertIn(top, patterns)
        self.assertNotIn("out/", patterns)  # 실행 산출물은 커밋 제외


if __name__ == "__main__":
    unittest.main()
