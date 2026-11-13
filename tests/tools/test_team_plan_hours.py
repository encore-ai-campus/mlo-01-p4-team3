"""team_plan.md "전체 시간표"가 태스크 문서의 시간 계산(hours_table)과 같은지 검사한다.

태스크 시간을 바꾸고 표를 고치지 않으면 이 테스트가 실패한다.
"""
import unittest
from pathlib import Path

from tools.tasks_md import hours_table, load_all

ROOT = Path(__file__).resolve().parents[2]
TEAM_PLAN = ROOT / "docs" / "team_plan.md"
HEADING = "### 전체 시간표"


def parse_hours_section(text: str) -> tuple[list[str], dict[str, list[float]]]:
    """'전체 시간표' 절의 첫 표를 (날짜 열 목록, 행 이름 → 값 목록(마지막은 합계))로 읽는다."""
    start = text.index(HEADING)
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("|"):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif rows:
            break
    header, body = rows[0], rows[2:]
    dates = header[1:-1]
    table = {r[0]: [float(x) for x in r[1:]] for r in body}
    return dates, table


def to_key(label: str) -> str:
    """'11/5' → '11-05'(hours_table 날짜 키)."""
    month, day = label.split("/")
    return f"{int(month):02d}-{int(day):02d}"


class TeamPlanHoursTable(unittest.TestCase):
    def test_matches_task_documents(self):
        dates, table = parse_hours_section(TEAM_PLAN.read_text(encoding="utf-8"))
        computed = hours_table(load_all(ROOT / "docs" / "tasks")[0])
        keys = [to_key(d) for d in dates]
        flows = [f for f in table if f != "전체"]
        self.assertEqual(sorted(flows), sorted(computed), "표의 흐름 행이 태스크 문서의 흐름과 다르다")
        for flow in flows:
            want = [round(computed[flow].get(k, 0.0), 2) for k in keys]
            self.assertEqual(table[flow][:-1], want, f"{flow} 일별 시간")
            self.assertAlmostEqual(table[flow][-1], sum(want), places=2, msg=f"{flow} 합계")
        for i, k in enumerate(keys):
            col = sum(computed[f].get(k, 0.0) for f in computed)
            self.assertAlmostEqual(table["전체"][i], col, places=2, msg=f"전체 {dates[i]}")
        grand = sum(sum(v.values()) for v in computed.values())
        self.assertAlmostEqual(table["전체"][-1], grand, places=2, msg="전체 합계")
        outside = {k for v in computed.values() for k in v} - set(keys)
        self.assertFalse(outside, f"표에 없는 날짜: {sorted(outside)}")

    def test_parser_reads_table(self):
        text = (f"{HEADING}\n\n설명\n\n| 흐름 | 11/5 | 11/6 | 합계 |\n|---|---|---|---|\n"
                "| A | 1 | 2.5 | 3.5 |\n| 전체 | 1 | 2.5 | 3.5 |\n\n다음\n")
        dates, table = parse_hours_section(text)
        self.assertEqual(dates, ["11/5", "11/6"])
        self.assertEqual(table["A"], [1.0, 2.5, 3.5])
        self.assertEqual(to_key("11/5"), "11-05")


if __name__ == "__main__":
    unittest.main()
