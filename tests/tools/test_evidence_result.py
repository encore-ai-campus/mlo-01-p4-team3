"""결과 JSON 도우미(tools/evidence_result.py) 테스트. README 4절 "결과 JSON 형식"."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.evidence_result import ResultFormatError, build_result, result_path, write_result

GOOD = {"api_p95": {"value": 412.3, "unit": "ms"}, "error_count": {"value": 0, "unit": "건"}}


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def temp_repo() -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    git(tmp.name, "init", "-q")
    git(tmp.name, "config", "user.email", "t@example.invalid")
    git(tmp.name, "config", "user.name", "t")
    (Path(tmp.name) / "a.txt").write_text("a\n", encoding="utf-8")
    git(tmp.name, "add", "a.txt")
    git(tmp.name, "commit", "-q", "-m", "init")
    return tmp


class Build(unittest.TestCase):
    def test_shape(self):
        data = build_result("B-06-02", "api", GOOD, "python -m tools.perf.api_latency --seed 1", "abc1234", "2026-11-06T09:12:00Z")
        self.assertEqual(list(data), ["task", "commit", "generated_at", "source", "metrics"])
        self.assertEqual(data["metrics"]["api_p95"], {"value": 412.3, "unit": "ms"})

    def test_rejects_bad_inputs(self):
        cases = [
            ("bad-id", "api", GOOD),
            ("B-06-02", "a/b", GOOD),
            ("B-06-02", "api", {}),
            ("B-06-02", "api", {"API_p95": {"value": 1, "unit": "ms"}}),
            ("B-06-02", "api", {"p95": {"value": True, "unit": "ms"}}),
            ("B-06-02", "api", {"p95": {"value": "412", "unit": "ms"}}),
            ("B-06-02", "api", {"p95": {"value": float("nan"), "unit": "ms"}}),
            ("B-06-02", "api", {"p95": {"value": 1}}),
            ("B-06-02", "api", {"p95": {"value": 1, "unit": None}}),
        ]
        for task, name, metrics in cases:
            with self.assertRaises(ResultFormatError, msg=(task, name, metrics)):
                build_result(task, name, metrics, "cmd", "abc1234", "t")

    def test_empty_source_rejected(self):
        with self.assertRaises(ResultFormatError):
            build_result("B-06-02", "api", GOOD, " ", "abc1234", "t")

    def test_unitless_value(self):
        data = build_result("D-07-02", "ece", {"ece": {"value": 0.04, "unit": ""}}, "cmd", "abc1234", "t")
        self.assertEqual(data["metrics"]["ece"]["unit"], "")


class Write(unittest.TestCase):
    def test_writes_file_with_head_commit_and_utc_time(self):
        with temp_repo() as root:
            path = write_result("B-06-02", "api", GOOD, "python -m tools.perf.api_latency --seed 1", root=root)
            self.assertEqual(path, Path(root) / result_path("B-06-02", "api"))
            self.assertEqual(result_path("B-06-02", "api"), "docs/evidence/perf/B-06-02/api.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()
            self.assertEqual(data["commit"], head)
            self.assertRegex(data["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertEqual(data["task"], "B-06-02")

    def test_invalid_metrics_write_nothing(self):
        with temp_repo() as root:
            with self.assertRaises(ResultFormatError):
                write_result("B-06-02", "api", {"p95": {"value": "x", "unit": "ms"}}, "cmd", root=root)
            self.assertFalse((Path(root) / "docs").exists())


if __name__ == "__main__":
    unittest.main()
