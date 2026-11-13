"""디렉터리 트리 검사(`docs/contracts/structure.md` 6절 "허용 폴더(검사 기준)").

    python -m tools.check_structure --all            # 추적 파일이 있는 모든 폴더
    python -m tools.check_structure --diff <base>    # base 이후 새로 생긴 폴더만(커밋 전·추적 안 된 새 파일 포함)

패턴은 한 줄에 하나다. `dir/` = 그 폴더만, `dir/**` = 그 폴더와 아래 전부. 루트 파일은 늘 허용한다.
무시할 경로는 `.gitignore`가 정한다(git이 보는 파일만 검사).

계층: 규칙 함수(도메인, 문자열·목록만 받음: `parse_patterns`, `is_allowed`, `nearest_pattern`,
`dirs_of`, `check_dirs`) / `GitRepo`(인프라, `tools/gitrepo.py`) / `check_all`·`check_diff`(응용) /
`main`(표현). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time

from tools.gitrepo import GitError, GitRepo
from tools.jsonlog import log

CONTRACT = "docs/contracts/structure.md"
SECTION_TITLE = "허용 폴더(검사 기준)"


class PatternError(ValueError):
    """허용 패턴 절을 읽지 못함."""


# --- 규칙(도메인) ---
def parse_patterns(text: str) -> list[str]:
    """계약 문서에서 `허용 폴더(검사 기준)` 절의 첫 펜스 블록을 읽어 패턴 목록을 돌려준다."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("#") and SECTION_TITLE in l), None)
    if start is None:
        raise PatternError(f"'{SECTION_TITLE}' 절이 없음")
    patterns: list[str] = []
    inside = False
    for line in lines[start + 1:]:
        if not inside and re.match(r"#{1,6} ", line):
            break  # 펜스 블록 전에 다음 절이 나옴
        if line.strip().startswith("```"):
            if inside:
                break
            inside = True
            continue
        if not inside:
            continue
        item = line.split("#", 1)[0].strip()
        if not item:
            continue
        if not re.fullmatch(r"[^\s*]+/(\*\*)?", item) or item.startswith("/"):
            raise PatternError(f"패턴 형식이 아님: {item!r}(`dir/` 또는 `dir/**`)")
        patterns.append(item)
    if not inside:
        raise PatternError(f"'{SECTION_TITLE}' 절에 펜스 블록이 없음")
    if not patterns:
        raise PatternError(f"'{SECTION_TITLE}' 절의 패턴이 비어 있음")
    return patterns


def _match(directory: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        base = pattern[:-3]
        return directory == base or directory.startswith(base + "/")
    return directory == pattern[:-1]


def is_allowed(directory: str, patterns: list[str]) -> bool:
    """폴더(루트 기준 `/` 구분, 끝 `/` 없음) 하나가 허용되는지. 루트("")는 늘 허용."""
    return directory == "" or any(_match(directory, p) for p in patterns)


def nearest_pattern(directory: str, patterns: list[str]) -> str | None:
    """안내용: 앞부분 폴더가 가장 많이 겹치는 패턴. 하나도 안 겹치면 None."""
    parts = directory.split("/")
    best, best_len = None, 0
    for p in patterns:
        p_parts = p.rstrip("*").rstrip("/").split("/")
        n = 0
        for a, b in zip(parts, p_parts):
            if a != b:
                break
            n += 1
        if n > best_len or (n == best_len and n and best is not None and len(p) < len(best)):
            best, best_len = p, n
    return best


def dirs_of(paths: list[str]) -> set[str]:
    """파일 경로 목록이 들어 있는 폴더와 그 모든 상위 폴더(루트 제외)."""
    dirs: set[str] = set()
    for path in paths:
        parts = path.replace("\\", "/").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return dirs


def check_dirs(dirs: set[str] | list[str], patterns: list[str]) -> list[str]:
    """허용되지 않은 폴더마다 오류 문장 하나. 상위 폴더가 이미 걸리면 그 아래는 따로 적지 않는다."""
    bad = sorted(d for d in set(dirs) if not is_allowed(d, patterns))
    reported: list[str] = []
    errors: list[str] = []
    for d in bad:
        if any(d.startswith(r + "/") for r in reported):
            continue
        reported.append(d)
        near = nearest_pattern(d, patterns)
        hint = f"가장 가까운 허용 패턴 `{near}`" if near else "루트에 새 폴더"
        errors.append(f"허용되지 않은 폴더 {d}/ ({hint}). 새 폴더는 PM 승인 후 {CONTRACT} 6절부터 고친다")
    return errors


# --- 응용 ---
def load_patterns(repo) -> list[str]:
    """저장소의 계약 문서에서 허용 패턴을 읽는다. 문서가 없거나 절을 못 읽으면 `PatternError`."""
    text = repo.read_text(CONTRACT)
    if text is None:
        raise PatternError(f"{CONTRACT} 없음")
    return parse_patterns(text)


def check_all(repo) -> list[str]:
    """추적 파일이 있는 모든 폴더."""
    return check_dirs(dirs_of(repo.tracked_files()), load_patterns(repo))


def check_diff(base: str, repo) -> list[str]:
    """`base` 이후 추가된 파일의 폴더 중 base 시점에 없던 폴더만."""
    patterns = load_patterns(repo)
    added = [path for status, path in repo.changed_files(base) if status == "A"]
    new_dirs = {d for d in dirs_of(added) if not repo.dir_existed(base, d)}
    return check_dirs(new_dirs, patterns)


# --- 표현 ---
def main(argv: list[str] | None = None, repo=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1."""
    ap = argparse.ArgumentParser(prog="python -m tools.check_structure",
                                 description="디렉터리 트리 검사(structure.md 6절)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="추적 파일의 모든 폴더")
    mode.add_argument("--diff", metavar="BASE", help="BASE 이후 새로 생긴 폴더만")
    args = ap.parse_args(argv)
    repo = repo or GitRepo()
    started = time.monotonic()
    target = "--all" if args.all else f"--diff {args.diff}"
    try:
        errors = check_all(repo) if args.all else check_diff(args.diff, repo)
    except GitError as exc:  # 없는 base 커밋 등. 검사를 못 했으면 실패로 본다
        errors = [f"git 오류로 검사하지 못함: {exc}"]
    except PatternError as exc:
        errors = [f"{CONTRACT} 허용 패턴을 읽지 못함: {exc}"]
    for e in errors:
        print(f"FAIL {e}")
    print(f"check_structure {target}: {'통과' if not errors else f'실패 {len(errors)}건'}")
    extra = {"duration_ms": int((time.monotonic() - started) * 1000), "target": target, "failures": len(errors)}
    if errors:
        log("check_structure", "error", "디렉터리 트리 검사 실패", error_type="CheckFailed", **extra)
    else:
        log("check_structure", "info", "디렉터리 트리 검사 통과", **extra)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
