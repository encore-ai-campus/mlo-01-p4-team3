"""계획 대비 실제 소요 시간 요약(흐름 × 날짜).

계획 시간은 태스크 문서(`docs/tasks/*.md`)의 태스크 시간 + 문서화 + 모듈 문서 일괄 갱신이고,
실제 시간은 증빙 문서(`docs/evidence/<ID>.md`)의 `- 실제 소요 시간(h): <숫자>` 줄이다(README 4절).
11/6 09:00 스탠드업에서 P-23(남은 시간 초과 처리)을 정할 때 쓴다.

    python -m tools.hours_report --until 11-05

표준 라이브러리만 쓴다. 파일을 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.tasks_md import UNASSIGNED_FLOWS, Task, load_all

ROOT = Path(__file__).resolve().parents[1]
ACTUAL_LINE_RE = re.compile(r"^-\s*실제 소요 시간\(h\):[ \t]*(.*?)[ \t]*$", re.M)
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")


class ActualHoursFormatError(ValueError):
    """`실제 소요 시간(h)` 값이 숫자가 아니다."""


def parse_actual_hours(text: str) -> float | None:
    """증빙 문서에서 실제 소요 시간을 읽는다. 줄이 없으면 None, 숫자가 아니면 ActualHoursFormatError."""
    m = ACTUAL_LINE_RE.search(text)
    if not m:
        return None
    value = m.group(1)
    if not NUMBER_RE.match(value):
        raise ActualHoursFormatError(f"실제 소요 시간(h)이 숫자가 아님: {value!r}")
    return float(value)


def normalize_date(value: str) -> str:
    """`11-05`, `11/5`, `1105` → `11-05`."""
    m = re.fullmatch(r"(\d{1,2})[-/]?(\d{1,2})", value.strip())
    if not m:
        raise ValueError(f"날짜 형식이 아님: {value!r}")
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def planned_hours(t: Task) -> float:
    return t.hours + t.doc_hours + t.batch_hours


@dataclass
class Row:
    """흐름 하나의 하루 요약."""

    flow: str
    date: str
    planned: float = 0.0  # 그날 태스크 전부의 계획 시간
    planned_reported: float = 0.0  # 실제 시간을 적은 태스크들의 계획 시간
    actual: float = 0.0  # 적힌 실제 시간 합
    missing: list[str] = field(default_factory=list)  # 증빙이 없거나 실제 시간 줄이 없는 태스크

    @property
    def ratio(self) -> float | None:
        """실제 / 계획(적은 태스크만). 적은 태스크가 없으면 None."""
        return self.actual / self.planned_reported if self.planned_reported else None


def summarize(tasks: list[Task], actuals: dict[str, float | None], until: str | None = None) -> list[Row]:
    """태스크와 실제 시간(ID → 시간, 없으면 None)으로 흐름 × 날짜 요약을 만든다. 통합(INT)은 뺀다."""
    rows: dict[tuple[str, str], Row] = {}
    for t in tasks:
        if t.kind != "task" or t.date is None or (until and t.date > until):
            continue
        row = rows.setdefault((t.flow, t.date), Row(t.flow, t.date))
        plan = planned_hours(t)
        row.planned += plan
        actual = actuals.get(t.task_id)
        if actual is None:
            row.missing.append(t.task_id)
        else:
            row.planned_reported += plan
            row.actual += actual
    return sorted(rows.values(), key=lambda r: (r.flow in UNASSIGNED_FLOWS, r.flow, r.date))


def load_actuals(evidence_dir: Path, task_ids: list[str]) -> tuple[dict[str, float | None], dict[str, str]]:
    """증빙 폴더에서 태스크별 실제 시간을 읽는다. (값, 형식 오류 목록)을 돌려준다."""
    values: dict[str, float | None] = {}
    errors: dict[str, str] = {}
    for tid in task_ids:
        path = evidence_dir / f"{tid}.md"
        if not path.exists():
            values[tid] = None
            continue
        try:
            values[tid] = parse_actual_hours(path.read_text(encoding="utf-8"))
        except ActualHoursFormatError as e:
            values[tid] = None
            errors[tid] = str(e)
    return values, errors


def render(rows: list[Row]) -> str:
    def f(x: float) -> str:
        return f"{x:g}"

    lines = ["| 흐름 | 날짜 | 계획(전체) | 계획(기록한 태스크) | 실제 | 실제/계획 | 기록 없음 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        ratio = "-" if r.ratio is None else f"{r.ratio:.2f}"
        name = f"{r.flow}(발표 트랙)" if r.flow in UNASSIGNED_FLOWS else r.flow
        lines.append(f"| {name} | {r.date[:2].lstrip('0')}/{r.date[3:].lstrip('0')} | {f(r.planned)} | "
                     f"{f(r.planned_reported)} | {f(r.actual)} | {ratio} | {', '.join(r.missing) or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="계획 대비 실제 소요 시간 요약(흐름 × 날짜)")
    p.add_argument("--until", help="이 날짜까지(예: 11-05 또는 11/5)")
    p.add_argument("--tasks-dir", default=str(ROOT / "docs" / "tasks"))
    p.add_argument("--evidence-dir", default=str(ROOT / "docs" / "evidence"))
    args = p.parse_args(argv)
    until = normalize_date(args.until) if args.until else None
    tasks, _ = load_all(Path(args.tasks_dir))
    # 요약 범위(--until) 안의 태스크만 읽는다. 뒤 날짜 증빙의 형식 오류가 앞 날짜 요약을 막지 않게 한다.
    in_range = [t.task_id for t in tasks if t.kind == "task" and t.date and (until is None or t.date <= until)]
    actuals, errors = load_actuals(Path(args.evidence_dir), in_range)
    sys.stdout.write(render(summarize(tasks, actuals, until)))
    for tid, msg in sorted(errors.items()):
        sys.stdout.write(f"형식 오류 {tid}: {msg}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
