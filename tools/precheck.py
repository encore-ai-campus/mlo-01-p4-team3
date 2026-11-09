"""로컬 검사 한 번에 돌리기(11/5 PM 결정: GitHub Actions CI 폐지, README 3절 ②).

    python -m tools.precheck                      # 구조·비밀·바뀐 파일과 이어진 단위 테스트(tests/assets 제외)·문서·증빙
    python -m tools.precheck --with-assets        # tests/assets도 포함(에셋 관련 파일을 바꿨을 때)
    python -m tools.precheck --pr-body pr.md      # PR 본문 파일까지 검사(Task·Closes/Refs 문법, Closes면 --complete)
    python -m tools.precheck --base <커밋>         # 비교 기준 직접 지정(기본: origin/dev와 HEAD의 merge-base)
    python -m tools.precheck --base origin/main   # 승격 PR(dev → main): dev에서 main 이후 바뀐 증빙 검사
    python -m tools.precheck --quick              # 작업 중 확인: 같은 대상, 통과 기록 없음, PR 본문에 못 붙이는 줄
    python -m tools.precheck --full-tests         # 테스트 전체(무거운 테스트는 바뀐 파일과 이어질 때만). PM이 배포 후보 전 dev에서 1회
    python -m tools.precheck --all-slow           # 테스트 전체 + 무거운 테스트도 모두(--full-tests 포함)

`git push` 때 `.githooks/pre-push`가 이 명령을 부른다(설치: `git config core.hooksPath .githooks`).

통과 기록(11/7 PM 결정): 깨끗한 작업 트리에서 전체 통과하면 `<git 공용 폴더>/precheck-pass/<HEAD SHA>`에
기록을 남긴다(worktree끼리 공유, 저장소에는 안 올라감). 훅은 먼저 `python -m tools.precheck --check-pass <SHA>`로
그 커밋의 기록과 지금 비교 기준(`origin/dev`와의 merge-base)이 같은지 보고, 같으면 검사를 생략한다.
`PALDO_PRECHECK_NO_CACHE=1`이면 항상 전체 검사한다. 14일 지난 기록은 새 기록을 남길 때 지운다.
하나라도 실패하면 종료 코드 1이고, 마지막 줄에 한 줄 요약(`precheck: 통과 …` 또는 `precheck: 실패 …`)을 낸다.
PR 본문에는 이 마지막 줄을 붙인다.

테스트 대상 고르기(11/9 사용자 결정 "모든 경우에 precheck를 최소화"): 기본 검사(PR 직전 `--pr-body`, pre-push 훅)도
바뀐 파일(비교 기준 대비 작업 트리 차이와 추적 안 하는 파일)과 이어진 테스트 모듈만 돌린다. 고르는 규칙은
`tools/precheck_scope.py`에 있고, 공용 파일·쓰는 테스트를 못 찾은 모듈처럼 애매하면 전체 테스트로 돈다. 바뀐 파일이 없으면
(dev를 받아 그대로 돌릴 때) 전체다. 구조·비밀·문서·증빙은 언제나 그대로 돈다. 요약 줄은
`precheck: 통과 (…, 테스트 N개(선택: tests/tools 3개…), …)`로 고른 실행임을 적고 PR 본문에 붙일 수 있다. 통과 기록 mode에
`selected`가 붙는다.

`--quick`(11/9 PM 결정)은 같은 대상을 돌리되 통과 기록을 남기지 않고, 마지막 줄이 `precheck(빠른): …`이라 PR 본문에
붙일 수 없다(`check_evidence`가 거부). 커밋 전 작업 트리에서 쓰는 확인용이다.

`--full-tests`: 고르지 않고 테스트 폴더 전체를 돈다. 이때 무거운 테스트(`precheck_scope.SLOW_TESTS`, 11/9 PM 결정)는
바뀐 파일과 이어질 때만 돌리고 `tools/run_tests.py`로 나머지만 돌린다(바뀐 파일이 없거나 공용 파일이면 모두). `--all-slow`는
`--full-tests`에 무거운 테스트까지 모두 돈다. 저장소에 GitHub CI가 없어 전체 묶음을 도는 곳이 없으므로, PM이 배포 후보
태그 전 `dev`에서 `--full-tests`로 한 번 돈다(README 3절 ②).

비교 기준을 못 구하면(원격 `origin/dev`를 받아 오지 않은 새 clone 등) 문서·증빙 검사를 할 수 없으므로
건너뛰지 않고 실패로 본다. 검사 없이 통과 줄이 나오는 것을 막기 위해서다. `git fetch origin` 뒤 다시 돌리거나 `--base`를 준다.

계층: `StepResult`·`test_rc_ok`·`count_tests`·`summarize`·`PassRecord`·`format_record`·`parse_record`·
`may_record`·`skip_decision`·`count_skipped`(도메인, 문자열·숫자만) / `SubprocessRunner`·`PassStore`(인프라, 하위 명령·기록 파일) /
`run_precheck`·`record_pass`·`check_pass`·`select_plan`·`slow_plan`(응용, 순서 조립) / `main`(표현).
테스트 대상 고르기와 무거운 테스트 목록은 도메인 모듈 `tools/precheck_scope.py`.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.precheck_scope import Selection, describe, select_tests, slow_skips

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_REF = "origin/dev"
ASSETS_DIR = "assets"
PR_BODY_ENV = "PRECHECK_PR_BODY"
PASS_DIR_NAME = "precheck-pass"
NO_CACHE_ENV = "PALDO_PRECHECK_NO_CACHE"
RECORD_MAX_AGE = timedelta(days=14)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.M)
_SKIP_RE = re.compile(r"^precheck-skip: (\d+)$", re.M)  # tools/run_tests.SKIP_LINE


# ---------------------------------------------------------------- 도메인
@dataclass(frozen=True)
class StepResult:
    """검사 단계 하나의 결과. `tests`는 단위 테스트 단계에서만 센 테스트 수."""

    name: str
    ok: bool
    output: str = ""
    tests: int = 0
    slow_skipped: int = 0


def test_rc_ok(rc: int) -> bool:
    """unittest discover 종료 코드 판정. 0 = 통과, 5 = 테스트 0개(폴더만 있음)도 통과."""
    return rc in (0, 5)


def count_tests(output: str) -> int:
    """unittest 출력의 `Ran N tests`를 모두 더한다. 없으면 0."""
    return sum(int(n) for n in _RAN_RE.findall(output))


def count_skipped(output: str) -> int:
    """`tools/run_tests` 출력의 `precheck-skip: N`(실제로 뺀 무거운 테스트 수)을 모두 더한다. 없으면 0."""
    return sum(int(n) for n in _SKIP_RE.findall(output))


def summarize(results: list[StepResult], base_short: str | None, seconds: float,
              quick_target: str | None = None, selected: str | None = None) -> tuple[int, str]:
    """단계 결과 → (종료 코드, 한 줄 요약). 하나라도 실패면 1.

    `quick_target`(빠른 검사의 대상 설명)이 있으면 머리를 `precheck(빠른):`로 바꾸고 대상을 붙인다.
    이 줄은 PR 본문에 붙일 수 없다(`check_evidence.check_pr_body`).
    `selected`(기본 검사가 고른 테스트 대상 설명)가 있으면 테스트 수 뒤에 `(선택: 대상)`을 붙인다. 이 줄은 PR 본문에 붙일 수 있다.
    """
    failed = [r.name for r in results if not r.ok]
    base = base_short or "없음"
    head = "precheck" if quick_target is None else "precheck(빠른)"
    target = "" if quick_target is None else f", 대상 {quick_target}"
    if failed:
        sel = f", 선택 {selected}" if selected is not None else ""
        return 1, f"{head}: 실패 ({', '.join(failed)}; base {base}{target}{sel}, {seconds:.0f}초)"
    n = sum(r.tests for r in results)
    skipped = sum(r.slow_skipped for r in results)
    slow = f", 무거운 테스트 {skipped}개 생략" if skipped else ""
    sel = f"(선택: {selected})" if selected is not None else ""
    names = "·".join(r.name for r in results)
    return 0, f"{head}: 통과 ({names}, 테스트 {n}개{sel}{slow}, base {base}{target}, {seconds:.0f}초)"


@dataclass(frozen=True)
class PassRecord:
    """통과 기록 한 건. `base`는 그 실행이 문서·증빙 검사에 쓴 비교 기준 전체 SHA, `at`은 UTC ISO 시각."""

    head: str
    base: str
    mode: str
    at: str


def format_record(rec: PassRecord) -> str:
    """기록 → `key=value` 줄 텍스트."""
    return f"head={rec.head}\nbase={rec.base}\nmode={rec.mode}\nat={rec.at}\n"


def parse_record(text: str) -> PassRecord | None:
    """`format_record` 텍스트 → 기록. 키가 빠졌거나 SHA 형식이 아니면 None."""
    kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    head, base = kv.get("head", ""), kv.get("base", "")
    if not (_SHA_RE.match(head) and _SHA_RE.match(base)):
        return None
    return PassRecord(head, base, kv.get("mode", ""), kv.get("at", ""))


def may_record(rc: int, push_evidence_ok: bool, clean_before: bool, clean_after: bool,
               head_before: str | None, head_after: str | None, base: str | None) -> bool:
    """통과 기록을 남겨도 되는지: 전체 통과, push 때와 같은 증빙 검사 통과, 시작·끝 모두 깨끗한 트리,
    실행 중 HEAD 불변, 기준 있음. 시작 때 더러웠다면 검사가 커밋 안 된 파일을 봤을 수 있다."""
    return (rc == 0 and push_evidence_ok and clean_before and clean_after and base is not None
            and head_before is not None and head_before == head_after)


def skip_decision(rec: PassRecord | None, sha: str, current_base: str | None,
                  disabled: bool) -> tuple[bool, str]:
    """훅이 검사를 생략해도 되는지와 이유. 기록의 커밋·비교 기준이 지금과 같을 때만 생략."""
    if disabled:
        return False, f"{NO_CACHE_ENV}=1, 전체 검사"
    if rec is None:
        return False, "통과 기록 없음, 전체 검사"
    if rec.head != sha:
        return False, "기록의 커밋이 다름, 전체 검사"
    if current_base is None:
        return False, "지금 비교 기준을 못 구함, 전체 검사"
    if rec.base != current_base:
        return False, f"비교 기준이 바뀜({rec.base[:7]} → {current_base[:7]}), 전체 검사"
    return True, f"통과 기록 있음({rec.at}), 검사 생략"


# ---------------------------------------------------------------- 인프라
# git이 훅을 부를 때 넘기는 저장소 지정 변수(`git rev-parse --local-env-vars`). 남아 있으면 테스트가 임시
# 저장소 대신 이 저장소에 `git init`·`commit`·`config`를 해 버린다(11/5 사고: 브랜치 이동, core.bare·user 설정 덮어씀).
GIT_LOCAL_ENV_VARS = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


def clean_env(environ: dict[str, str]) -> dict[str, str]:
    """저장소 지정 git 변수(`GIT_LOCAL_ENV_VARS`, `GIT_CONFIG_KEY_*`·`GIT_CONFIG_VALUE_*` 포함)를 뺀 사본."""
    return {k: v for k, v in environ.items()
            if k not in GIT_LOCAL_ENV_VARS and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}


class SubprocessRunner:
    """하위 명령 실행과 git 조회. 테스트에서는 같은 메서드를 가진 가짜로 바꿔 끼운다."""

    def __init__(self, root: Path = ROOT):
        self.root = root

    def run(self, args: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
        """명령을 저장소 루트에서 실행하고 (종료 코드, 표준 출력+오류)를 돌려준다."""
        full_env = dict(clean_env(dict(os.environ)), PYTHONIOENCODING="utf-8")
        if env:
            full_env.update(env)
        proc = subprocess.run(args, cwd=self.root, env=full_env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def merge_base(self, ref: str, other: str = "HEAD") -> str | None:
        """`ref`와 `other`(기본 HEAD)의 merge-base 전체 SHA. 못 구하면 None."""
        rc, out = self.run(["git", "merge-base", ref, other])
        return out.strip() if rc == 0 and out.strip() else None

    def resolve(self, ref: str) -> str | None:
        """커밋 이름 → 전체 SHA. 없으면 None."""
        rc, out = self.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        return out.strip() if rc == 0 and out.strip() else None

    def test_dirs(self) -> list[str]:
        """`tests/` 아래 패키지 폴더 이름(`__init__.py`가 있는 것), 정렬."""
        base = self.root / "tests"
        return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "__init__.py").exists())

    def changed_files(self, base: str) -> list[str] | None:
        """비교 기준 대비 작업 트리의 바뀐 경로(커밋한 것·안 한 것, 지운 것, 이름 바꾼 양쪽)와 추적 안 하는 파일.
        git 오류면 None."""
        rc1, diff = self.run(["git", "diff", "--name-only", "--no-renames", base])
        rc2, other = self.run(["git", "ls-files", "--others", "--exclude-standard"])
        if rc1 != 0 or rc2 != 0:
            return None
        return sorted({line.strip() for line in (diff + "\n" + other).splitlines() if line.strip()})

    def python_sources(self) -> dict[str, str]:
        """추적하는 `.py`와 추적 안 하는 `.py`(무시 대상 제외)의 경로 → 소스. 못 읽는 파일은 뺀다."""
        rc, out = self.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.py"])
        sources: dict[str, str] = {}
        for rel in (out.splitlines() if rc == 0 else []):
            rel = rel.strip()
            try:
                sources[rel] = (self.root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return sources

    def tree_clean(self) -> bool:
        """`git status --porcelain`이 비었는지(추적 안 하는 파일도 더러운 것으로 본다)."""
        rc, out = self.run(["git", "status", "--porcelain"])
        return rc == 0 and not out.strip()

    def git_common_dir(self) -> Path | None:
        """worktree끼리 공유하는 git 폴더(절대 경로). 못 구하면 None."""
        rc, out = self.run(["git", "rev-parse", "--git-common-dir"])
        if rc != 0 or not out.strip():
            return None
        p = Path(out.strip())
        return p if p.is_absolute() else (self.root / p).resolve()


class PassStore:
    """통과 기록 파일(`<폴더>/<SHA>`) 읽기·쓰기·정리. 판정은 하지 않는다."""

    def __init__(self, directory: Path):
        self.dir = directory

    def read(self, sha: str) -> PassRecord | None:
        """기록을 읽는다. 파일이 없거나 형식이 깨졌으면 None."""
        if not _SHA_RE.match(sha):
            return None
        try:
            return parse_record((self.dir / sha).read_text(encoding="utf-8"))
        except OSError:
            return None

    def write(self, rec: PassRecord) -> None:
        """기록을 임시 파일에 쓴 뒤 바꿔 넣는다(동시 실행에도 반쯤 쓴 파일이 남지 않게)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{rec.head}.{os.getpid()}.tmp"
        tmp.write_text(format_record(rec), encoding="utf-8", newline="\n")
        os.replace(tmp, self.dir / rec.head)

    def prune(self, now: float, max_age: timedelta = RECORD_MAX_AGE) -> None:
        """수정 시각이 `max_age`보다 오래된 기록을 지운다. 실패는 무시한다."""
        if not self.dir.is_dir():
            return
        limit = now - max_age.total_seconds()
        for p in self.dir.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < limit:
                    p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 응용
def run_precheck(runner, base: str | None, with_assets: bool = False,
                 pr_body: str | None = None, workers: int | None = None,
                 only_tests: Selection | None = None,
                 skip_tests: dict[str, list[str]] | None = None) -> list[StepResult]:
    """구조·비밀·단위 테스트(폴더별)·문서·증빙을 동시에 돌리고 결과는 이 순서로 모은다.

    `skip_tests`(폴더 → 테스트 id)가 있는 폴더는 `tools.run_tests`로 그 테스트만 빼고 돌린다(무거운 테스트 생략).

    서로 독립인 하위 명령이라 병렬로 돌린다(11/5: 순서대로 돌리면 약 90초). 앞 단계가 실패해도 끝까지 돈다.
    """
    py = sys.executable
    jobs: dict[str, tuple[list[str], dict[str, str] | None]] = {
        "구조": ([py, "-m", "tools.check_structure", "--all"], None),
        "비밀": ([py, "-m", "tools.check_secrets"], None),
    }
    test_dirs = [d for d in runner.test_dirs() if with_assets or d != ASSETS_DIR]
    if only_tests is not None:  # 빠른 검사: 고른 폴더만(assets 제외 규칙은 그대로)
        test_dirs = [d for d in test_dirs if d in only_tests]
    for d in test_dirs:
        mods = only_tests.get(d) if only_tests is not None else None
        skips = (skip_tests or {}).get(d)
        if mods:  # 빠른 검사: 고른 테스트 모듈만
            jobs[f"tests/{d}"] = ([py, "-m", "unittest", *mods], None)
        elif skips:  # 전체 검사: 바뀐 파일과 이어지지 않는 무거운 테스트는 빼고
            flags = [a for t in skips for a in ("--skip", t)]
            jobs[f"tests/{d}"] = ([py, "-m", "tools.run_tests", "-s", f"tests/{d}", "-t", ".", *flags], None)
        else:
            jobs[f"tests/{d}"] = ([py, "-m", "unittest", "discover", "-s", f"tests/{d}", "-t", "."], None)
    if base is not None:
        jobs["문서"] = ([py, "-m", "tools.check_docs", "--diff", base], None)
        if pr_body is not None:
            jobs["증빙"] = ([py, "-m", "tools.check_evidence", "--pr-body-env", PR_BODY_ENV, "--changed", base],
                          {PR_BODY_ENV: pr_body})
        else:
            jobs["증빙"] = ([py, "-m", "tools.check_evidence", "--changed", base], None)

    with ThreadPoolExecutor(max_workers=workers or min(8, len(jobs))) as pool:
        futures = {k: pool.submit(runner.run, args, env) for k, (args, env) in jobs.items()}
        done = {k: f.result() for k, f in futures.items()}

    results = [StepResult(k, done[k][0] == 0, done[k][1]) for k in ("구조", "비밀")]
    outputs, ok, n, skipped = [], True, 0, 0
    for d in test_dirs:
        rc, out = done[f"tests/{d}"]
        n += count_tests(out)
        skipped += count_skipped(out)
        if not test_rc_ok(rc):
            ok = False
            outputs.append(f"[tests/{d}] rc={rc}\n{out}")
    results.append(StepResult("테스트", ok, "\n".join(outputs), tests=n, slow_skipped=skipped))
    if base is None:
        msg = "비교 기준을 못 구함(`git fetch origin` 뒤 다시 돌리거나 --base 지정)"
        results.append(StepResult("문서", False, msg))
        results.append(StepResult("증빙", False, msg))
        return results
    results += [StepResult(k, done[k][0] == 0, done[k][1]) for k in ("문서", "증빙")]
    return results


def _store(runner) -> PassStore | None:
    common = runner.git_common_dir()
    return PassStore(common / PASS_DIR_NAME) if common is not None else None


def record_pass(runner, rc: int, base: str | None, head_before: str | None, clean_before: bool,
                with_assets: bool, pr_body: str | None, store: PassStore | None = None,
                selected: bool = False) -> bool:
    """전체 통과면 통과 기록을 남긴다. 남겼으면 True.

    PR 본문 검사(`--pr-body`)는 본문 종류(P-xx)에 따라 push 때의 `check_evidence --changed`를 건너뛸 수 있으므로,
    그때는 같은 증빙 검사를 한 번 더 돌려 통과해야 기록한다.
    """
    if rc != 0 or base is None or head_before is None or not clean_before:
        return False
    push_ok = True
    if pr_body is not None:
        prc, _ = runner.run([sys.executable, "-m", "tools.check_evidence", "--changed", base])
        push_ok = prc == 0
    if not may_record(rc, push_ok, clean_before, runner.tree_clean(), head_before, runner.resolve("HEAD"), base):
        return False
    store = store or _store(runner)
    if store is None:
        return False
    flags = (("with-assets", with_assets), ("pr-body", pr_body is not None), ("selected", selected))
    mode = ",".join(m for m, on in flags if on) or "plain"
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        store.prune(time.time())
        store.write(PassRecord(head_before, base, mode, at))
    except OSError:
        return False
    return True


def check_pass(runner, sha: str, environ: dict[str, str],
               store: PassStore | None = None) -> tuple[bool, str]:
    """훅용: `sha` 검사를 생략해도 되는지. 지금 비교 기준은 `origin/dev`와 `sha`의 merge-base."""
    disabled = environ.get(NO_CACHE_ENV, "") not in ("", "0")
    if disabled:
        return skip_decision(None, sha, None, True)
    store = store or _store(runner)
    rec = store.read(sha) if store is not None else None
    current = runner.merge_base(DEFAULT_BASE_REF, sha) if rec is not None else None
    return skip_decision(rec, sha, current, False)


def select_plan(runner, base: str | None, with_assets: bool) -> tuple[Selection | None, str]:
    """테스트 대상(폴더별 테스트 모듈)과 설명. (None, 이유)면 전체 테스트.

    비교 기준이 없거나 바뀐 파일을 못 구하면 전체, 바뀐 파일이 없으면(dev 자체를 검사) 전체다(덜 줄이는 쪽).
    `with_assets`면 고른 결과와 무관하게 tests/assets 폴더 전체를 넣는다(명시 요청).
    """
    changed_files = getattr(runner, "changed_files", None)
    if base is None or changed_files is None:
        return None, "비교 기준 없음"
    changed = changed_files(base)
    if changed is None:
        return None, "바뀐 파일을 못 구함"
    if not changed:
        return None, "바뀐 파일 없음"
    selection, why = select_tests(changed, runner.python_sources(), runner.test_dirs())
    if selection is not None and not with_assets and ASSETS_DIR in selection:
        head = describe(selection)
        note = why[len(head):] if why.startswith(head) else ""  # Django 쪽 안내 등 덧붙인 말
        selection = {d: m for d, m in selection.items() if d != ASSETS_DIR}
        why = describe(selection) + note + "(tests/assets는 --with-assets로)"
    if selection is not None and with_assets and selection.get(ASSETS_DIR, []) is not None:
        # 명시적으로 요청한 에셋 테스트는 고르기와 무관하게 폴더 전체를 돈다
        head = describe(selection)
        note = why[len(head):] if why.startswith(head) else ""
        selection = dict(selection, **{ASSETS_DIR: None})
        why = describe(selection) + note
    return selection, why


def slow_plan(runner, base: str | None) -> tuple[dict[str, list[str]], str]:
    """전체 검사에서 뺄 무거운 테스트(폴더 → 테스트 id)와 설명. 바뀐 파일을 못 구하면 빼지 않는다(덜 줄이는 쪽)."""
    changed_files = getattr(runner, "changed_files", None)
    if base is None or changed_files is None:
        return {}, "모두 실행(비교 기준 없음)"
    changed = changed_files(base)
    if changed is None:
        return {}, "모두 실행(바뀐 파일을 못 구함)"
    if not changed:
        return {}, "모두 실행(바뀐 파일 없음)"
    selection, why = select_tests(changed, runner.python_sources(), runner.test_dirs())
    if selection is None:
        return {}, f"모두 실행({why})"
    skips = slow_skips(selection, True)
    n = sum(len(v) for v in skips.values())
    return skips, (f"{n}개 생략(바뀐 파일과 이어지지 않음, 모두 돌리려면 --all-slow)" if n
                   else "모두 실행(바뀐 파일과 이어짐)")


# ---------------------------------------------------------------- 표현
def main(argv: list[str] | None = None, runner=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1, 사용법 오류 2."""
    ap = argparse.ArgumentParser(prog="python -m tools.precheck", description="로컬 검사(README 3절 ②)")
    ap.add_argument("--base", help=f"비교 기준 커밋(기본: {DEFAULT_BASE_REF}와 HEAD의 merge-base)")
    ap.add_argument("--with-assets", action="store_true", help="tests/assets도 돌린다")
    ap.add_argument("--pr-body", metavar="FILE", help="PR 본문 파일(Task·Closes/Refs 검사)")
    ap.add_argument("--quick", action="store_true",
                    help="작업 중 확인: 기본과 같은 대상, 통과 기록 없음, PR 본문에 못 붙이는 요약 줄")
    ap.add_argument("--full-tests", action="store_true",
                    help="테스트 폴더 전체(무거운 테스트는 바뀐 파일과 이어질 때만). PM이 배포 후보 전 dev에서 1회")
    ap.add_argument("--all-slow", action="store_true",
                    help="테스트 전체와 무거운 테스트 모두(--full-tests 포함)")
    ap.add_argument("--check-pass", metavar="SHA",
                    help="훅용: 그 커밋의 통과 기록이 지금 비교 기준과 맞으면 0(생략), 아니면 1(전체 검사)")
    args = ap.parse_args(argv)
    runner = runner or SubprocessRunner()
    if args.check_pass:
        if args.base or args.with_assets or args.pr_body or args.quick or args.all_slow or args.full_tests:
            ap.error("--check-pass는 다른 인자와 함께 쓸 수 없다")
        skip, why = check_pass(runner, args.check_pass, dict(os.environ))
        print(f"pre-push: {args.check_pass[:7]} {why}")
        return 0 if skip else 1
    pr_body = None
    if args.pr_body:
        try:
            pr_body = Path(args.pr_body).read_text(encoding="utf-8")
        except OSError as exc:
            ap.error(f"PR 본문 파일을 읽지 못함: {exc}")
    started = time.monotonic()
    head_before = runner.resolve("HEAD")
    clean_before = getattr(runner, "tree_clean", lambda: False)()
    base = runner.resolve(args.base) if args.base else runner.merge_base(DEFAULT_BASE_REF)
    if args.quick and (args.full_tests or args.all_slow):
        ap.error("--quick은 --full-tests·--all-slow와 함께 쓸 수 없다")
    full = args.full_tests or args.all_slow
    only, why = (None, "--full-tests" if args.full_tests else "--all-slow") if full else \
        select_plan(runner, base, args.with_assets)
    label = why if only is not None else f"전체({why})"
    quick_target = label if args.quick else None
    selected = why if only is not None and not args.quick else None
    print(f"[{'빠른 검사' if args.quick else '테스트'}] 대상: {label}")
    skips: dict[str, list[str]] = {}
    if args.full_tests and not args.all_slow:  # 고른 실행은 고른 모듈 안의 무거운 테스트를 그대로 돈다
        skips, slow_why = slow_plan(runner, base)
        print(f"[무거운 테스트] {slow_why}")
    results = run_precheck(runner, base, with_assets=args.with_assets, pr_body=pr_body, only_tests=only,
                           skip_tests=skips)
    for r in results:
        print(f"[{'통과' if r.ok else '실패'}] {r.name}")
        if not r.ok and r.output.strip():
            print(r.output.rstrip())
    rc, line = summarize(results, base[:7] if base else None, time.monotonic() - started, quick_target, selected)
    if not args.quick:  # --quick은 통과 기록을 남기지 않는다(커밋 전 작업 트리 확인용)
        record_pass(runner, rc, base, head_before, clean_before, args.with_assets, pr_body,
                    selected=selected is not None)
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
