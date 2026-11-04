"""계획 대비 실제 소요 시간 요약(tools/hours_report.py) 테스트."""
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.hours_report import (
    ActualHoursFormatError, load_actuals, main, normalize_date, parse_actual_hours, summarize,
)
from tools.tasks_md import parse_task_file

SAMPLE = """# A

## 11/5(목)

### A-05-01 판 생성
- 3h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블

### A-05-02 투표
- 2h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블
- 모듈 문서 일괄 갱신(+0.5h, 공통 ⑦) · 대상: A-05-01, A-05-02 · 코드를 바꿨다면: 없음 · 모듈: [`engine`](../modules/engine.md)

## 11/6(금)

### A-06-01 밤
- 4h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블
"""


class ParseActual(unittest.TestCase):
    def test_number(self):
        self.assertEqual(parse_actual_hours("# A\n- 실제 소요 시간(h): 2.5\n"), 2.5)
        self.assertEqual(parse_actual_hours("- 실제 소요 시간(h): 3\n"), 3.0)

    def test_missing_line_is_none(self):
        self.assertIsNone(parse_actual_hours("# A\n- 커밋: abc\n"))

    def test_non_number_rejected(self):
        for bad in ("약 2", "2h", "2,5", "", "-1"):
            with self.subTest(bad=bad), self.assertRaises(ActualHoursFormatError):
                parse_actual_hours(f"- 실제 소요 시간(h): {bad}\n")

    def test_normalize_date(self):
        self.assertEqual(normalize_date("11/5"), "11-05")
        self.assertEqual(normalize_date("11-05"), "11-05")
        with self.assertRaises(ValueError):
            normalize_date("내일")


class Summarize(unittest.TestCase):
    def setUp(self):
        self.tasks = parse_task_file(SAMPLE, "A", "docs/tasks/A_engine.md")

    def test_planned_includes_doc_and_batch(self):
        rows = summarize(self.tasks, {"A-05-01": 4.0, "A-05-02": None, "A-06-01": None})
        r5 = next(r for r in rows if r.date == "11-05")
        self.assertEqual(r5.planned, 3 + 0.25 + 2 + 0.25 + 0.5)
        self.assertEqual((r5.planned_reported, r5.actual), (3.25, 4.0))
        self.assertAlmostEqual(r5.ratio, 4.0 / 3.25)
        self.assertEqual(r5.missing, ["A-05-02"])

    def test_until_filters_later_days(self):
        rows = summarize(self.tasks, {}, until="11-05")
        self.assertEqual([r.date for r in rows], ["11-05"])
        self.assertIsNone(rows[0].ratio)


class FromFiles(unittest.TestCase):
    def test_cli_reads_evidence_and_flags_bad_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "tasks").mkdir()
            (tmp / "tasks" / "A_engine.md").write_text(SAMPLE, encoding="utf-8")
            ev = tmp / "evidence"
            ev.mkdir()
            (ev / "A-05-01.md").write_text("# A-05-01\n- 실제 소요 시간(h): 4\n", encoding="utf-8")
            (ev / "A-05-02.md").write_text("# A-05-02\n- 실제 소요 시간(h): 두 시간\n", encoding="utf-8")
            values, errors = load_actuals(ev, ["A-05-01", "A-05-02", "A-06-01"])
            self.assertEqual(values, {"A-05-01": 4.0, "A-05-02": None, "A-06-01": None})
            self.assertEqual(list(errors), ["A-05-02"])
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--until", "11/5", "--tasks-dir", str(tmp / "tasks"), "--evidence-dir", str(ev)])
            out = buf.getvalue()
            self.assertEqual(code, 1)
            self.assertIn("| A | 11/5 | 6 | 3.25 | 4 | 1.23 | A-05-02 |", out)
            self.assertIn("형식 오류 A-05-02", out)
            self.assertNotIn("11/6", out)

    def test_later_bad_evidence_does_not_fail_earlier_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "tasks").mkdir()
            (tmp / "tasks" / "A_engine.md").write_text(SAMPLE, encoding="utf-8")
            ev = tmp / "evidence"
            ev.mkdir()
            (ev / "A-05-01.md").write_text("- 실제 소요 시간(h): 3\n", encoding="utf-8")
            (ev / "A-06-01.md").write_text("- 실제 소요 시간(h): 모름\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--until", "11-05", "--tasks-dir", str(tmp / "tasks"), "--evidence-dir", str(ev)])
            self.assertEqual(code, 0)
            self.assertNotIn("형식 오류", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
