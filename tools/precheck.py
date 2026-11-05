"""로컬 검사 한 번에 돌리기(11/5 PM 결정: GitHub Actions CI 폐지, README 3절 ②).

    python -m tools.precheck                      # 구조·비밀·단위 테스트(tests/assets 제외)·문서·증빙
    python -m tools.precheck --with-assets        # tests/assets도 포함(에셋 관련 파일을 바꿨을 때)
    python -m tools.precheck --pr-body pr.md      # PR 본문 파일까지 검사(Task·Closes/Refs 문법, Closes면 --complete)
    python -m tools.precheck --base <커밋>         # 비교 기준 직접 지정(기본: origin/dev와 HEAD의 merge-base)
    python -m tools.precheck --base origin/main   # 승격 PR(dev → main): dev에서 main 이후 바뀐 증빙 검사

`git push` 때 `.githooks/pre-push`가 이 명령을 부른다(설치: `git config core.hooksPath .githooks`).
하나라도 실패하면 종료 코드 1이고, 마지막 줄에 한 줄 요약(`precheck: 통과 …` 또는 `precheck: 실패 …`)을 낸다.
PR 본문에는 이 마지막 줄을 붙인다.

비교 기준을 못 구하면(원격 `origin/dev`를 받아 오지 않은 새 clone 등) 문서·증빙 검사를 할 수 없으므로
건너뛰지 않고 실패로 본다. 검사 없이 통과 줄이 나오는 것을 막기 위해서다. `git fetch origin` 뒤 다시 돌리거나 `--base`를 준다.

계층: `StepResult`·`test_rc_ok`·`count_tests`·`summarize`(도메인, 문자열·숫자만) /
`SubprocessRunner`(인프라, 하위 명령 실행) / `run_precheck`(응용, 순서 조립) / `main`(표현).
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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_REF = "origin/dev"
ASSETS_DIR = "assets"
PR_BODY_ENV = "PRECHECK_PR_BODY"
_RAN_RE = re.compile(r"^Ran (\d+) tests?\b", re.M)


# ---------------------------------------------------------------- 도메인
@dataclass(frozen=True)
class StepResult:
    """검사 단계 하나의 결과. `tests`는 단위 테스트 단계에서만 센 테스트 수."""

    name: str
    ok: bool
    output: str = ""
    tests: int = 0


def test_rc_ok(rc: int) -> bool:
    """unittest discover 종료 코드 판정. 0 = 통과, 5 = 테스트 0개(폴더만 있음)도 통과."""
    return rc in (0, 5)


def count_tests(output: str) -> int:
    """unittest 출력의 `Ran N tests`를 모두 더한다. 없으면 0."""
    return sum(int(n) for n in _RAN_RE.findall(output))


def summarize(results: list[StepResult], base_short: str | None, seconds: float) -> tuple[int, str]:
    """단계 결과 → (종료 코드, 한 줄 요약). 하나라도 실패면 1."""
    failed = [r.name for r in results if not r.ok]
    base = base_short or "없음"
    if failed:
        return 1, f"precheck: 실패 ({', '.join(failed)}; base {base}, {seconds:.0f}초)"
    n = sum(r.tests for r in results)
    names = "·".join(r.name for r in results)
    return 0, f"precheck: 통과 ({names}, 테스트 {n}개, base {base}, {seconds:.0f}초)"


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

    def merge_base(self, ref: str) -> str | None:
        """`ref`와 HEAD의 merge-base 전체 SHA. 못 구하면 None."""
        rc, out = self.run(["git", "merge-base", ref, "HEAD"])
        return out.strip() if rc == 0 and out.strip() else None

    def resolve(self, ref: str) -> str | None:
        """커밋 이름 → 전체 SHA. 없으면 None."""
        rc, out = self.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        return out.strip() if rc == 0 and out.strip() else None

    def test_dirs(self) -> list[str]:
        """`tests/` 아래 패키지 폴더 이름(`__init__.py`가 있는 것), 정렬."""
        base = self.root / "tests"
        return sorted(p.name for p in base.iterdir() if p.is_dir() and (p / "__init__.py").exists())


# ---------------------------------------------------------------- 응용
def run_precheck(runner, base: str | None, with_assets: bool = False,
                 pr_body: str | None = None, workers: int | None = None) -> list[StepResult]:
    """구조·비밀·단위 테스트(폴더별)·문서·증빙을 동시에 돌리고 결과는 이 순서로 모은다.

    서로 독립인 하위 명령이라 병렬로 돌린다(11/5: 순서대로 돌리면 약 90초). 앞 단계가 실패해도 끝까지 돈다.
    """
    py = sys.executable
    jobs: dict[str, tuple[list[str], dict[str, str] | None]] = {
        "구조": ([py, "-m", "tools.check_structure", "--all"], None),
        "비밀": ([py, "-m", "tools.check_secrets"], None),
    }
    test_dirs = [d for d in runner.test_dirs() if with_assets or d != ASSETS_DIR]
    for d in test_dirs:
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
    outputs, ok, n = [], True, 0
    for d in test_dirs:
        rc, out = done[f"tests/{d}"]
        n += count_tests(out)
        if not test_rc_ok(rc):
            ok = False
            outputs.append(f"[tests/{d}] rc={rc}\n{out}")
    results.append(StepResult("테스트", ok, "\n".join(outputs), tests=n))
    if base is None:
        msg = "비교 기준을 못 구함(`git fetch origin` 뒤 다시 돌리거나 --base 지정)"
        results.append(StepResult("문서", False, msg))
        results.append(StepResult("증빙", False, msg))
        return results
    results += [StepResult(k, done[k][0] == 0, done[k][1]) for k in ("문서", "증빙")]
    return results


# ---------------------------------------------------------------- 표현
def main(argv: list[str] | None = None, runner=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1, 사용법 오류 2."""
    ap = argparse.ArgumentParser(prog="python -m tools.precheck", description="로컬 검사(README 3절 ②)")
    ap.add_argument("--base", help=f"비교 기준 커밋(기본: {DEFAULT_BASE_REF}와 HEAD의 merge-base)")
    ap.add_argument("--with-assets", action="store_true", help="tests/assets도 돌린다")
    ap.add_argument("--pr-body", metavar="FILE", help="PR 본문 파일(Task·Closes/Refs 검사)")
    args = ap.parse_args(argv)
    runner = runner or SubprocessRunner()
    pr_body = None
    if args.pr_body:
        try:
            pr_body = Path(args.pr_body).read_text(encoding="utf-8")
        except OSError as exc:
            ap.error(f"PR 본문 파일을 읽지 못함: {exc}")
    started = time.monotonic()
    base = runner.resolve(args.base) if args.base else runner.merge_base(DEFAULT_BASE_REF)
    results = run_precheck(runner, base, with_assets=args.with_assets, pr_body=pr_body)
    for r in results:
        print(f"[{'통과' if r.ok else '실패'}] {r.name}")
        if not r.ok and r.output.strip():
            print(r.output.rstrip())
    rc, line = summarize(results, base[:7] if base else None, time.monotonic() - started)
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
