"""저장소 배정 전 임시 운영 도구(`docs/tasks/README.md` 10-1절). GitHub 없이 NAS 공유 폴더로 번들을 주고받는다.

    python -m tools.interim ci --base <SHA> [--task <ID> --hours 2 [--complete]]   # 커밋된 HEAD로 로컬 CI
    python -m tools.interim submit --task <ID> --base <공개 main SHA> --hours 2 [--complete] [--out DIR]
    python -m tools.interim intake <번들 경로>                                      # PM: 검증 후 refs/inbox/<ID>/<NN>
    python -m tools.interim merge --task <ID> --number <NN> [--complete --hours 2]  # PM: --no-ff 병합 + 재검사
    python -m tools.interim publish --out DIR --until 11-05 [--inbox DIR]           # PM: 일 마감 전체 검사 + main 번들
    python -m tools.interim status --until 11-05 [--inbox DIR]                      # 진행표
    python -m tools.interim check-ledger                                            # 원장(⑧ 대체) 검사

NAS의 실제 경로·계정은 이 도구와 문서에 적지 않는다. `--out`/`--inbox`에는 PM이 알려 준 공유 폴더나
로컬 폴더를 준다. NAS로 옮길 때는 `.part` 이름으로 복사한 뒤 이름을 바꾼다(10-1절).

계층: 판정 규칙은 `tools/interim_rules.py`(도메인)와 기존 검사 도구의 규칙 함수다. `InterimGit`(인프라,
`GitRepo`에 쓰기 명령을 더함) / `run_ci`·`submit`·`intake`·`merge`·`publish`·`status`·`check_ledger`(응용) /
`main`(표현). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools import check_docs, check_evidence, check_secrets, check_structure
from tools import interim_rules as rules
from tools.evidence_md import evidence_path, parse_evidence
from tools.gitrepo import GitError, GitRepo
from tools.jsonlog import log
from tools.tasks_md import load_all

LEDGER_PATH = "docs/ops/interim_ledger.md"
DEFAULT_OUT = Path("out") / "interim"


# ---------------------------------------------------------------- 인프라
class InterimGit(GitRepo):
    """`GitRepo`(읽기)에 임시 운영에 필요한 git 명령(작업 트리·번들·병합)을 더한다."""

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """git 명령을 실행하고 결과를 돌려준다. check면 실패 시 GitError."""
        proc = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
        return proc

    def rev(self, name: str) -> str:
        """이름 → 커밋 SHA(40자)."""
        return self.run("rev-parse", "--verify", f"{name}^{{commit}}").stdout.strip()

    def is_ancestor(self, older: str, newer: str) -> bool:
        """older가 newer의 조상(같은 커밋 포함)인지."""
        return self.run("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def parents(self, sha: str) -> list[str]:
        """커밋의 부모 SHA 목록."""
        return self.run("rev-list", "--parents", "-n", "1", sha).stdout.split()[1:]

    def dirty(self) -> list[str]:
        """커밋하지 않은 변경과 추적되지 않은 파일(`git status --porcelain`)."""
        return [line for line in self.run("status", "--porcelain").stdout.splitlines() if line.strip()]

    def ref_names(self, prefix: str) -> list[str]:
        """`prefix` 아래 참조 이름들(`git for-each-ref`)."""
        return self.run("for-each-ref", "--format=%(refname)", prefix).stdout.split()

    def current_branch(self) -> str:
        """현재 브랜치 이름(분리된 HEAD면 빈 문자열)."""
        return self.run("branch", "--show-current").stdout.strip()


def default_runner(cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, str]:
    """하위 프로세스 실행 → (종료 코드, 출력 끝부분)."""
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode, "\n".join(out[-3:])


def sha256_of(path: Path) -> str:
    """파일의 SHA-256(NAS 복사 손상 확인용)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 응용
@dataclass
class SubmitInfo:
    """PR 본문을 대신하는 제출 정보(README 10-1절)."""

    task_id: str | None = None
    complete: bool = False
    hours: str | None = None
    kind: str = "task"


@dataclass
class CiResult:
    """로컬 CI 결과. steps는 [(단계, 통과 여부, 요지)]."""

    target: str
    base: str
    steps: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """모든 단계가 통과했는지."""
        return all(passed for _, passed, _ in self.steps)

    def report(self, info: SubmitInfo) -> str:
        """`.ci.txt` 본문."""
        lines = [
            f"로컬 CI: {'통과' if self.ok else '실패'}",
            f"대상 SHA: {self.target}",
            f"base SHA: {self.base}",
            f"시각: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Python: {platform.python_version()}",
            f"태스크: {info.task_id or '-'}",
            f"완료 제출: {'예' if info.complete else '아니오'}",
            f"실제 소요 시간(h): {info.hours or '-'}",
            f"종류: {info.kind}",
        ]
        for name, passed, detail in self.steps:
            lines.append(f"[{'통과' if passed else '실패'}] {name}")
            lines += [f"    {d}" for d in detail.splitlines() if d.strip()]
        return "\n".join(lines) + "\n"


def _step(result: CiResult, name: str, errors: list[str], ok_detail: str = "") -> None:
    result.steps.append((name, not errors, "\n".join(errors[:30]) if errors else ok_detail))


def run_ci(root: Path, base: str, info: SubmitInfo, runner=default_runner, environ=None) -> CiResult:
    """깨끗한 작업 트리 `root`(검사할 커밋을 꺼낸 것)에서 core·docs·evidence·service 단계를 돈다."""
    environ = dict(os.environ if environ is None else environ)
    repo = InterimGit(root)
    result = CiResult(target=repo.head(), base=base)
    try:
        changed = [p for _, p in repo.changed_files(base)]
    except GitError as exc:
        _step(result, "base 확인", [f"base {base}를 읽지 못함: {exc}"])
        return result
    # 정적 검사(docs·evidence·secrets)를 먼저 한다: 테스트 실행이 남기는 파일이 변경분으로 잡히지 않게
    remaining, allow_errors, scanned = check_secrets.run(repo)
    docs_errors = check_docs.check_diff(base, repo)
    try:
        structure_errors = check_structure.check_diff(base, repo)
    except check_structure.PatternError as exc:
        structure_errors = [f"{check_structure.CONTRACT} 허용 패턴을 읽지 못함: {exc}"]
    tasks, _ = load_all(root / "docs" / "tasks")
    ev_errors: list[str] = []
    if info.task_id is not None and info.task_id not in {t.task_id for t in tasks}:
        ev_errors.append(f"태스크 목록에 없는 ID: {info.task_id}")
    if info.hours is not None or info.complete:
        ev_errors += rules.parse_hours(info.hours)
    if info.complete:
        if info.task_id is None:
            ev_errors.append("완료 제출에는 --task가 필요하다")
        else:
            ev_errors += check_evidence.complete(info.task_id, repo, tasks)
    else:
        ev_errors += check_evidence.check_changed(base, repo, tasks)
    ev_errors += check_evidence.check_commit_messages(repo.commit_messages(base))
    environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # core
    rc, tail = runner([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], root, environ)
    _step(result, "core: unittest", [] if rc == 0 else [f"종료 코드 {rc}", tail], tail)
    _step(result, "core: check_secrets", [str(f) for f in remaining] + allow_errors, f"파일 {scanned}개")
    _step(result, f"core: check_structure --diff {base[:12]}", structure_errors, "새 폴더 없음 또는 모두 허용(structure.md 6절)")
    # docs
    _step(result, f"docs: check_docs --diff {base[:12]}", docs_errors)
    # evidence
    _step(result, f"evidence: {'--complete' if info.complete else '--format(바뀐 증빙)'} + 커밋 메시지", ev_errors)
    # service
    ran = environ.get("PALDO_SERVICE_TESTS") == "1"
    svc_errors: list[str] = []
    notes: list[str] = []
    if ran:
        for d in rules.SERVICE_TEST_DIRS:
            if d == "server/tests":
                if (root / "server" / "manage.py").exists():
                    rc, tail = runner([sys.executable, "manage.py", "test"], root / "server", environ)
                    (svc_errors if rc else notes).append(f"server manage.py test: 종료 코드 {rc} {tail}")
                continue
            if (root / d).is_dir():
                rc, tail = runner([sys.executable, "-m", "unittest", "discover", "-s", d, "-t", "."], root, environ)
                (svc_errors if rc else notes).append(f"{d}: 종료 코드 {rc} {tail}")
    svc_errors += rules.service_check(changed, ran)
    _step(result, f"service: {'PALDO_SERVICE_TESTS=1' if ran else '서비스 테스트 안 돎'}", svc_errors,
          "\n".join(notes) or ("대상 영역 변경 없음" if not ran else "서비스 테스트 폴더 없음"))
    return result


def ci_at(repo: InterimGit, rev: str, base: str, info: SubmitInfo, runner=default_runner,
          environ=None) -> CiResult:
    """`rev`를 임시 `git worktree`로 꺼내 `run_ci`를 돌고 작업 트리를 지운다(커밋 안 된 변경은 검사에 안 들어감)."""
    tmp = Path(tempfile.mkdtemp(prefix="paldo_ci_"))
    wt = tmp / "wt"
    repo.run("worktree", "add", "--detach", str(wt), rev)
    try:
        return run_ci(wt, base, info, runner, environ)
    finally:
        repo.run("worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        repo.run("worktree", "prune", check=False)


class InterimError(RuntimeError):
    """제출·병합 절차를 진행할 수 없다(사용자에게 보여 줄 이유)."""


def submit(repo: InterimGit, info: SubmitInfo, base: str, out_dir: Path, runner=default_runner,
           environ=None, seen: Path | None = None) -> tuple[CiResult, Path | None]:
    """제출: 깨끗한지 확인 → 작업 트리에서 로컬 CI → 통과하면 번들·`.ci.txt`를 out_dir에 만든다.

    제출 번호는 out_dir과 seen(공유 폴더의 자기 inbox, 선택)에 있는 번들 중 가장 큰 번호 + 1이다.
    """
    branch = f"task/{info.task_id}"
    if repo.current_branch() != branch:
        raise InterimError(f"현재 브랜치가 {branch}가 아님({repo.current_branch() or '분리된 HEAD'})")
    dirty = repo.dirty()
    if dirty:
        raise InterimError("커밋하지 않은 변경이 있다(추적 안 된 파일 포함). 커밋하거나 치운 뒤 다시 제출:\n  "
                           + "\n  ".join(dirty[:10]))
    base_sha = repo.rev(base)
    head = repo.head()
    if not repo.is_ancestor(base_sha, head):
        raise InterimError(f"base {base[:12]}가 {branch}의 조상이 아님(공개된 main 번들의 SHA를 줘야 한다)")
    if base_sha == head:
        raise InterimError(f"{branch}에 base 이후 커밋이 없다")
    result = ci_at(repo, head, base_sha, info, runner, environ)
    if not result.ok:
        return result, None
    out_dir.mkdir(parents=True, exist_ok=True)
    names = os.listdir(out_dir) + (os.listdir(seen) if seen is not None and seen.is_dir() else [])
    number = rules.next_submission_number(names, info.task_id)
    bundle = out_dir / rules.bundle_filename(info.task_id, number, head)
    repo.run("bundle", "create", str(bundle.resolve()), f"{base_sha}..{branch}")
    repo.run("bundle", "verify", str(bundle.resolve()))
    text = result.report(info) + f"번들: {bundle.name}\n번들 SHA-256: {sha256_of(bundle)}\n"
    bundle.with_suffix(".ci.txt").write_text(text, encoding="utf-8", newline="\n")
    return result, bundle


def _ci_field(text: str, label: str) -> str | None:
    for line in text.splitlines():
        if line.startswith(f"{label}: "):
            return line.split(": ", 1)[1].strip()
    return None


def intake(repo: InterimGit, bundle: Path) -> tuple[str, str]:
    """PM: 번들 이름·SHA-256·`git bundle verify` 확인 후 `refs/inbox/<ID>/<NN>`으로 가져온다 → (ref, HEAD SHA)."""
    name = rules.parse_bundle_name(bundle.name)
    if name is None:
        raise InterimError(f"번들 이름 형식이 아님: {bundle.name}(<ID>_<NN>_<SHA12>.bundle, .part는 복사 중)")
    ci_txt = bundle.with_suffix(".ci.txt")
    if not ci_txt.is_file():
        raise InterimError(f"{ci_txt.name}이 없다(로컬 CI 결과 없이 받지 않는다)")
    text = ci_txt.read_text(encoding="utf-8")
    if _ci_field(text, "로컬 CI") != "통과":
        raise InterimError(f"{ci_txt.name}의 로컬 CI가 통과가 아님")
    if _ci_field(text, "번들 SHA-256") != sha256_of(bundle):
        raise InterimError("번들 SHA-256이 .ci.txt와 다름(복사 손상이거나 다른 번들)")
    repo.run("bundle", "verify", str(bundle.resolve()))
    ref = f"refs/inbox/{name.task_id}/{name.number:02d}"
    tmp_ref = f"refs/inbox-tmp/{name.task_id}/{name.number:02d}"
    # 임시 참조로 받아 검증한 뒤에만 최종 참조를 만든다(검증 실패 커밋이 병합 대상이 되지 않게)
    repo.run("fetch", "--no-tags", str(bundle.resolve()), f"+refs/heads/task/{name.task_id}:{tmp_ref}")
    try:
        sha = repo.rev(tmp_ref)
        target = _ci_field(text, "대상 SHA") or ""
        if not sha.startswith(name.sha12) or sha != target:
            raise InterimError(f"가져온 커밋 {sha[:12]}이 번들 이름·.ci.txt 대상 SHA와 다름")
        if ref in repo.ref_names(ref) and repo.rev(ref) != sha:
            raise InterimError(f"{ref}가 이미 다른 커밋을 가리킨다(같은 번호는 다시 쓰지 않는다, 새 번호로 재제출)")
        repo.run("update-ref", ref, sha)
    finally:
        repo.run("update-ref", "-d", tmp_ref, check=False)
    return ref, sha


def merge(repo: InterimGit, task_id: str, number: int, info: SubmitInfo, runner=default_runner,
          environ=None) -> tuple[str, str, CiResult | None, str]:
    """PM: main에 `--no-ff` 병합 후 병합 결과를 base=병합 전 SHA로 재검사 → (병합 전, 병합 후, CI, 안내)."""
    if repo.current_branch() != "main":
        raise InterimError("main 브랜치에서 병합한다")
    if repo.dirty():
        raise InterimError("main 작업 트리가 깨끗하지 않다")
    ref = f"refs/inbox/{task_id}/{number:02d}"
    submitted = repo.rev(ref)
    newer = rules.newer_numbers(repo.ref_names(f"refs/inbox/{task_id}/"), number)
    if newer:
        raise InterimError(f"{task_id}에 더 큰 제출 번호 {', '.join(f'{n:02d}' for n in newer)}가 있다"
                           "(가장 큰 번호만 유효, 10-1절)")
    pre = repo.head()
    msg = rules.merge_message(task_id, number, info.kind)
    proc = repo.run("merge", "--no-ff", "-m", msg, ref, check=False)
    if proc.returncode != 0:
        return pre, "", None, (
            f"충돌. 해결 후 `git commit`(메시지 그대로 {msg!r})하고 "
            f"`python -m tools.interim ci --base {pre}`로 해결 결과를 재검사한다. "
            f"포기하려면 `git merge --abort`.")
    post = repo.head()
    if submitted not in repo.parents(post):
        return pre, post, None, "병합 커밋의 부모에 제출 HEAD가 없다(이미 병합된 제출?)"
    result = ci_at(repo, post, pre, info, runner, environ)
    if result.ok:
        note = (f"원장 행 초안: | {task_id} | <작성 흐름> | {number:02d} | {submitted} | <검토 흐름> | "
                f"<검토 SHA> | <판정> | {post[:12]} | 통과({datetime.now():%m/%d %H:%M}) |")
    else:
        note = (f"병합 결과 재검사 실패. 되돌리려면 PM만 `git reset --hard {pre}`(main 번들을 아직 공개하지 "
                f"않았을 때만, 10-1절)")
    return pre, post, result, note


def ledger_errors(repo: InterimGit, main_ref: str = "main") -> tuple[list[rules.LedgerRow], list[str]]:
    """원장 전체 검사 → (행, 오류). 병합 SHA·부모·증빙 작성자를 저장소에서 읽어 규칙에 넘긴다."""
    text = repo.read_text(LEDGER_PATH)
    if text is None:
        return [], [f"{LEDGER_PATH}이 없음"]
    rows, errors = rules.parse_ledger(text)
    for row in rows:
        ev_text = repo.read_text(evidence_path(row.task_id))
        author = parse_evidence(ev_text).field_value("작성자(흐름)") if ev_text else None
        in_main = has_parent = False
        if rules.SHORT_SHA_RE.match(row.merge_sha) and repo.commit_exists(row.merge_sha):
            in_main = repo.is_ancestor(row.merge_sha, main_ref)
            has_parent = row.submit_sha in repo.parents(row.merge_sha)
        errors += rules.check_ledger_row(row, author, in_main, has_parent)
    return rows, errors


def status(repo: InterimGit, until: str, inbox: Path | None = None) -> list[tuple[str, str, str]]:
    """진행표: 예정일이 until까지인 태스크 → [(ID, 흐름, 상태)]."""
    tasks, _ = load_all(repo.root / "docs" / "tasks")
    rows, _ = rules.parse_ledger(repo.read_text(LEDGER_PATH) or "")
    names: list[str] = []
    if inbox is not None and inbox.is_dir():
        for p in inbox.rglob("*.bundle"):
            names.append(p.name)
    submitted = rules.latest_submissions(names)
    out = []
    for t in tasks:
        if t.date is None or t.date > until or t.task_id.startswith("INT-"):
            continue
        ev_text = repo.read_text(evidence_path(t.task_id))
        failed = bool(ev_text) and parse_evidence(ev_text).failed
        complete_ok = bool(ev_text) and not check_evidence.complete(t.task_id, repo, tasks)
        mine = [r for r in rows if r.task_id == t.task_id]
        merged = any(rules.SHORT_SHA_RE.match(r.merge_sha) and repo.commit_exists(r.merge_sha)
                     and repo.is_ancestor(r.merge_sha, "HEAD") for r in mine)
        ledger_ok = False
        for r in mine:
            if r.verdict != "통과" or not (rules.SHORT_SHA_RE.match(r.merge_sha) and repo.commit_exists(r.merge_sha)):
                continue
            author = parse_evidence(ev_text).field_value("작성자(흐름)") if ev_text else None
            if not rules.check_ledger_row(r, author, repo.is_ancestor(r.merge_sha, "HEAD"),
                                          r.submit_sha in repo.parents(r.merge_sha)):
                ledger_ok = True
        out.append((t.task_id, t.flow, rules.task_status(complete_ok, failed, ledger_ok, merged,
                                                         t.task_id in submitted)))
    return out


def publish(repo: InterimGit, out_dir: Path, until: str, runner=default_runner,
            environ=None, inbox: Path | None = None) -> tuple[list[str], Path | None]:
    """PM 일 마감: 전체 검사(unittest·check_secrets·check_docs --all·원장) 통과 시 main 번들과 진행표를 쓴다."""
    if repo.current_branch() != "main" or repo.dirty():
        raise InterimError("main 브랜치의 깨끗한 작업 트리에서 공개한다")
    environ = {**(os.environ if environ is None else environ), "PYTHONDONTWRITEBYTECODE": "1"}
    errors: list[str] = []
    rc, tail = runner([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], repo.root, environ)
    if rc:
        errors.append(f"unittest 실패: {tail}")
    remaining, allow_errors, _ = check_secrets.run(repo)
    errors += [str(f) for f in remaining] + allow_errors
    tasks, _ = load_all(repo.root / "docs" / "tasks")
    errors += check_docs.check_all(until, repo, tasks)
    errors += ledger_errors(repo)[1]
    if errors:
        return errors, None
    out_dir.mkdir(parents=True, exist_ok=True)
    head = repo.head()
    now = datetime.now()
    bundle = out_dir / rules.main_bundle_filename(f"{now:%m%d}", f"{now:%H%M}", head)
    # 공유 폴더에 바로 쓸 수 있으므로 `.part`로 다 쓴 뒤 이름을 바꾼다(팀원이 쓰다 만 파일을 읽지 않게)
    part = bundle.with_name(bundle.name + ".part")
    repo.run("bundle", "create", str(part.resolve()), "HEAD", "main")  # HEAD 포함: clone이 main을 꺼낸다
    repo.run("bundle", "verify", str(part.resolve()))
    os.replace(part, bundle)
    table = ["| 태스크 | 흐름 | 상태 |", "|---|---|---|"]
    table += [f"| {tid} | {flow} | {st} |" for tid, flow, st in status(repo, until, inbox)]
    status_file = out_dir / f"status_{now:%m%d}_{now:%H%M}.md"
    status_part = status_file.with_name(status_file.name + ".part")
    status_part.write_text(f"# 진행표 {until}까지 (main {head[:12]})\n\n" + "\n".join(table) + "\n",
                           encoding="utf-8", newline="\n")
    os.replace(status_part, status_file)
    return [], bundle


# ---------------------------------------------------------------- 표현
def _normalize_date(value: str) -> str:
    return check_docs._normalize_date(value)


def _info(args) -> SubmitInfo:
    return SubmitInfo(getattr(args, "task", None), getattr(args, "complete", False),
                      getattr(args, "hours", None), getattr(args, "kind", "task"))


def _print_ci(result: CiResult, info: SubmitInfo) -> None:
    print(result.report(info), end="")


def main(argv: list[str] | None = None, repo: InterimGit | None = None, runner=default_runner,
         environ=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1, 사용법 오류 2."""
    ap = argparse.ArgumentParser(prog="python -m tools.interim", description="저장소 배정 전 임시 운영(README 10-1절)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def info_args(p, task_required=False):
        p.add_argument("--task", required=task_required, help="태스크 ID")
        p.add_argument("--complete", action="store_true", help="완료 제출(check_evidence --complete)")
        p.add_argument("--hours", help="실제 소요 시간(h), 숫자")
        p.add_argument("--kind", choices=rules.KINDS, default="task", help="fix = 수정 제출(fix PR 대신)")

    p = sub.add_parser("ci", help="커밋된 커밋(기본 HEAD)을 임시 작업 트리로 꺼내 로컬 CI")
    p.add_argument("--base", required=True)
    p.add_argument("--rev", default="HEAD")
    info_args(p)
    p = sub.add_parser("submit", help="로컬 CI 통과 시 제출 번들과 .ci.txt 생성")
    p.add_argument("--base", required=True, help="받아 둔 main 번들의 SHA")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--seen", type=Path, help="공유 폴더의 자기 inbox(이미 올린 번호를 이어 쓴다)")
    info_args(p, task_required=True)
    p.set_defaults(hours_required=True)
    p = sub.add_parser("intake", help="PM: 번들 검증 후 refs/inbox/<ID>/<NN>으로 가져오기")
    p.add_argument("bundle", type=Path)
    p = sub.add_parser("merge", help="PM: --no-ff 병합 후 재검사")
    p.add_argument("--number", type=int, required=True)
    info_args(p, task_required=True)
    p = sub.add_parser("publish", help="PM: 일 마감 전체 검사 후 main 번들·진행표")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--until", type=_normalize_date, required=True)
    p.add_argument("--inbox", type=Path, help="진행표의 '제출됨' 판정에 쓸 제출 폴더(하위 폴더 포함)")
    p = sub.add_parser("status", help="진행표")
    p.add_argument("--until", type=_normalize_date, required=True)
    p.add_argument("--inbox", type=Path, help="제출 번들이 있는 폴더(하위 폴더 포함)")
    sub.add_parser("check-ledger", help="원장(⑧ 대체) 검사")
    args = ap.parse_args(argv)
    if getattr(args, "hours_required", False) and args.hours is None:
        ap.error("submit에는 --hours(실제 소요 시간 h)가 필요하다")

    repo = repo or InterimGit()
    started = time.monotonic()
    info = _info(args)
    code = 0
    try:
        if args.cmd == "ci":
            if repo.dirty():
                print("주의: 커밋하지 않은 변경은 검사에 들어가지 않는다(커밋된 커밋만 검사)")
            result = ci_at(repo, args.rev, repo.rev(args.base), info, runner, environ)
            _print_ci(result, info)
            code = 0 if result.ok else 1
        elif args.cmd == "submit":
            result, bundle = submit(repo, info, args.base, args.out, runner, environ, args.seen)
            _print_ci(result, info)
            if bundle is None:
                print("로컬 CI 실패: 번들을 만들지 않았다")
                code = 1
            else:
                print(f"번들: {bundle}\n결과: {bundle.with_suffix('.ci.txt')}\n"
                      "두 파일을 공유 폴더 inbox/<흐름>/에 `.part` 이름으로 복사한 뒤 이름을 바꾼다(10-1절)")
        elif args.cmd == "intake":
            ref, sha = intake(repo, args.bundle)
            print(f"가져옴: {ref} = {sha}")
        elif args.cmd == "merge":
            pre, post, result, note = merge(repo, args.task, args.number, info, runner, environ)
            print(f"병합 전: {pre}\n병합 후: {post or '-'}")
            if result is not None:
                _print_ci(result, info)
            print(note)
            code = 0 if result is not None and result.ok else 1
        elif args.cmd == "publish":
            errors, bundle = publish(repo, args.out, args.until, runner, environ, args.inbox)
            for e in errors:
                print(f"FAIL {e}")
            print(f"main 번들: {bundle}" if bundle else f"공개하지 않음: 실패 {len(errors)}건")
            code = 1 if errors else 0
        elif args.cmd == "status":
            print("| 태스크 | 흐름 | 상태 |\n|---|---|---|")
            for tid, flow, st in status(repo, args.until, args.inbox):
                print(f"| {tid} | {flow} | {st} |")
        else:
            rows, errors = ledger_errors(repo)
            for e in errors:
                print(f"FAIL {e}")
            print(f"check-ledger: 행 {len(rows)}개, {'통과' if not errors else f'실패 {len(errors)}건'}")
            code = 1 if errors else 0
    except (InterimError, GitError) as exc:
        print(f"FAIL {exc}")
        code = 1
    extra = {"duration_ms": int((time.monotonic() - started) * 1000), "target": args.cmd, "exit": code}
    if code:
        log("interim", "error", "임시 운영 명령 실패", error_type="CheckFailed", **extra)
    else:
        log("interim", "info", "임시 운영 명령 통과", **extra)
    return code


if __name__ == "__main__":
    sys.exit(main())
