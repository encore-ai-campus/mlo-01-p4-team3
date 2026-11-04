"""태스크 마크다운(`docs/tasks/*.md`) 파서.

태스크 정의의 원본은 저장소의 마크다운이다(`docs/tasks/README.md` 11절).
이 모듈은 그 마크다운을 읽기만 한다. GitHub 호출이나 파일 쓰기는 하지 않는다
(`tools/tasks_to_issues.py`가 이 결과를 받아 이슈를 만든다).

표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 한글 조사가 바로 붙는 경우("D-10-01의")도 잡도록 \b 대신 앞뒤 문자를 직접 본다.
# 흐름 A~E, 발표 트랙 S(11/5 PM 결정 ②안. 결정 대기 P-xx와 겹치지 않게 S), 통합 INT
TASK_ID_RE = re.compile(r"(?<![A-Za-z0-9-])(?:[A-ES]-\d{2}-\d{2}|INT-\d{2})(?!\d)")
DECISION_ID_RE = re.compile(r"(?<![A-Za-z0-9-])P-\d{2}(?!\d)")
_TABLE_NUM_RE = re.compile(r"^\|\s*(\d+)\s*\|")
_HEADING_TASK_RE = re.compile(r"^###\s+((?:[A-ES]-\d{2}-\d{2})|(?:INT-\d{2}))\s+(.*)$")
_DATE_HEADING_RE = re.compile(r"^##\s+(\d{1,2})/(\d{1,2})\(")
_INT_DATE_RE = re.compile(r"\((\d{1,2})/(\d{1,2})\)")
_HOURS_RE = re.compile(r"^-\s+(\d+(?:\.\d+)?)h\b")
_DOC_HOURS_RE = re.compile(r"^-\s+문서화\(\+(\d+(?:\.\d+)?)h")
# 흐름별 하루 마지막 태스크의 "모듈 문서 일괄 갱신" 줄(README 10절)
_BATCH_RE = re.compile(r"^-\s+모듈 문서 일괄 갱신\(\+(\d+(?:\.\d+)?)h")
_MODULE_LINK_RE = re.compile(r"docs/modules/(\w+)\.md|\.\./modules/(\w+)\.md")
_COND_RE = re.compile(r"^\s{2,}(\d+)\.\s")
_COND_LABEL_RE = re.compile(r"(?:완료 조건[^:\n]*|(?:^|·\s*)조건):")
_SAME_AS_RE = re.compile(r"조건은 ((?:[A-ES]-\d{2}-\d{2})(?:\s*\+\s*[A-ES]-\d{2}-\d{2})*)")
FLOW_FILES = {"A": "A_engine.md", "B": "B_web.md", "C": "C_infra.md", "D": "D_ml_ads.md", "E": "E_pm.md",
              "S": "P_presentation.md"}
# 하루 태스크 한도(공통 1.5h 제외). E는 P-22(11/5 PM 결정)로 근무 10.5h = 태스크 9h + 공통 1.5h.
TASK_LIMITS = {"A": 7.5, "B": 7.5, "C": 7.5, "D": 7.5, "E": 9.0}
# 담당이 정해지지 않은 트랙: 흐름별 한도 계산에서 빼고 시간표에 별도 줄로만 보인다(발표 트랙, P-24).
UNASSIGNED_FLOWS = {"S"}


@dataclass
class Task:
    """태스크 하나. `body`는 제목 줄을 뺀 원문 마크다운이다."""

    task_id: str
    title: str
    flow: str  # A~E, 발표 트랙 "S", 통합은 "INT"
    date: str | None  # "11-05" 형식. 날짜를 알 수 없으면 None
    hours: float
    doc_hours: float
    body: str
    source: str  # 상대 경로(예: docs/tasks/A_engine.md)
    deps: list[str] = field(default_factory=list)  # 선행 태스크 ID
    decisions: list[str] = field(default_factory=list)  # 걸려 있는 P-xx
    conditions: list[str] = field(default_factory=list)  # 완료 조건 번호("1", "2", ...)
    tags: list[str] = field(default_factory=list)  # "에셋", "검수"
    same_as: list[str] = field(default_factory=list)  # "조건은 X와 같음"의 X
    batch_hours: float = 0.0  # 모듈 문서 일괄 갱신 시간(그날 흐름의 마지막 태스크에만)
    batch_targets: list[str] = field(default_factory=list)  # 일괄 갱신에서 변경 이력 행이 반드시 있어야 하는 태스크
    batch_conditional: list[str] = field(default_factory=list)  # 코드를 바꿨을 때만 행이 필요한 태스크
    batch_modules: list[str] = field(default_factory=list)  # 일괄 갱신 대상 모듈 문서

    @property
    def kind(self) -> str:
        return "integration" if self.task_id.startswith("INT-") else "task"


@dataclass
class Decision:
    """결정 대기 항목(README 7절 표의 한 행)."""

    decision_id: str
    what: str
    blocks: str
    deadline: str


def _date_key(month: str, day: str) -> str:
    return f"{int(month):02d}-{int(day):02d}"


def _first_segment_after(label: str, text: str) -> str:
    """`label:` 뒤부터 다음 ` · ` 또는 줄 끝까지를 돌려준다."""
    idx = text.find(label)
    if idx < 0:
        return ""
    rest = text[idx + len(label):]
    for stop in (" · ", "\n"):
        cut = rest.find(stop)
        if cut >= 0:
            rest = rest[:cut]
    return rest


def parse_task_file(text: str, flow: str, source: str) -> list[Task]:
    """흐름 파일 하나를 태스크 목록으로 바꾼다.

    규칙: `## 11/5(목)` 같은 날짜 제목이 날짜를 정한다. 날짜가 아닌 `##` 제목은
    날짜를 지운다. `### <ID> <제목>`만 태스크로 본다. 코드 블록 안의 `#`은 무시한다.
    `---` 줄은 태스크 본문을 끝낸다.
    """
    tasks: list[Task] = []
    date: str | None = None
    current: dict | None = None
    in_fence = False

    def close() -> None:
        if current is None:
            return
        body = "\n".join(current["lines"]).strip("\n")
        tasks.append(_build_task(current["id"], current["title"], flow, current["date"], body, source))

    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current is not None:
                current["lines"].append(line)
            continue
        if not in_fence and line.startswith("## "):
            close()
            current = None
            m = _DATE_HEADING_RE.match(line)
            date = _date_key(m.group(1), m.group(2)) if m else None
            continue
        if not in_fence and line.startswith("### "):
            close()
            current = None
            m = _HEADING_TASK_RE.match(line)
            if m:
                tid, title = m.group(1), m.group(2).strip()
                tdate = date
                if tid.startswith("INT-"):
                    dm = _INT_DATE_RE.search(title)
                    tdate = _date_key(dm.group(1), dm.group(2)) if dm else None
                current = {"id": tid, "title": title, "date": tdate, "lines": []}
            continue
        if not in_fence and line.strip() == "---" and current is not None:
            close()
            current = None
            continue
        if current is not None:
            current["lines"].append(line)
    close()
    return tasks


def _build_task(tid: str, title: str, flow: str, date: str | None, body: str, source: str) -> Task:
    lines = body.splitlines()
    hours = 0.0
    doc_hours = 0.0
    batch = {"hours": 0.0, "targets": [], "conditional": [], "modules": []}
    for ln in lines:
        b = _BATCH_RE.match(ln)
        if b:
            batch = {
                "hours": float(b.group(1)),
                "targets": TASK_ID_RE.findall(_first_segment_after("대상:", ln)),
                "conditional": TASK_ID_RE.findall(_first_segment_after("코드를 바꿨다면:", ln)),
                "modules": [a or c for a, c in _MODULE_LINK_RE.findall(_first_segment_after("모듈:", ln))],
            }
        m = _HOURS_RE.match(ln)
        if m and hours == 0.0:
            hours = float(m.group(1))
        d = _DOC_HOURS_RE.match(ln)
        if d:
            doc_hours = float(d.group(1))
    first = lines[0] if lines else ""
    dep_text = _first_segment_after("선행:", first)
    deps = [d for d in TASK_ID_RE.findall(dep_text) if d != tid]
    decisions = sorted(set(DECISION_ID_RE.findall(first)))
    tags = [t for t in ("에셋", "검수") if f"[{t}]" in title]
    same = _SAME_AS_RE.search(body)
    same_as = TASK_ID_RE.findall(same.group(1)) if same else []
    clean_title = re.sub(r"\s*\[(에셋|검수)\]", "", title).strip()
    return Task(
        task_id=tid, title=clean_title, flow="INT" if tid.startswith("INT-") else flow, date=date,
        hours=hours, doc_hours=doc_hours, body=body, source=source,
        deps=list(dict.fromkeys(deps)), decisions=decisions, conditions=_condition_numbers(lines),
        tags=tags, same_as=same_as, batch_hours=batch["hours"], batch_targets=batch["targets"],
        batch_conditional=batch["conditional"], batch_modules=batch["modules"],
    )


def _condition_numbers(lines: list[str]) -> list[str]:
    """`완료 조건:` 아래의 번호 목록. 번호가 없고 한 줄짜리 조건이면 ["1"]."""
    nums: list[str] = []
    in_cond = False
    has_inline = False
    for ln in lines:
        if _COND_LABEL_RE.search(ln):
            in_cond = True
            after = _COND_LABEL_RE.split(ln, 1)[-1].strip()
            if after and not after.startswith("("):
                has_inline = True
            continue
        if in_cond:
            m = _COND_RE.match(ln)
            if m:
                nums.append(m.group(1))
            elif ln.startswith("- "):
                in_cond = False
    if nums:
        return list(dict.fromkeys(nums))
    if has_inline:
        return ["1"]
    # 통합 태스크는 판정 표의 번호 열(`| 1 | ...`)이 조건 번호다.
    table_nums = [m.group(1) for ln in lines if (m := _TABLE_NUM_RE.match(ln))]
    return list(dict.fromkeys(table_nums))


def _inherit_conditions(tasks: list[Task]) -> None:
    """"조건은 X와 같음" 태스크는 X의 조건 번호를 물려받는다(자기 번호가 없을 때만)."""
    by_id = {t.task_id: t for t in tasks}
    for t in tasks:
        if not t.same_as or (t.conditions and t.conditions != ["1"]):
            continue
        nums: list[str] = []
        for ref in t.same_as:
            src = by_id.get(ref)
            if src:
                nums.extend(src.conditions)
        t.conditions = list(dict.fromkeys(nums)) or ["1"]


def parse_decisions(readme_text: str) -> list[Decision]:
    """README 7절 결정 대기 표를 읽는다. `| P-xx | 무엇 | 막는 태스크 | 기한 |`."""
    out: list[Decision] = []
    for line in readme_text.splitlines():
        if not line.startswith("| P-"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4 or not DECISION_ID_RE.fullmatch(cells[0]):
            continue
        out.append(Decision(cells[0], cells[1], cells[2], cells[3]))
    return out


def load_all(tasks_dir: Path) -> tuple[list[Task], list[Decision]]:
    """`docs/tasks/`의 흐름 파일 5개와 README를 읽는다."""
    tasks: list[Task] = []
    for flow, name in FLOW_FILES.items():
        path = tasks_dir / name
        if path.exists():
            tasks.extend(parse_task_file(path.read_text(encoding="utf-8"), flow, f"docs/tasks/{name}"))
    _inherit_conditions(tasks)
    readme = tasks_dir / "README.md"
    decisions = parse_decisions(readme.read_text(encoding="utf-8")) if readme.exists() else []
    return tasks, decisions


def hours_table(tasks: list[Task]) -> dict[str, dict[str, float]]:
    """흐름 → 날짜 → (태스크 시간 + 문서화 시간 + 일괄 갱신 시간). 통합(INT)은 주관 태스크 시간에 들어 있어 뺀다."""
    table: dict[str, dict[str, float]] = {}
    for t in tasks:
        if t.kind != "task" or t.date is None:
            continue
        table.setdefault(t.flow, {}).setdefault(t.date, 0.0)
        table[t.flow][t.date] += t.hours + t.doc_hours + t.batch_hours
    return table
