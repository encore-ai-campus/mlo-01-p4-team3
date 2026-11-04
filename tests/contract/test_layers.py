"""의존 방향 검사(README): engine은 표준 라이브러리만, server/app은 Django 금지."""
from __future__ import annotations

import ast
import sys
import unittest

from ._util import ROOT

STDLIB = set(sys.stdlib_module_names)


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name.split(".")[0], 0
        elif isinstance(node, ast.ImportFrom):
            yield (node.module or "").split(".")[0], node.level


class Layers(unittest.TestCase):
    def check(self, folder, allowed):
        for f in (ROOT / folder).rglob("*.py"):
            for mod, level in imports(f):
                if level:   # 상대 import는 같은 패키지 안
                    continue
                self.assertTrue(mod in STDLIB or mod in allowed, f"{f.relative_to(ROOT)}: {mod}")

    def test_engine_stdlib_only(self):
        self.check("engine", {"engine"})

    def test_contracts_stdlib_only(self):
        self.check("contracts", {"contracts"})

    def test_app_no_django(self):
        self.check("server/app", {"engine", "server", "ads"})

    def test_ads_domain_stdlib_only(self):
        self.check("ads/domain", {"ads"})

    def test_engine_does_not_import_ads(self):
        for f in (ROOT / "engine").rglob("*.py"):
            self.assertNotIn("ads", {m for m, _ in imports(f)}, str(f))

    def test_tools_no_third_party(self):
        self.check("tools", {"engine", "contracts", "tools", "ads"})


if __name__ == "__main__":
    unittest.main()
