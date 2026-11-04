"""도메인: 증빙 문서(`docs/evidence/<ID>.md`) 파서와 "완료된 태스크" 판정.

양식은 `docs/tasks/README.md` 4절, "완료된 태스크" 정의는 10절이다. 문자열만 다루고
파일·git을 읽지 않는다(`check_evidence`, `check_docs`가 함께 쓴다).

표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FLOWS = ("A", "B", "C", "D", "E", "S")
BASE_HEADINGS = ("명령", "출력", "수치", "로그", "트러블", "문서")
REVIEW_HEADING = "검수"
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
EVIDENCE_PATH_RE = re.compile(r"^docs/evidence/([^/]+)\.md$")
_FAILED_RE = re.compile(r"^\s*(?:-\s*)?상태\s*:\s*실패")
_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
NUMERIC_COLUMNS = ("조건", "기준", "측정값", "측정 방법", "결과 파일", "통과")


@dataclass
class Evidence:
    """파싱한 증빙 문서. `sections`는 `## 제목` → 본문(코드 블록 안의 `##`은 제목으로 보지 않는다)."""

    title: str | None
    fields: dict[str, str] = field(default_factory=dict)
    headings: list[str] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    failed: bool = False

    def field_value(self, label: str) -> str | None:
        """`- <label>: 값` 줄의 값. 줄이 없으면 None."""
        return self.fields.get(label)


def parse_evidence(text: str) -> Evidence:
    """증빙 마크다운을 읽는다. 첫 `## ` 앞의 `- 이름: 값` 줄을 필드로 본다."""
    title: str | None = None
    fields: dict[str, str] = {}
    headings: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    failed = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if current is not None:
                sections[current].append(line)
            continue
        if not in_fence:
            if _FAILED_RE.match(line):
                failed = True
            if line.startswith("## "):
                current = line[3:].strip()
                headings.append(current)
                sections.setdefault(current, [])
                continue
            if line.startswith("# ") and title is None and current is None:
                title = line[2:].strip()
                continue
            if current is None:
                m = re.match(r"^-\s*([^:]+?)\s*:[ \t]*(.*?)[ \t]*$", line)
                if m and m.group(1) not in fields:
                    fields[m.group(1)] = m.group(2)
                continue
        if current is not None:
            sections[current].append(line)
    return Evidence(
        title=title, fields=fields, headings=headings,
        sections={k: "\n".join(v) for k, v in sections.items()}, failed=failed,
    )


def split_row(line: str) -> list[str]:
    """`| a | b |` → ["a", "b"]. `\\|`는 칸 안의 `|`로 본다."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [c.replace("\\|", "|").strip() for c in cells]


def parse_table(section: str) -> list[list[str]]:
    """절 본문의 첫 표에서 머리행과 구분행을 뺀 행 목록. 표가 없으면 []."""
    rows: list[list[str]] = []
    seen_header = False
    in_fence = False
    for line in section.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        s = line.strip()
        if not s.startswith("|"):
            if seen_header and rows:
                break
            continue
        if not seen_header:
            seen_header = True
            continue
        if _SEPARATOR_RE.match(s):
            continue
        rows.append(split_row(s))
    return rows


def strip_code(cell: str) -> str:
    """칸 전체를 감싼 백틱 한 쌍을 벗긴다(`` `x` `` → `x`)."""
    s = cell.strip()
    if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
        return s[1:-1].strip()
    return s


def numeric_rows(ev: Evidence) -> list[list[str]]:
    """"수치" 표의 행(칸 6개 기준). 절이 없으면 []."""
    return parse_table(ev.sections.get("수치", ""))


def is_completed(ev: Evidence | None) -> bool:
    """README 10절 "완료된 태스크": 증빙이 있고, `상태: 실패`가 없고, 수치 표 통과 칸이 모두 "예"."""
    if ev is None or ev.failed:
        return False
    for row in numeric_rows(ev):
        if len(row) != len(NUMERIC_COLUMNS) or strip_code(row[5]) != "예":
            return False
    return True


def evidence_path(task_id: str) -> str:
    """태스크 ID → 증빙 경로."""
    return f"docs/evidence/{task_id}.md"
