"""문서화 검사(`docs/tasks/README.md` 10절 검사 규칙 1~6, 공통 완료 정의 ⑦).

    python -m tools.check_docs --task E-05-02          # 규칙 1~3(태스크 하나)
    python -m tools.check_docs --all --until 11-05     # 규칙 4(완료된 구현 태스크의 변경 이력 행)
    python -m tools.check_docs --diff <base>           # 규칙 5~6(PR 변경분)

"완료된 태스크"는 10절 정의(`tools/evidence_md.is_completed`)를 직접 판정한다. `check_evidence`를
부르지 않고(순환 방지), GitHub도 조회하지 않는다.

계층: 규칙 함수(도메인, 문자열·목록만 받음) / `GitRepo`(인프라, `tools/gitrepo.py`) /
`check_task`·`check_all`·`check_diff`(응용, 읽어서 규칙에 넘김) / `main`(표현).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import re
import sys
import time

from tools.evidence_md import evidence_path, is_completed, parse_evidence, parse_table
from tools.gitrepo import GitError, GitRepo
from tools.jsonlog import log
from tools.tasks_md import Task, load_all

REQUIRED_MODULE_SECTIONS = ("무엇을", "왜", "사용법", "설정값", "한계", "변경 이력")
TROUBLE_FIELDS = ("문제", "원인", "대응", "결과")
DOC_OUTPUT_MARK = "산출물 자체가 문서다"
CONTRACT_FILES = (
    "engine/domain/model.py", "engine/domain/events.py", "engine/domain/serde.py", "engine/domain/content.py",
    "engine/ports.py", "server/app/dto.py", "server/app/ads_port.py", "server/api/viewmodels.py",
    "contracts/datasets.py",
)
CONTRACT_GLOBS = ("ads/domain/*.py",)
ROUTING_DOC = "README.md"
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_DATE_CELL_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")


# ---------------------------------------------------------------- 도메인: 규칙 1(트러블)
def trouble_entry(text: str, task_id: str) -> list[str] | None:
    """트러블 파일에서 `### <ID>` 항목의 본문 줄. 항목이 없으면 None."""
    lines = text.splitlines()
    head = re.compile(rf"^###\s+{re.escape(task_id)}(?:\s|$)")
    for i, line in enumerate(lines):
        if head.match(line):
            body = []
            for nxt in lines[i + 1:]:
                if nxt.startswith("### ") or nxt.startswith("## "):
                    break
                body.append(nxt)
            return body
    return None


def check_trouble_entry(body: list[str] | None, task_id: str, path: str) -> list[str]:
    """규칙 1 앞부분: 항목이 있고 4줄 형식(문제·원인·대응·결과)이거나 `- 없음`."""
    if body is None:
        return [f"{path}에 `### {task_id}` 항목이 없음(규칙 1)"]
    bullets = [ln.strip() for ln in body if ln.strip().startswith("- ")]
    if bullets == ["- 없음"]:
        return []
    labels = []
    for b in bullets:
        m = re.match(r"^-\s*([^:]+?)\s*:\s*(.*)$", b)
        labels.append((m.group(1), m.group(2)) if m else (b, ""))
    if [lab for lab, _ in labels] != list(TROUBLE_FIELDS):
        return [f"{path}#{task_id}: `- 문제:`·`- 원인:`·`- 대응:`·`- 결과:` 4줄 또는 `- 없음` 한 줄이어야 함(규칙 1)"]
    empty = [lab for lab, val in labels if not val.strip()]
    if empty:
        return [f"{path}#{task_id}: 빈 줄 {', '.join(empty)}(규칙 1)"]
    return []


def check_trouble_link(evidence_text: str | None, flow: str, task_id: str) -> list[str]:
    """규칙 1 뒷부분: 증빙 "트러블" 절이 `troubles/<흐름>.md#<ID>`를 링크한다."""
    if evidence_text is None:
        return [f"{evidence_path(task_id)}가 없어 트러블 링크를 확인할 수 없음(규칙 1)"]
    section = parse_evidence(evidence_text).sections.get("트러블")
    if section is None:
        return [f"{evidence_path(task_id)}에 `## 트러블` 절이 없음(규칙 1)"]
    if not re.search(rf"troubles/{re.escape(flow)}\.md#{re.escape(task_id)}", section, re.I):
        return [f"{evidence_path(task_id)}의 트러블 절에 `docs/troubles/{flow}.md#{task_id}` 링크가 없음(규칙 1)"]
    return []


# ---------------------------------------------------------------- 도메인: 규칙 2·4(모듈 문서)
def module_headings(text: str) -> list[str]:
    """모듈 문서의 `## ` 제목 목록(코드 블록 제외)."""
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            out.append(line[3:].strip())
    return out


def check_module_sections(text: str, path: str) -> list[str]:
    """필수 절 6개가 이 제목 그대로 이 순서로 있다(사이에 다른 절은 허용)."""
    heads = module_headings(text)
    pos = -1
    for name in REQUIRED_MODULE_SECTIONS:
        try:
            pos = heads.index(name, pos + 1)
        except ValueError:
            return [f"{path}: 필수 절 `## {name}`이 없거나 순서가 다름(필수: {', '.join(REQUIRED_MODULE_SECTIONS)})(규칙 2)"]
    return []


def change_history(text: str) -> list[tuple[str, str]]:
    """`## 변경 이력` 표의 (태스크 ID, 날짜 칸) 목록."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    rows = parse_table("\n".join(sections.get("변경 이력", [])))
    return [(r[0].strip("` "), r[1].strip()) for r in rows if len(r) >= 2]


def date_matches(cell: str, date_key: str) -> bool:
    """변경 이력 날짜 칸(`11/5`)이 예정일(`11-05`)과 같은지."""
    m = _DATE_CELL_RE.match(cell)
    return bool(m) and f"{int(m.group(1)):02d}-{int(m.group(2)):02d}" == date_key


def has_history_row(text: str, task_id: str, date_key: str | None = None) -> bool:
    """변경 이력에 `| <ID> | <날짜> |` 행이 있는지(date_key가 None이면 날짜는 보지 않는다)."""
    return any(tid == task_id and (date_key is None or date_matches(d, date_key))
               for tid, d in change_history(text))


def parse_module_code_map(readme_text: str) -> dict[str, list[str]]:
    """`docs/modules/README.md` 표 → 모듈 이름 → 코드 위치 패턴(백틱 안의 경로만)."""
    out: dict[str, list[str]] = {}
    for line in readme_text.splitlines():
        m = re.match(r"^\|\s*`(\w+)\.md`\s*\|([^|]*)\|", line)
        if not m:
            continue
        pats = [p for p in _BACKTICK_RE.findall(m.group(2)) if "/" in p or p.endswith(".py")]
        out[m.group(1)] = pats
    return out


def touches_module(files: list[str], patterns: list[str]) -> bool:
    """바뀐 파일 중 모듈 코드 위치에 드는 것이 있는지. `x/`는 폴더 접두, `*`는 glob."""
    for f in files:
        for p in patterns:
            if p.endswith("/") and f.startswith(p):
                return True
            if any(ch in p for ch in "*?[") and fnmatch.fnmatchcase(f, p):
                return True
            if f == p:
                return True
    return False


# ---------------------------------------------------------------- 도메인: 규칙 3(문서 산출 태스크)
def is_doc_output_task(task: Task) -> bool:
    """문서화 +0h이고 "산출물 자체가 문서다"가 적힌 태스크."""
    return DOC_OUTPUT_MARK in task.body


def output_paths(task: Task) -> list[str]:
    """태스크 본문 `산출물:` 뒤의 백틱 경로(저장소 상대 경로)."""
    paths: list[str] = []
    for line in task.body.splitlines():
        idx = line.find("산출물:")
        if idx < 0:
            continue
        seg = line[idx + len("산출물:"):]
        cut = seg.find(" · ")
        if cut >= 0:
            seg = seg[:cut]
        paths += [p for p in _BACKTICK_RE.findall(seg) if "/" in p]
    return paths


# ---------------------------------------------------------------- 도메인: 규칙 5·6(변경분)
def public_defs(tree: ast.Module) -> list[tuple[str, int, int, bool]]:
    """(이름, 시작 줄, 끝 줄, docstring 있음). 최상위 공개 함수·클래스와 공개 클래스의 공개 메서드."""
    defs = []
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def span(node) -> tuple[int, int]:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return start, node.end_lineno or node.lineno

    for node in tree.body:
        if not isinstance(node, kinds) or node.name.startswith("_"):
            continue
        s, e = span(node)
        defs.append((node.name, s, e, ast.get_docstring(node) is not None))
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and not sub.name.startswith("_"):
                    s2, e2 = span(sub)
                    defs.append((f"{node.name}.{sub.name}", s2, e2, ast.get_docstring(sub) is not None))
    return defs


def missing_docstrings(source: str, changed: set[int] | None, path: str) -> list[str]:
    """규칙 5: 바뀐 줄(None이면 파일 전체)과 겹치는 공개 정의 중 docstring이 없는 것."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [f"{path}: 구문 오류로 docstring을 검사할 수 없음({exc.msg}, {exc.lineno}행)(규칙 5)"]
    errors = []
    for name, s, e, has_doc in public_defs(tree):
        if has_doc:
            continue
        if changed is None or any(s <= ln <= e for ln in changed):
            errors.append(f"{path}:{s}: 공개 정의 `{name}`에 docstring이 없음(규칙 5)")
    return errors


def docstring_target(path: str) -> bool:
    """규칙 5 대상: `.py`, `tests/`와 마이그레이션 제외."""
    return path.endswith(".py") and not path.startswith("tests/") and "/migrations/" not in f"/{path}"


def is_contract_file(path: str) -> bool:
    """규칙 6의 계약 파일인지."""
    return path in CONTRACT_FILES or any(fnmatch.fnmatchcase(path, g) for g in CONTRACT_GLOBS)


def check_contract_docs(changed_paths: list[str]) -> list[str]:
    """규칙 6 앞부분: 계약 파일이 바뀌면 `docs/contracts/` 문서도 바뀐다."""
    contracts = sorted(p for p in changed_paths if is_contract_file(p))
    if contracts and not any(p.startswith("docs/contracts/") for p in changed_paths):
        return [f"계약 파일 변경({', '.join(contracts)})에 `docs/contracts/` 문서 변경이 없음(규칙 6)"]
    return []


def candidate_new_dirs(added_paths: list[str]) -> list[str]:
    """추가된 파일이 들어 있는 최상위·두 번째 단계 폴더 후보."""
    dirs: list[str] = []
    for p in added_paths:
        parts = p.split("/")
        for depth in (1, 2):
            if len(parts) > depth:
                d = "/".join(parts[:depth])
                if d not in dirs:
                    dirs.append(d)
    return dirs


def check_routing_doc(new_dirs: list[str], changed_paths: list[str]) -> list[str]:
    """규칙 6 뒷부분: 새 폴더가 생기면 `paldo/README.md`(저장소 루트 README.md)도 바뀐다."""
    if new_dirs and ROUTING_DOC not in changed_paths:
        return [f"새 폴더({', '.join(d + '/' for d in new_dirs)})가 생겼는데 `README.md`(라우팅 문서) 변경이 없음(규칙 6)"]
    return []


# ---------------------------------------------------------------- 응용
def _find(tasks: list[Task], task_id: str) -> Task | None:
    return next((t for t in tasks if t.task_id == task_id), None)


def check_task(task_id: str, repo, tasks: list[Task]) -> list[str]:
    """`--task <ID>`: 규칙 1~3. 오류 메시지 목록(빈 목록 = 통과)."""
    task = _find(tasks, task_id)
    if task is None:
        return [f"{task_id}: 태스크 문서에 없는 ID"]
    errors: list[str] = []
    ev_text = repo.read_text(evidence_path(task_id))
    # 규칙 1. 통합(INT)은 10절이 "문서 = 판정 결과(증빙)"로 정해 트러블 항목을 요구하지 않는다(문서 보완 대기).
    if task.kind != "integration":
        tpath = f"docs/troubles/{task.flow}.md"
        ttext = repo.read_text(tpath)
        errors += check_trouble_entry(trouble_entry(ttext, task_id) if ttext is not None else None, task_id, tpath)
        errors += check_trouble_link(ev_text, task.flow, task_id)
    # 규칙 2
    if task.batch_modules or task.batch_targets or task.batch_conditional:
        errors += _check_batch(task, repo, tasks)
    # 규칙 3(산출물 파일 존재까지. 구간 제목은 태스크 문서에 읽을 형식이 없어 검사하지 않는다)
    if is_doc_output_task(task):
        paths = output_paths(task)
        if not paths:
            errors.append(f"{task_id}: 문서 산출 태스크인데 태스크 문서에 `산출물:` 경로가 없음(규칙 3)")
        for p in paths:
            if not repo.exists(p):
                errors.append(f"{task_id}: 산출물 `{p}`이 없음(규칙 3)")
    return errors


def _check_batch(task: Task, repo, tasks: list[Task]) -> list[str]:
    errors: list[str] = []
    readme = repo.read_text("docs/modules/README.md") or ""
    code_map = parse_module_code_map(readme)
    docs: dict[str, str] = {}
    for mod in task.batch_modules:
        path = f"docs/modules/{mod}.md"
        text = repo.read_text(path)
        if text is None:
            errors.append(f"{path}이 없음(규칙 2, {task.task_id} 일괄 갱신)")
            continue
        docs[mod] = text
        errors += check_module_sections(text, path)
    date = task.date or ""

    def completed(tid: str) -> bool:
        t = repo.read_text(evidence_path(tid))
        return is_completed(parse_evidence(t) if t is not None else None)

    def require_row(tid: str, mods: list[str]) -> None:
        if not any(has_history_row(docs[m], tid, date) for m in mods if m in docs):
            where = ", ".join(f"docs/modules/{m}.md" for m in mods)
            errors.append(f"{where} `변경 이력`에 `| {tid} | {date[:2].lstrip('0')}/{date[3:].lstrip('0')} |` 행이 없음(규칙 2)")

    for tid in task.batch_targets:
        if completed(tid):  # 밀린 태스크(완료 아님)는 뺀다
            require_row(tid, task.batch_modules)
    for tid in task.batch_conditional:
        ev = repo.read_text(evidence_path(tid))
        if ev is None or not completed(tid):
            continue
        sha = parse_evidence(ev).field_value("커밋") or ""
        if not sha or not repo.commit_exists(sha):
            errors.append(f"{evidence_path(tid)}의 커밋을 찾을 수 없어 모듈 코드 변경 여부를 판단할 수 없음(규칙 2)")
            continue
        files = repo.commit_files(sha)
        touched = [m for m in task.batch_modules if touches_module(files, code_map.get(m, []))]
        if touched:
            require_row(tid, touched)
    return errors


def check_all(until: str, repo, tasks: list[Task]) -> list[str]:
    """`--all --until <날짜>`: 규칙 4. 완료된 구현 태스크(일괄 갱신 "대상")마다 어느 모듈 문서에든 행."""
    targets: list[str] = []
    for t in tasks:
        for tid in t.batch_targets:
            if tid not in targets:
                targets.append(tid)
    by_id = {t.task_id: t for t in tasks}
    histories = []
    for t in tasks:
        for mod in t.batch_modules:
            text = repo.read_text(f"docs/modules/{mod}.md")
            if text is not None:
                histories.append(text)
    histories = list(dict.fromkeys(histories))
    errors = []
    for tid in targets:
        t = by_id.get(tid)
        if t is None or t.date is None or t.date > until:
            continue
        ev = repo.read_text(evidence_path(tid))
        if not is_completed(parse_evidence(ev) if ev is not None else None):
            continue
        if not any(has_history_row(h, tid) for h in histories):
            errors.append(f"{tid}: 완료된 구현 태스크인데 어느 모듈 문서 `변경 이력`에도 행이 없음(규칙 4)")
    return errors


def check_diff(base: str, repo) -> list[str]:
    """`--diff <base>`: 규칙 5·6."""
    changes = repo.changed_files(base)
    changed_paths = [p for _, p in changes]
    errors: list[str] = []
    for status, path in changes:
        if status == "D" or not docstring_target(path):
            continue
        src = repo.read_text(path)
        if src is None:
            continue
        errors += missing_docstrings(src, repo.changed_lines(base, path), path)
    errors += check_contract_docs(changed_paths)
    added = [p for s, p in changes if s == "A"]
    new_dirs = [d for d in candidate_new_dirs(added) if not repo.dir_existed(base, d)]
    errors += check_routing_doc(new_dirs, changed_paths)
    return errors


# ---------------------------------------------------------------- 표현
def _normalize_date(value: str) -> str:
    m = re.fullmatch(r"(\d{1,2})[-/]?(\d{1,2})", value.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"날짜 형식이 아님: {value!r}(예: 11-05)")
    return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"


def main(argv: list[str] | None = None, repo=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1."""
    ap = argparse.ArgumentParser(prog="python -m tools.check_docs", description="문서화 검사(README 10절)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task", metavar="ID", help="규칙 1~3")
    mode.add_argument("--all", action="store_true", help="규칙 4(--until 필요)")
    mode.add_argument("--diff", metavar="BASE", help="규칙 5~6")
    ap.add_argument("--until", type=_normalize_date, help="--all의 마지막 예정일(예: 11-05)")
    args = ap.parse_args(argv)
    if args.all and not args.until:
        ap.error("--all에는 --until이 필요하다")
    repo = repo or GitRepo()
    started = time.monotonic()
    tasks, _ = load_all(repo.root / "docs" / "tasks")
    target = args.task or (f"--all --until {args.until}" if args.all else f"--diff {args.diff}")
    try:
        if args.task:
            errors = check_task(args.task, repo, tasks)
        elif args.all:
            errors = check_all(args.until, repo, tasks)
        else:
            errors = check_diff(args.diff, repo)
    except GitError as exc:  # 없는 base 커밋 등. 검사를 못 했으면 실패로 본다
        errors = [f"git 오류로 검사하지 못함: {exc}"]
    for e in errors:
        print(f"FAIL {e}")
    print(f"check_docs {target}: {'통과' if not errors else f'실패 {len(errors)}건'}")
    extra = {"duration_ms": int((time.monotonic() - started) * 1000), "target": target, "failures": len(errors)}
    if errors:
        log("check_docs", "error", "문서화 검사 실패", error_type="CheckFailed", **extra)
    else:
        log("check_docs", "info", "문서화 검사 통과", **extra)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
