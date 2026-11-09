"""tools/precheck_scope.py: 빠른 검사 대상 테스트 폴더 고르기(가짜 저장소 소스)."""
from __future__ import annotations

import unittest

from tools.precheck_scope import import_names, module_name, select_test_dirs

DIRS = ["assets", "contract", "engine", "sim", "tools"]
SOURCES = {
    "engine/__init__.py": "",
    "engine/domain/__init__.py": "",
    "engine/domain/model.py": "X = 1\n",
    "engine/domain/rules.py": "from .model import X\n",
    "tools/__init__.py": "",
    "tools/report.py": "from engine.domain import rules\n",
    "tools/lonely.py": "print('only run by hand')\n",
    "tools/cli_only.py": "def main(): pass\n",
    "web_helper/__init__.py": "",
    "tests/__init__.py": "",
    "tests/engine/__init__.py": "",
    "tests/engine/test_model.py": "from engine.domain.model import X\n",
    "tests/sim/__init__.py": "",
    "tests/sim/test_rules.py": "import engine.domain.rules\n",
    "tests/tools/__init__.py": "",
    "tests/tools/test_report.py": "from tools import report\n",
    "tests/tools/test_cli.py": "CMD = ['python', '-m', 'tools.cli_only']\n",
    "tests/contract/__init__.py": "",
    "tests/contract/test_layers.py": "ROOT = 'README.md'\n",
    "tests/assets/__init__.py": "",
    "tests/assets/test_spec.py": "SPEC = 'docs/assets/spec.md'\n",
}


class Names(unittest.TestCase):
    def test_module_name(self):
        self.assertEqual(module_name("engine/domain/model.py"), "engine.domain.model")
        self.assertEqual(module_name("engine/domain/__init__.py"), "engine.domain")
        self.assertIsNone(module_name("web/templates/a.html"))

    def test_import_names_relative_and_strings(self):
        self.assertEqual(import_names("from .model import X\n", "engine.domain.rules"),
                         {"engine.domain.model", "engine.domain.model.X"})
        self.assertIn("engine.domain.x", import_names("from . import x\n", "engine.domain", is_package=True))
        self.assertIn("tools.cli_only", import_names("A = ['-m', 'tools.cli_only']\n", "t"))
        self.assertIn("tools.run", import_names("P = 'tools/run.py'\n", "t"))
        self.assertEqual(import_names("def (:\n", "t"), set())


class Select(unittest.TestCase):
    def pick(self, changed):
        return select_test_dirs(changed, SOURCES, DIRS)

    def test_engine_change_reaches_direct_and_indirect_tests(self):
        dirs, _ = self.pick(["engine/domain/model.py"])
        self.assertEqual(dirs, ["engine", "sim", "tools"])  # rules→report 간접 사용까지

    def test_module_called_by_subprocess_string(self):
        self.assertEqual(self.pick(["tools/cli_only.py"])[0], ["tools"])

    def test_package_init_change_reaches_submodule_users(self):
        self.assertEqual(self.pick(["engine/domain/__init__.py"])[0], ["engine", "sim", "tools"])

    def test_test_file_change_only_its_folder(self):
        self.assertEqual(self.pick(["tests/engine/test_model.py"])[0], ["engine"])

    def test_docs_change_picks_tests_that_read_docs(self):
        self.assertEqual(self.pick(["docs/troubles/E.md"])[0], ["assets", "contract"])

    def test_full_for_shared_unknown_or_unused(self):
        for changed in (["tools/precheck.py"], ["tools/precheck_scope.py"], ["tests/fixtures/a.json"],
                        ["tests/__init__.py"], ["tests/test_top.py"], ["requirements/base.txt"],
                        ["pyproject.toml"], ["web/templates/a.html"], ["content/bundle_v1.json"],
                        ["tools/lonely.py"], ["engine/domain/model.py", "web/a.js"]):
            dirs, why = self.pick(changed)
            self.assertIsNone(dirs, changed)
            self.assertTrue(why, changed)

    def test_deleted_module_still_reaches_importers(self):
        sources = {k: v for k, v in SOURCES.items() if k != "engine/domain/model.py"}
        self.assertEqual(select_test_dirs(["engine/domain/model.py"], sources, DIRS)[0], ["engine", "sim", "tools"])

    def test_nothing_changed(self):
        self.assertEqual(self.pick([]), ([], "바뀐 파일 없음"))


if __name__ == "__main__":
    unittest.main()
