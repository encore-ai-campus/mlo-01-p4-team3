"""빠른 검사(`python -m tools.precheck --quick`, 11/9 PM 결정)의 대상 테스트 폴더 고르기.

작업 중에는 바뀐 파일과 이어진 테스트 폴더만 돌리고, PR 직전과 push 때는 전체 검사를 한 번 돈다.

- `.py` 변경: 저장소 전체 `.py`의 import 관계를 거슬러 올라가 그 모듈을 (간접으로라도) 쓰는 테스트 폴더를 고른다.
  `"-m", "tools.x"`처럼 하위 명령으로 부르는 점 이름 문자열과 `tools/x.py` 같은 경로 문자열도 import로 본다.
- `.md`와 `docs/` 아래 변경(증빙 수치 JSON 포함): 문서를 읽는 테스트 파일(본문에 `docs`·`README`가 있는 것)의 폴더를 고른다.
- `web/` 아래 비 `.py` 변경(템플릿·정적 파일): 화면 파일을 읽는 테스트 파일(본문에 `web/`·`templates`·`"web"`이 있는 것)의 폴더.
  Django 렌더링 테스트(`server/tests`)는 전체 검사에도 없다.
- `server/tests/` 아래 변경(precheck가 돌리지 않는 Django·JS 테스트): `.py`는 가져다 쓰는 테스트 폴더, 그 밖의 파일은
  경로나 파일 이름을 적은 테스트 폴더만 고르고, 없으면 테스트를 돌리지 않는다(테스트 코드라 쓰는 곳이 없는 것이 정상).
- `tests/<폴더>/` 아래 변경: 그 폴더.
- 전체 테스트로 도는 경우(덜 줄이는 쪽): 공용 파일(`QUICK_FULL_PATHS`), 저장소 루트 파일(`README.md` 제외),
  테스트 루트·`tests/fixtures`, 그 밖의 비 `.py` 파일(콘텐츠·설정·데이터), 쓰는 테스트를 하나도 못 찾은 `.py` 변경.

계층: 도메인(문자열·구문 트리만, 파일·git 접근 없음). 표준 라이브러리만 쓴다.
저장소 밖 사본 `githooks_local/precheck_quick.py`가 이 규칙을 그대로 옮겨 쓴다(병합 전 임시). 규칙을 바꾸면 사본도 맞춘다.
"""
from __future__ import annotations

import ast
import re

# 바뀌면 전체 테스트로 도는 공용 경로. 끝이 `/`면 폴더 접두어, 아니면 파일 하나.
QUICK_FULL_PATHS = (
    "tools/precheck.py", "tools/precheck_scope.py", "tests/fixtures/", "tests/__init__.py",
    ".githooks/", "requirements/", ".github/",
)
_MODULE_STR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_DOC_HINTS = ("docs", "README")
_WEB_HINTS = ("web/", "templates", '"web"')
SERVER_TESTS = "server/tests/"  # precheck가 돌리지 않는 Django·JS 테스트 폴더


def module_name(path: str) -> str | None:
    """저장소 상대 `.py` 경로 → 모듈 이름(`a/b/__init__.py` → `a.b`). `.py`가 아니면 None."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def import_names(source: str, module: str, is_package: bool = False) -> set[str]:
    """소스가 가져다 쓰는 모듈 이름 후보. 상대 import는 절대 이름으로 푼다.

    `from a.b import c`는 `a.b`와 `a.b.c`를 모두 넣는다(c가 모듈인지 이름인지 소스만으로는 모른다).
    점 이름 문자열(`"tools.check_docs"`)과 `.py` 경로 문자열(`"tools/x.py"`)도 넣는다.
    구문 오류면 빈 집합(그 파일 자체가 바뀌었다면 그 테스트가 잡는다).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    pkg = module.split(".") if is_package else module.split(".")[:-1]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            if base:
                out.add(base)
            out.update(f"{base}.{a.name}" if base else a.name for a in node.names if a.name != "*")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value.strip()
            if _MODULE_STR_RE.match(v):
                out.add(v)
            elif v.endswith(".py") and "/" in v and " " not in v:
                m = module_name(v.lstrip("./"))
                if m:
                    out.add(m)
    return out


def _test_dir_of(path: str, known: set[str]) -> str | None:
    parts = path.split("/")
    return parts[1] if len(parts) >= 3 and parts[0] == "tests" and parts[1] in known else None


def _prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


def select_test_dirs(changed: list[str], sources: dict[str, str],
                     test_dirs: list[str]) -> tuple[list[str] | None, str]:
    """빠른 검사 대상 테스트 폴더와 설명. 전체로 돌아야 하면 (None, 이유).

    `changed`는 저장소 상대 경로(`/` 구분), `sources`는 저장소 `.py` 경로 → 소스, `test_dirs`는 `tests/` 아래 폴더 이름.
    """
    if not changed:
        return [], "바뀐 파일 없음"
    known = set(test_dirs)
    picked: set[str] = set()
    targets: set[str] = set()       # 쓰는 테스트를 못 찾으면 전체로 도는 모듈
    test_mods: set[str] = set()     # 테스트 폴더 안 모듈: 자기 폴더는 이미 골랐고, 다른 폴더가 가져다 쓰면 그 폴더도
    hints: set[tuple[str, ...]] = set()
    for path in changed:
        if any(path == f or (f.endswith("/") and path.startswith(f)) for f in QUICK_FULL_PATHS):
            return None, f"공용 파일 {path}"
        if "/" not in path and path != "README.md":
            return None, f"저장소 루트 파일 {path}"
        if path.split("/")[0] == "tests":
            d = _test_dir_of(path, known)
            if d is None:
                return None, f"테스트 공용 위치 {path}"
            picked.add(d)
            if module_name(path):
                test_mods.add(module_name(path))
            continue
        if path.startswith(SERVER_TESTS):
            if module_name(path):
                test_mods.add(module_name(path))
            else:
                hints.add((path, path.rsplit("/", 1)[-1]))
            continue
        if path.endswith(".md") or path.startswith("docs/"):
            hints.add(_DOC_HINTS)
            continue
        if path.startswith("web/") and not path.endswith(".py"):
            hints.add(_WEB_HINTS)
            continue
        mod = module_name(path)
        if mod is None:
            return None, f"코드가 아닌 파일 {path}"
        targets.add(mod)

    for group in hints:
        for path, src in sources.items():
            d = _test_dir_of(path, known)
            if d and any(h in src for h in group):
                picked.add(d)
    if targets or test_mods:
        modules = {p: module_name(p) for p in sources}
        # 이름(과 그 점 접두어) → 그 이름을 가져다 쓰는 파일. `t`를 쓴다 = 가져다 쓰는 이름이 `t`이거나 `t.`로 시작.
        importers: dict[str, set[str]] = {}
        for p, src in sources.items():
            for n in import_names(src, modules[p] or "", p.endswith("__init__.py")):
                for pre in _prefixes(n):
                    importers.setdefault(pre, set()).add(p)

        def reached_from(start: str) -> set[str]:
            affected, frontier, reached = {start}, {start}, set()
            while frontier:
                nxt: set[str] = set()
                for t in frontier:
                    for p in importers.get(t, ()):
                        m = modules[p]
                        if m and m not in affected:
                            nxt.add(m)
                            d = _test_dir_of(p, known)
                            if d:
                                reached.add(d)
                affected |= nxt
                frontier = nxt
            return reached

        for t in sorted(targets):  # 모듈마다 따로 본다: 하나라도 쓰는 테스트가 없으면 전체
            reached = reached_from(t)
            if not reached:
                return None, f"바뀐 모듈을 쓰는 테스트를 못 찾음({t})"
            picked |= reached
        for t in test_mods:
            picked |= reached_from(t)
    return sorted(picked), "·".join(f"tests/{d}" for d in sorted(picked)) or "없음"
