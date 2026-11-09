"""tools/run_tests.py: 지정한 테스트 id를 빼고 폴더 돌리기(precheck 무거운 테스트 생략)."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tools.run_tests import drop_tests, main


class _Case(unittest.TestCase):
    def test_a(self):
        pass

    def test_b(self):
        pass


class Drop(unittest.TestCase):
    def test_drops_named_and_counts_only_found(self):
        loader = unittest.TestLoader()
        suite = unittest.TestSuite([loader.loadTestsFromTestCase(_Case)])
        a = f"{__name__}._Case.test_a"
        out, n = drop_tests(suite, {a, "no.such.Test.test_x"})
        self.assertEqual(n, 1)
        self.assertEqual([t.id() for s in out for t in s], [f"{__name__}._Case.test_b"])


class Cli(unittest.TestCase):
    def _run(self, files, skip):
        with tempfile.TemporaryDirectory() as top:
            pkg = Path(top) / "pkg_rt"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            for name, src in files.items():
                (pkg / name).write_text(src, encoding="utf-8")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = main(["-s", str(pkg), "-t", top, *[a for t in skip for a in ("--skip", t)]])
            import sys
            for m in [m for m in sys.modules if m == "pkg_rt" or m.startswith("pkg_rt.")]:
                del sys.modules[m]
            return rc, err.getvalue()

    SRC = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n    def test_bad(self): self.fail()\n"

    def test_skip_failing_test_passes_and_reports_count(self):
        rc, out = self._run({"test_m.py": self.SRC}, ["pkg_rt.test_m.T.test_bad"])
        self.assertEqual(rc, 0)
        self.assertIn("precheck-skip: 1", out)
        self.assertIn("Ran 1 test", out)

    def test_without_skip_fails(self):
        rc, out = self._run({"test_m.py": self.SRC}, [])
        self.assertEqual(rc, 1)
        self.assertIn("precheck-skip: 0", out)

    def test_no_tests_is_5(self):
        rc, _ = self._run({"test_m.py": "X = 1\n"}, [])
        self.assertEqual(rc, 5)
