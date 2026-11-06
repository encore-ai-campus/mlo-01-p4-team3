"""LLM MVP 제출 묶음(tools/build_llm_bundle.py)과 MVP 노트북(llm/colab/paldo_llm_mvp.ipynb) 검사. E-07-06 1·2번."""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import build_llm_bundle as bundle
from tools.check_secrets import scan_text
from tools.gitrepo import ROOT

NOTEBOOK = ROOT / "llm" / "colab" / "paldo_llm_mvp.ipynb"
EXPECTED = ["interrogation_eval.json", "llm/mvp_metrics.py", "llm/prompt.py", "llm/validator.py"]


def copy_members(dst: Path) -> None:
    """MEMBERS 원본을 임시 저장소 형태로 복사한다(가짜 저장소 루트)."""
    for rel in bundle.MEMBERS.values():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / rel).read_bytes())


class Bundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmpdir = Path(self.tmp.name)

    def test_members_only(self):
        path = bundle.build(self.tmpdir / "out")
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(zf.namelist(), EXPECTED)
            self.assertNotIn("llm/__init__.py", zf.namelist())
            for arc, rel in bundle.MEMBERS.items():
                self.assertEqual(zf.read(arc), (ROOT / rel).read_bytes(), arc)   # 바이트 그대로

    def test_deterministic(self):
        a = bundle.build(self.tmpdir / "a")
        b = bundle.build(self.tmpdir / "b")
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_fixed_metadata(self):
        with zipfile.ZipFile(bundle.build(self.tmpdir / "out")) as zf:
            for info in zf.infolist():
                self.assertEqual(info.date_time, bundle.FIXED_TIME)
                self.assertEqual(info.external_attr >> 16, bundle.FILE_MODE)
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)

    def test_no_secrets_in_zip(self):
        with zipfile.ZipFile(bundle.build(self.tmpdir / "out")) as zf:
            for arc in zf.namelist():
                self.assertEqual(scan_text(bundle.MEMBERS[arc], zf.read(arc).decode("utf-8")), [], arc)

    def test_secret_blocks_zip(self):
        fake = self.tmpdir / "repo"
        copy_members(fake)
        # 가짜 비밀값은 검사 제외 폴더(tests/fixtures/secrets/)의 탐지 테스트용 파일에서 가져온다
        planted = (ROOT / "tests" / "fixtures" / "secrets" / "key_values.py").read_text(encoding="utf-8")
        with open(fake / "llm" / "prompt.py", "a", encoding="utf-8") as f:
            f.write("\n" + planted)
        out = self.tmpdir / "out"
        with self.assertRaises(bundle.BundleError) as ctx:
            bundle.build(out, root=fake)
        self.assertIn("llm/prompt.py", str(ctx.exception))
        self.assertFalse((out / bundle.ZIP_NAME).exists())

    def test_missing_file_fails(self):
        fake = self.tmpdir / "repo"
        copy_members(fake)
        (fake / "llm" / "validator.py").unlink()
        with self.assertRaises(bundle.BundleError):
            bundle.build(self.tmpdir / "out", root=fake)

    def test_unzipped_imports_as_namespace_package(self):
        path = bundle.build(self.tmpdir / "out")
        site = self.tmpdir / "site"
        with zipfile.ZipFile(path) as zf:
            zf.extractall(site)
        code = ("import json\nfrom llm import mvp_metrics, prompt, validator\n"
                "print(json.dumps({'file': mvp_metrics.__file__, 'gen': prompt.GEN_SETTINGS,"
                " 'has_run_eval': callable(mvp_metrics.run_eval)}))")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(site)
        out = subprocess.run([sys.executable, "-c", code], cwd=site, env=env, capture_output=True,
                             text=True, encoding="utf-8", check=True)
        data = json.loads(out.stdout)
        self.assertTrue(Path(data["file"]).resolve().is_relative_to(site.resolve()), data["file"])
        self.assertTrue(data["has_run_eval"])
        evalset = json.loads((site / "interrogation_eval.json").read_text(encoding="utf-8"))
        self.assertEqual(len(evalset["cases"]), 100)

    def test_cli(self):
        self.assertEqual(bundle.main(["--out", str(self.tmpdir / "cli")]), 0)
        self.assertTrue((self.tmpdir / "cli" / bundle.ZIP_NAME).is_file())


DRIVE_ID_RE = re.compile(r"""id=['"]?[A-Za-z0-9_-]{10,}""")


class Notebook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        cls.sources = ["".join(c["source"]) if isinstance(c["source"], list) else c["source"]
                       for c in cls.nb["cells"]]
        cls.code = [c for c in cls.nb["cells"] if c["cell_type"] == "code"]

    def test_outputs_cleared(self):
        for i, cell in enumerate(self.code):
            self.assertEqual(cell.get("outputs"), [], f"코드 칸 {i}")
            self.assertIsNone(cell.get("execution_count"), f"코드 칸 {i}")

    def test_file_ids_empty(self):
        src = "\n".join(self.sources)
        for name in ("MODEL_FILE_ID", "ZIP_FILE_ID", "LOCAL_MODEL_PATH", "LOCAL_ZIP_PATH"):
            m = re.search(rf"^{name}\s*=\s*(.+?)\s*(?:#.*)?$", src, re.M)
            self.assertIsNotNone(m, name)
            self.assertIn(m.group(1), ('""', "''"), name)

    def test_inputs_in_first_code_cell(self):
        first = "".join(self.code[0]["source"])
        for name in ("MODEL_FILE_ID", "ZIP_FILE_ID", "CONSISTENCY_N", "CONSISTENCY_K", "LOCAL_MODEL_PATH",
                     "LOCAL_ZIP_PATH"):
            self.assertRegex(first, rf"(?m)^{name}\s*=", name)
        self.assertRegex(first, r"(?m)^CONSISTENCY_N\s*=\s*20\b")
        self.assertRegex(first, r"(?m)^CONSISTENCY_K\s*=\s*5\b")

    def test_no_secrets_in_sources(self):
        # JSON 이스케이프 때문에 원문 검사에서 놓치는 경우를 막으려고 파싱한 셀 소스를 검사한다
        self.assertEqual(scan_text("llm/colab/paldo_llm_mvp.ipynb", "\n".join(self.sources)), [])

    def test_no_drive_links(self):
        src = "\n".join(self.sources)
        self.assertNotIn("drive.google.com", src)
        self.assertIsNone(DRIVE_ID_RE.search(src))

    def test_uses_mvp_metrics(self):
        src = "\n".join("".join(c["source"]) for c in self.code)   # 설명(마크다운)은 빼고 코드 칸만
        for fn in ("run_eval", "success_metrics", "latency_metrics", "run_consistency"):
            self.assertIn(f"mvp_metrics.{fn}(", src, fn)
        # 지표 계산을 노트북 안에서 다시 정의하지 않는다
        self.assertNotRegex(src, r"(?m)^\s*def\s+(percentile|similarity|consistency_\w+|success_metrics|latency_metrics)\b")
        self.assertNotIn("SequenceMatcher", src)


if __name__ == "__main__":
    unittest.main()
