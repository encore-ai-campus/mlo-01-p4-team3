"""증빙 검사(`docs/tasks/README.md` 4절 검사 규칙, 11절 PR 본문 문법, E-05-02 조건 2·3).

    python -m tools.check_evidence E-05-02 --format      # 양식 제목·작성자(흐름)·실제 소요 시간(h)
    python -m tools.check_evidence E-05-02 --complete    # 4절 검사 규칙 1~6(규칙 6 = check_docs --task)
    python -m tools.check_evidence --all --until 11-05   # 예정일이 그날까지인 태스크 전부 --complete
    python -m tools.check_evidence --changed <base>      # base 이후 바뀐 증빙에 --format(push 이벤트)
    python -m tools.check_evidence --pr-body-env PR_BODY --changed <base>   # PR 본문 검사(pull_request)

GitHub를 조회하지 않는다. 계층: 규칙 함수(도메인, 문자열만 받음) / `GitRepo`(인프라) /
`complete`·`check_changed`·`check_pr`(응용) / `main`(표현). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from tools import check_docs
from tools.evidence_md import (
    BASE_HEADINGS, EVIDENCE_PATH_RE, FLOWS, NUMBER_RE, NUMERIC_COLUMNS, REVIEW_HEADING, Evidence,
    evidence_path, numeric_rows, parse_evidence, parse_table, strip_code,
)
from tools.gitrepo import ZERO_SHA_RE, GitError, GitRepo
from tools.jsonlog import log
from tools.tasks_md import DECISION_ID_RE, TASK_ID_RE, Task, load_all

_NUM = r"-?\d+(?:\.\d+)?"
CRIT_OP_RE = re.compile(rf"^(<=|>=|==|<|>)\s*({_NUM})(?:\s+(\S+))?$")
CRIT_RANGE_RE = re.compile(rf"^({_NUM})\s*~\s*({_NUM})(?:\s+(\S+))?$")
MEASURED_RE = re.compile(rf"^({_NUM})(?:\s+(\S+))?$")
RESULT_CELL_RE = re.compile(r"^(docs/evidence/perf/([^/#]+)/[^/#]+\.json)#([a-z0-9_]+)$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


# ---------------------------------------------------------------- 도메인: --format
def check_format(task_id: str, ev: Evidence, task: Task | None) -> list[str]:
    """`--format`: 양식 제목, `작성자(흐름)` 값, `실제 소요 시간(h)` 숫자 형식. `상태: 실패`도 통과할 수 있다."""
    p = evidence_path(task_id)
    if task is None:
        return [f"{p}: 태스크 문서에 없는 ID {task_id}"]
    errors = []
    if not ev.title or not re.match(rf"^{re.escape(task_id)}(?:\s|$)", ev.title):
        errors.append(f"{p}: 첫 제목이 `# {task_id} <제목>`이 아님")
    required = list(BASE_HEADINGS) + ([REVIEW_HEADING] if "검수" in task.tags else [])
    missing = [h for h in required if h not in ev.headings]
    if missing:
        errors.append(f"{p}: 양식 제목 없음: {', '.join('## ' + h for h in missing)}")
    author = ev.field_value("작성자(흐름)")
    if author is None:
        errors.append(f"{p}: `- 작성자(흐름):` 줄이 없음")
    elif author not in FLOWS:
        errors.append(f"{p}: 작성자(흐름) 값은 {'·'.join(FLOWS)} 중 하나: {author!r}")
    hours = ev.field_value("실제 소요 시간(h)")
    if hours is None:
        errors.append(f"{p}: `- 실제 소요 시간(h):` 줄이 없음")
    elif not NUMBER_RE.match(hours):
        errors.append(f"{p}: 실제 소요 시간(h)이 숫자 형식이 아님: {hours!r}")
    return errors


# ---------------------------------------------------------------- 도메인: 수치 표(규칙 1·2·3)
@dataclass
class Criterion:
    """기준 칸. op는 `<=`·`<`·`>=`·`>`·`==`·`~`(범위, 양 끝 포함)."""

    op: str
    low: Decimal
    high: Decimal | None
    unit: str

    def holds(self, value: Decimal) -> bool:
        """반올림하지 않은 값으로 기준을 만족하는지."""
        if self.op == "~":
            return self.low <= value <= self.high
        return {"<=": value <= self.low, "<": value < self.low, ">=": value >= self.low,
                ">": value > self.low, "==": value == self.low}[self.op]


def parse_criterion(cell: str) -> Criterion | None:
    """`<= 1000 ms`, `40~60 %`, `== 0 건`, `<= 0.05`. 해석할 수 없으면 None."""
    s = strip_code(cell)
    m = CRIT_OP_RE.match(s)
    if m:
        return Criterion(m.group(1), Decimal(m.group(2)), None, m.group(3) or "")
    m = CRIT_RANGE_RE.match(s)
    if m:
        lo, hi = Decimal(m.group(1)), Decimal(m.group(2))
        return Criterion("~", lo, hi, m.group(3) or "") if lo <= hi else None
    return None


def round_half_up(value: Decimal, places: int) -> Decimal:
    """사사오입(ROUND_HALF_UP)으로 소수 places 자리."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


@dataclass
class RowResult:
    """수치 행 하나의 검사 결과. `commit`은 결과 JSON의 commit(규칙 4 대조용)."""

    errors: list[str] = field(default_factory=list)
    commit: str | None = None


def check_numeric_row(task_id: str, row: list[str], load_json) -> RowResult:
    """규칙 3: 기준 문법, 결과 파일·키, JSON task, 단위 글자 일치, 반올림 대조, 판정 재계산.

    load_json(path) → (dict | None, 오류 문자열 | None). 행은 칸 6개(조건·기준·측정값·방법·결과 파일·통과).
    """
    res = RowResult()
    cond, crit_cell, measured_cell = row[0], strip_code(row[1]), strip_code(row[2])
    result_cell, pass_cell = strip_code(row[4]), strip_code(row[5])
    where = f"{evidence_path(task_id)} 수치 조건 {cond}"
    if crit_cell == "-":
        if result_cell != "-":
            res.errors.append(f"{where}: 기준 칸이 `-`이면 결과 파일 칸도 `-`여야 함")
        return res
    crit = parse_criterion(crit_cell)
    if crit is None:
        res.errors.append(f"{where}: 기준 칸을 해석할 수 없음: {crit_cell!r}")
        return res
    if result_cell == "-":
        res.errors.append(f"{where}: 수치 기준인데 결과 파일 칸이 `-`(④ 수치는 계측값만)")
        return res
    m = RESULT_CELL_RE.match(result_cell)
    if not m:
        res.errors.append(f"{where}: 결과 파일 칸은 `docs/evidence/perf/<ID>/<이름>.json#<metrics 키>`: {result_cell!r}")
        return res
    path, dir_id, key = m.groups()
    if dir_id != task_id:
        res.errors.append(f"{where}: 결과 파일이 docs/evidence/perf/{task_id}/ 아래가 아님: {path}")
        return res
    data, err = load_json(path)
    if err:
        res.errors.append(f"{where}: {err}")
        return res
    if not isinstance(data, dict):
        res.errors.append(f"{where}: {path}가 JSON 객체가 아님")
        return res
    if data.get("task") != task_id:
        res.errors.append(f"{where}: {path}의 task {data.get('task')!r} ≠ {task_id}")
    commit = data.get("commit")
    res.commit = commit if isinstance(commit, str) else ""
    metrics = data.get("metrics")
    item = metrics.get(key) if isinstance(metrics, dict) else None
    if not isinstance(item, dict):
        res.errors.append(f"{where}: {path}에 metrics 키 {key!r}가 없음")
        return res
    value, unit = item.get("value"), item.get("unit")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        res.errors.append(f"{where}: {path}#{key}의 value가 숫자가 아님: {value!r}")
        return res
    if not isinstance(unit, str):
        res.errors.append(f"{where}: {path}#{key}의 unit이 문자열이 아님")
        return res
    mm = MEASURED_RE.match(measured_cell)
    if not mm:
        res.errors.append(f"{where}: 측정값 칸은 `<수> <단위>`: {measured_cell!r}")
        return res
    measured_str, measured_unit = mm.group(1), mm.group(2) or ""
    if not (crit.unit == measured_unit == unit):
        res.errors.append(f"{where}: 단위가 다름(기준 {crit.unit!r}, 측정값 {measured_unit!r}, JSON {unit!r})")
        return res
    try:
        exact = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        res.errors.append(f"{where}: {path}#{key}의 value를 읽을 수 없음: {value!r}")
        return res
    places = len(measured_str.split(".")[1]) if "." in measured_str else 0
    if round_half_up(exact, places) != Decimal(measured_str):
        res.errors.append(f"{where}: 측정값 {measured_str}이 JSON 값 {value}(소수 {places}자리 반올림 "
                          f"{round_half_up(exact, places)})과 다름")
    expected = "예" if crit.holds(exact) else "아니오"
    if pass_cell != expected:
        res.errors.append(f"{where}: 통과 칸 {pass_cell!r} ≠ 다시 계산한 판정 {expected!r}(값 {value}, 기준 {crit_cell})")
    return res


def check_numeric_table(task_id: str, ev: Evidence, task: Task, load_json) -> tuple[list[str], list[str]]:
    """규칙 1·2·3. (오류 목록, 결과 JSON commit 목록)."""
    errors: list[str] = []
    commits: list[str] = []
    p = evidence_path(task_id)
    if ev.failed:
        errors.append(f"{p}: `상태: 실패` 문서는 --complete를 통과하지 못함(규칙 2)")
    rows = numeric_rows(ev)
    seen: set[str] = set()
    for row in rows:
        if len(row) != len(NUMERIC_COLUMNS):
            errors.append(f"{p}: 수치 표 행의 칸 수가 {len(row)}(필요 {len(NUMERIC_COLUMNS)}): {' | '.join(row)}")
            continue
        cond = strip_code(row[0])
        if not re.fullmatch(r"\d+", cond):
            errors.append(f"{p}: 수치 표 조건 칸은 완료 조건 번호 하나: {cond!r}(규칙 1)")
        else:
            seen.add(cond)
        if strip_code(row[5]) != "예":
            errors.append(f"{p}: 조건 {cond} 행의 통과 칸이 \"예\"가 아님: {row[5]!r}(규칙 2)")
        rr = check_numeric_row(task_id, row, load_json)
        errors += [e + "(규칙 3)" for e in rr.errors]
        if rr.commit is not None:
            commits.append(rr.commit)
    missing = [c for c in task.conditions if c not in seen]
    if missing:
        errors.append(f"{p}: 수치 표에 완료 조건 번호 {', '.join(missing)} 행이 없음(규칙 1)")
    return errors, commits


def check_review(task_id: str, ev: Evidence, task: Task) -> list[str]:
    """규칙 5: `[검수]` 태스크는 검수 표가 있고 모든 행의 검수자 흐름이 작성자(흐름)과 다르다."""
    if "검수" not in task.tags:
        return []
    p = evidence_path(task_id)
    section = ev.sections.get(REVIEW_HEADING)
    rows = parse_table(section) if section is not None else []
    if not rows:
        return [f"{p}: [검수] 태스크인데 `## 검수` 표 행이 없음(규칙 5)"]
    author = ev.field_value("작성자(흐름)")
    errors = []
    for row in rows:
        reviewer = strip_code(row[0]) if row else ""
        if reviewer not in FLOWS:
            errors.append(f"{p}: 검수자(흐름) 칸은 흐름 ID({'·'.join(FLOWS)}): {reviewer!r}(규칙 5)")
        elif reviewer == author:
            errors.append(f"{p}: 검수자 흐름 {reviewer} = 작성자 흐름(규칙 5)")
        if len(row) < 4 or not all(c.strip() for c in row[:4]):
            errors.append(f"{p}: 검수 행에 기준표·샘플 ID·판정 칸이 비었음(규칙 5)")
    return errors


def check_commits(task_id: str, ev: Evidence, json_commits: list[str], commit_exists) -> list[str]:
    """규칙 4: 증빙 `- 커밋:`과 결과 JSON `commit`이 저장소에 있다."""
    p = evidence_path(task_id)
    errors = []
    sha = ev.field_value("커밋")
    if not sha:
        errors.append(f"{p}: `- 커밋:` 값이 없음(규칙 4)")
    elif not SHA_RE.match(sha) or not commit_exists(sha):
        errors.append(f"{p}: 커밋 {sha!r}이 저장소에 없음(규칙 4)")
    for c in dict.fromkeys(json_commits):
        if not c or not SHA_RE.match(c) or not commit_exists(c):
            errors.append(f"{p}: 결과 JSON의 commit {c!r}이 저장소에 없음(규칙 4)")
    return errors


# ---------------------------------------------------------------- 도메인: PR 본문(11절)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TASK_LINE_RE = re.compile(r"^\s*Task\s*:(.*)$", re.M)
_CLOSING_RE = re.compile(r"(?i)\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*(?:[\w.-]+/[\w.-]+)?#\d+")
_REFS_RE = re.compile(r"(?i)\brefs\s*:?\s*(?:[\w.-]+/[\w.-]+)?#\d+")
_PR_HOURS_RE = re.compile(r"^\s*(?:-\s*)?실제 소요 시간\(h\)\s*:[ \t]*(.*?)[ \t]*$", re.M)


@dataclass
class PrCheck:
    """PR 본문 검사 결과. mode: "complete"(Closes), "format"(Refs), "decision"(Task: P-xx), None(실패)."""

    errors: list[str]
    task_id: str | None = None
    mode: str | None = None


def check_pr_body(body: str, task_ids: set[str], confirmed: set[str]) -> PrCheck:
    """README 11절: `Task:` 줄 1개·ID 1개, `Closes #`/`Refs #` 중 정확히 하나, 실제 소요 시간(h)."""
    text = _COMMENT_RE.sub("", body or "")
    errors: list[str] = []
    lines = _TASK_LINE_RE.findall(text)
    task_id = None
    if len(lines) != 1:
        errors.append(f"PR 본문의 `Task:` 줄이 {len(lines)}개(정확히 1개)")
    else:
        value = lines[0].strip()
        if TASK_ID_RE.fullmatch(value) or DECISION_ID_RE.fullmatch(value):
            task_id = value
        else:
            errors.append(f"`Task:` 값은 태스크 ID 하나: {value!r}")
    is_decision = bool(task_id and DECISION_ID_RE.fullmatch(task_id))
    if task_id and not is_decision and task_id not in task_ids:
        errors.append(f"`Task: {task_id}`가 태스크 문서에 없음")
    if is_decision and task_id not in confirmed:
        errors.append(f"`Task: {task_id}`가 README 7절 \"확정된 결정\"에 기록되지 않음")
    closes = others = 0
    for m in _CLOSING_RE.finditer(text):
        if m.group(1).lower() == "closes":
            closes += 1
        else:
            others += 1
            errors.append(f"허용되지 않은 닫기 키워드 {m.group(1)!r}(`Closes #` 또는 `Refs #`만)")
    refs = len(_REFS_RE.findall(text))
    if others == 0 and closes + refs != 1:
        errors.append(f"이슈 연결 줄은 `Closes #<번호>` 또는 `Refs #<번호>` 중 정확히 하나(Closes {closes}, Refs {refs})")
    if not is_decision:
        hm = _PR_HOURS_RE.findall(text)
        if not hm:
            errors.append("PR 본문에 `실제 소요 시간(h):` 줄이 없음")
        elif not NUMBER_RE.match(hm[0]):
            errors.append(f"PR 본문의 실제 소요 시간(h)이 숫자 형식이 아님: {hm[0]!r}")
    if errors:
        return PrCheck(errors, task_id, None)
    mode = "decision" if is_decision else ("complete" if closes == 1 else "format")
    return PrCheck([], task_id, mode)


def confirmed_decisions(readme_text: str) -> set[str]:
    """README 7절 "확정된 결정" 목록들에 나오는 P-xx."""
    sec = re.search(r"^## 7\..*?(?=^## )", readme_text, re.S | re.M)
    text = sec.group(0) if sec else ""
    ids: set[str] = set()
    for block in re.finditer(r"^\*\*확정된 결정.*?(?=^\*\*|\Z)", text, re.S | re.M):
        ids.update(DECISION_ID_RE.findall(block.group(0)))
    return ids


def changed_evidence_ids(changes: list[tuple[str, str]]) -> list[str]:
    """바뀐 파일 목록에서 삭제되지 않은 `docs/evidence/<ID>.md`의 ID."""
    out = []
    for status, path in changes:
        m = EVIDENCE_PATH_RE.match(path)
        if m and status != "D" and m.group(1) not in out:
            out.append(m.group(1))
    return out


# ---------------------------------------------------------------- 응용
def _task(tasks: list[Task], task_id: str) -> Task | None:
    return next((t for t in tasks if t.task_id == task_id), None)


def _json_loader(repo):
    def load(path: str):
        text = repo.read_text(path)
        if text is None:
            return None, f"결과 파일 {path}이 없음"
        try:
            return json.loads(text), None
        except json.JSONDecodeError as exc:
            return None, f"{path}가 JSON이 아님({exc.msg})"
    return load


def check_format_id(task_id: str, repo, tasks: list[Task]) -> list[str]:
    """`<ID> --format`."""
    text = repo.read_text(evidence_path(task_id))
    if text is None:
        return [f"{evidence_path(task_id)}이 없음"]
    return check_format(task_id, parse_evidence(text), _task(tasks, task_id))


def complete(task_id: str, repo, tasks: list[Task]) -> list[str]:
    """`<ID> --complete`: 형식 + 4절 검사 규칙 1~6."""
    text = repo.read_text(evidence_path(task_id))
    if text is None:
        return [f"{evidence_path(task_id)}이 없음"]
    task = _task(tasks, task_id)
    ev = parse_evidence(text)
    errors = check_format(task_id, ev, task)
    if task is None:
        return errors
    table_errors, commits = check_numeric_table(task_id, ev, task, _json_loader(repo))
    errors += table_errors
    errors += check_commits(task_id, ev, commits, repo.commit_exists)
    errors += check_review(task_id, ev, task)
    errors += [f"{e}(규칙 6, check_docs)" for e in check_docs.check_task(task_id, repo, tasks)]
    return errors


def check_all(until: str, repo, tasks: list[Task]) -> dict[str, list[str]]:
    """`--all --until`: 예정일이 그날까지인 태스크 전부(통합 포함) → {ID: 오류 목록}."""
    return {t.task_id: complete(t.task_id, repo, tasks)
            for t in tasks if t.date is not None and t.date <= until}


def check_changed(base: str, repo, tasks: list[Task]) -> list[str]:
    """`--changed <base>`: base 이후 바뀐 증빙에 --format. base가 0으로만 된 SHA(새 브랜치 push)면 증빙 전부."""
    if ZERO_SHA_RE.match(base):
        changes = [("A", p) for p in repo.tracked_files()]
    else:
        changes = repo.changed_files(base)
    errors = []
    for tid in changed_evidence_ids(changes):
        errors += check_format_id(tid, repo, tasks)
    return errors


def check_pr(body: str, base: str, repo, tasks: list[Task]) -> list[str]:
    """PR 본문 검사 후 Closes면 --complete, Refs면 --changed, P-xx면 결정 기록만."""
    readme = repo.read_text("docs/tasks/README.md") or ""
    pr = check_pr_body(body, {t.task_id for t in tasks}, confirmed_decisions(readme))
    if pr.errors:
        return pr.errors
    if pr.mode == "complete":
        return complete(pr.task_id, repo, tasks)
    if pr.mode == "format":
        return check_changed(base, repo, tasks)
    return []


# ---------------------------------------------------------------- 표현
def _normalize_date(value: str) -> str:
    m = re.fullmatch(r"(\d{1,2})[-/]?(\d{1,2})", value.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"날짜 형식이 아님: {value!r}(예: 11-05)")
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def main(argv: list[str] | None = None, repo=None, environ=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1, 사용법 오류 2."""
    ap = argparse.ArgumentParser(prog="python -m tools.check_evidence", description="증빙 검사(README 4절·11절)")
    ap.add_argument("task_id", nargs="?", help="태스크 ID(--format/--complete)")
    ap.add_argument("--format", action="store_true", help="형식만 검사")
    ap.add_argument("--complete", action="store_true", help="4절 검사 규칙 1~6")
    ap.add_argument("--all", action="store_true", help="--until까지 태스크 전부 --complete")
    ap.add_argument("--until", type=_normalize_date)
    ap.add_argument("--changed", metavar="BASE", help="BASE 이후 바뀐 증빙에 --format")
    ap.add_argument("--pr-body-env", metavar="VAR", help="PR 본문을 담은 환경 변수 이름")
    args = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    if args.task_id and not (args.format or args.complete):
        ap.error("태스크 ID에는 --format 또는 --complete가 필요하다")
    if args.format and args.complete:
        ap.error("--format과 --complete는 함께 쓸 수 없다")
    if args.all and not args.until:
        ap.error("--all에는 --until이 필요하다")
    if args.pr_body_env and not args.changed:
        ap.error("--pr-body-env에는 --changed <PR base 커밋>이 필요하다")
    if not (args.task_id or args.all or args.changed):
        ap.error("태스크 ID, --all, --changed 중 하나가 필요하다")
    repo = repo or GitRepo()
    started = time.monotonic()
    tasks, _ = load_all(repo.root / "docs" / "tasks")
    target = " ".join(argv) if argv else "(인자 없음)"
    try:
        if args.task_id:
            target = f"{args.task_id} {'--complete' if args.complete else '--format'}"
            errors = (complete if args.complete else check_format_id)(args.task_id, repo, tasks)
        elif args.all:
            target = f"--all --until {args.until}"
            results = check_all(args.until, repo, tasks)
            errors = [f"{tid}: {e}" for tid, errs in results.items() for e in errs]
            bad = [tid for tid, errs in results.items() if errs]
            print(f"태스크 {len(results)}개 중 통과 {len(results) - len(bad)}, 실패 {len(bad)}")
        elif args.pr_body_env:
            target = f"--pr-body-env {args.pr_body_env} --changed {args.changed}"
            errors = check_pr(environ.get(args.pr_body_env, ""), args.changed, repo, tasks)
        else:
            target = f"--changed {args.changed}"
            errors = check_changed(args.changed, repo, tasks)
    except GitError as exc:  # 없는 base 커밋 등. 검사를 못 했으면 실패로 본다
        errors = [f"git 오류로 검사하지 못함: {exc}"]
    for e in errors:
        print(f"FAIL {e}")
    print(f"check_evidence {target}: {'통과' if not errors else f'실패 {len(errors)}건'}")
    extra = {"duration_ms": int((time.monotonic() - started) * 1000), "target": target, "failures": len(errors)}
    if errors:
        log("check_evidence", "error", "증빙 검사 실패", error_type="CheckFailed", **extra)
    else:
        log("check_evidence", "info", "증빙 검사 통과", **extra)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
