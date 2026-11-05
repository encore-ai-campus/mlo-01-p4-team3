"""도메인: 저장소 배정 전 임시 운영(`docs/tasks/README.md` 10-1절)의 판정 규칙.

NAS 번들 이름, 제출 번호, 원장(`docs/ops/interim_ledger.md`) 표, 서비스 테스트 대상 경로,
진행표 상태를 문자열과 값만으로 판정한다. 파일·git·NAS를 읽지 않는다(`tools/interim.py`가 부른다).

표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from tools.evidence_md import FLOWS, parse_table, strip_code

TASK_PART = r"(?:[A-ES]-\d{2}-\d{2}|INT-\d{2})"
BUNDLE_RE = re.compile(rf"^({TASK_PART})_(\d{{2}})_([0-9a-f]{{12}})\.bundle$")
MAIN_BUNDLE_RE = re.compile(r"^main_(\d{4})_(\d{4})_([0-9a-f]{12})\.bundle$")
REVIEW_RE = re.compile(rf"^({TASK_PART})_(\d{{2}})\.review\.txt$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
KINDS = ("task", "fix")
VERDICTS = ("통과", "반려")
# 서비스(Mongo·MySQL 등)가 있어야 도는 테스트와 그 대상 코드(README 10-1절 로컬 CI의 service 단계)
SERVICE_PREFIXES = ("server/", "ads/", "tests/infra/", "tests/ads/")
SERVICE_TEST_DIRS = ("tests/infra", "tests/ads", "server/tests")
LEDGER_COLUMNS = ("태스크", "작성 흐름", "제출 번호", "제출 HEAD SHA", "검토 흐름", "검토 SHA", "판정",
                  "병합 SHA", "로컬 CI")


@dataclass(frozen=True)
class BundleName:
    """제출 번들 이름 `<ID>_<번호 2자리>_<HEAD SHA 12자>.bundle`을 푼 값."""

    task_id: str
    number: int
    sha12: str

    @property
    def filename(self) -> str:
        """다시 만든 파일 이름."""
        return bundle_filename(self.task_id, self.number, self.sha12)


def bundle_filename(task_id: str, number: int, head_sha: str) -> str:
    """제출 번들 파일 이름. 번호는 1~99, SHA는 앞 12자."""
    if not 1 <= number <= 99:
        raise ValueError(f"제출 번호는 1~99: {number}")
    return f"{task_id}_{number:02d}_{head_sha[:12]}.bundle"


def parse_bundle_name(name: str) -> BundleName | None:
    """번들 파일 이름 → BundleName. 형식이 아니면 None(`.part` 복사 중 파일도 None)."""
    m = BUNDLE_RE.match(name)
    if not m:
        return None
    return BundleName(m.group(1), int(m.group(2)), m.group(3))


def next_submission_number(names: list[str], task_id: str) -> int:
    """폴더의 파일 이름들 중 그 태스크의 가장 큰 제출 번호 + 1(없으면 1). 이전 번들은 지우지 않는다."""
    numbers = [b.number for b in map(parse_bundle_name, names) if b and b.task_id == task_id]
    return max(numbers, default=0) + 1


def latest_submissions(names: list[str]) -> dict[str, BundleName]:
    """태스크마다 가장 큰 번호의 번들만 유효하다 → {ID: BundleName}."""
    latest: dict[str, BundleName] = {}
    for b in filter(None, map(parse_bundle_name, names)):
        if b.task_id not in latest or b.number > latest[b.task_id].number:
            latest[b.task_id] = b
    return latest


def newer_numbers(ref_names: list[str], number: int) -> list[int]:
    """`refs/inbox/<ID>/<NN>` 참조들 중 number보다 큰 제출 번호(있으면 그 제출은 무효)."""
    found = []
    for name in ref_names:
        tail = name.rsplit("/", 1)[-1]
        if tail.isdigit() and int(tail) > number:
            found.append(int(tail))
    return sorted(found)


def main_bundle_filename(mmdd: str, hhmm: str, head_sha: str) -> str:
    """PM이 공개하는 main 번들 이름 `main_<MMDD>_<HHMM>_<SHA12>.bundle`."""
    return f"main_{mmdd}_{hhmm}_{head_sha[:12]}.bundle"


def merge_message(task_id: str, number: int, kind: str = "task") -> str:
    """병합 커밋 첫 줄. 닫기 키워드를 쓰지 않는다(README 11절). 수정 제출은 `fix`를 붙인다."""
    if kind not in KINDS:
        raise ValueError(f"제출 종류는 {'·'.join(KINDS)}: {kind!r}")
    suffix = ", fix" if kind == "fix" else ""
    return f"Task: {task_id} (제출 {number:02d}{suffix})"


def touches_service(paths: list[str]) -> bool:
    """바뀐 경로가 서비스 테스트 대상 영역을 건드렸는지."""
    return any(p.startswith(SERVICE_PREFIXES) for p in paths)


def service_check(paths: list[str], service_ran: bool) -> list[str]:
    """service 단계 판정: 대상 영역을 건드렸는데 서비스 테스트를 돌리지 않았으면 실패."""
    if touches_service(paths) and not service_ran:
        hit = sorted({p.split("/")[0] + "/" for p in paths if p.startswith(SERVICE_PREFIXES)})
        return [f"서비스 영역({', '.join(hit)})을 바꿨는데 서비스 테스트를 돌리지 않음"
                "(PALDO_SERVICE_TESTS=1과 Compose 기동 필요, README 10-1절)"]
    return []


def parse_hours(value: str | None) -> list[str]:
    """제출 정보의 실제 소요 시간(h): 숫자만(README 4절과 같은 형식)."""
    if value is None:
        return ["실제 소요 시간(h)이 없음(--hours)"]
    if not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        return [f"실제 소요 시간(h)이 숫자 형식이 아님: {value!r}"]
    return []


@dataclass(frozen=True)
class LedgerRow:
    """원장 한 행(README 10-1절 ⑧ 대체 기록)."""

    task_id: str
    author_flow: str
    number: int | None
    submit_sha: str
    review_flow: str
    review_sha: str
    verdict: str
    merge_sha: str
    ci: str


def parse_ledger(text: str) -> tuple[list[LedgerRow], list[str]]:
    """원장 마크다운의 "원장" 절 표 → (행, 형식 오류). 머리글 행은 빼고 읽는다."""
    section = _section(text, "원장")
    rows: list[LedgerRow] = []
    errors: list[str] = []
    for i, cells in enumerate(parse_table(section), start=1):
        cells = [strip_code(c) for c in cells]
        if len(cells) != len(LEDGER_COLUMNS):
            errors.append(f"원장 {i}번째 행: 칸 {len(cells)}개(필요 {len(LEDGER_COLUMNS)}개)")
            continue
        num = int(cells[2]) if cells[2].isdigit() else None
        if num is None:
            errors.append(f"원장 {i}번째 행: 제출 번호가 숫자가 아님: {cells[2]!r}")
        rows.append(LedgerRow(cells[0], cells[1], num, cells[3].lower(), cells[4], cells[5].lower(), cells[6],
                              cells[7].lower(), cells[8]))
    return rows, errors


def _section(text: str, heading: str) -> str:
    """`## <heading>` 절 본문(다음 `## `까지)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == heading
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def check_ledger_row(row: LedgerRow, evidence_author: str | None, merge_in_main: bool,
                     merge_has_submit_parent: bool) -> list[str]:
    """⑧ 대체 판정: 검토 흐름 ≠ 작성 흐름, 검토 SHA = 제출 HEAD, 병합 SHA가 main 조상이고 제출 HEAD를 부모로 가짐."""
    p = f"원장 {row.task_id} 제출 {row.number}"
    errors = []
    author = evidence_author or row.author_flow
    if row.author_flow not in FLOWS:
        errors.append(f"{p}: 작성 흐름 칸은 {'·'.join(FLOWS)} 중 하나: {row.author_flow!r}")
    elif evidence_author and evidence_author != row.author_flow:
        errors.append(f"{p}: 작성 흐름 {row.author_flow} ≠ 증빙 작성자(흐름) {evidence_author}")
    if row.review_flow not in FLOWS:
        errors.append(f"{p}: 검토 흐름 칸은 {'·'.join(FLOWS)} 중 하나: {row.review_flow!r}")
    elif row.review_flow == author:
        errors.append(f"{p}: 검토 흐름 {row.review_flow} = 작성 흐름(⑧ 대체 위반)")
    if not FULL_SHA_RE.match(row.submit_sha):
        errors.append(f"{p}: 제출 HEAD SHA는 40자: {row.submit_sha!r}")
    if row.review_sha != row.submit_sha:
        errors.append(f"{p}: 검토 SHA가 제출 HEAD SHA와 다름(검토한 것과 병합한 것이 같아야 한다)")
    if row.verdict not in VERDICTS:
        errors.append(f"{p}: 판정은 {'·'.join(VERDICTS)} 중 하나: {row.verdict!r}")
    elif row.verdict != "통과":
        errors.append(f"{p}: 검토 판정이 {row.verdict}인 제출을 병합함(⑧ 대체는 통과 판정이 필요하다)")
    if not SHORT_SHA_RE.match(row.merge_sha):
        errors.append(f"{p}: 병합 SHA 형식이 아님: {row.merge_sha!r}")
    else:
        if not merge_in_main:
            errors.append(f"{p}: 병합 SHA {row.merge_sha}가 main에 포함되지 않음")
        if not merge_has_submit_parent:
            errors.append(f"{p}: 병합 SHA {row.merge_sha}의 부모에 제출 HEAD가 없음")
    if not row.ci.startswith("통과"):
        errors.append(f"{p}: 로컬 CI 칸이 '통과'로 시작하지 않음: {row.ci!r}")
    return errors


STATUS_ORDER = ("완료", "병합됨", "제출됨", "실패", "미착수")


def task_status(complete_ok: bool, failed: bool, ledger_ok: bool, merged: bool, submitted: bool) -> str:
    """진행표 상태(README 10-1절 6번).

    완료 = `--complete` 통과 + 원장 행이 검사 통과(병합 SHA가 main에 포함, 검토 통과).
    실패 = 증빙에 `상태: 실패`. 병합됨 = 원장에 병합 행은 있으나 미완. 제출됨 = 번들만 있음.
    """
    if failed:
        return "실패"
    if complete_ok and ledger_ok:
        return "완료"
    if merged:
        return "병합됨"
    if submitted:
        return "제출됨"
    return "미착수"
