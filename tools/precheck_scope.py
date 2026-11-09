"""빠른 검사(`python -m tools.precheck --quick`, 11/9 PM 결정)의 대상 테스트 모듈 고르기.

작업 중에는 바뀐 파일과 이어진 테스트 모듈만 돌리고, PR 직전과 push 때는 전체 검사를 한 번 돈다.

- `.py` 변경: 저장소 전체 `.py`의 import 관계를 거슬러 올라가 그 모듈을 (간접으로라도) 쓰는 테스트 파일을 고른다.
  `"-m", "tools.x"`처럼 하위 명령으로 부르는 점 이름 문자열과 `tools/x.py` 같은 경로 문자열도 import로 본다.
  폴더를 통째로 훑는 테스트(의존 방향 검사 `tests/contract/test_layers.py` 등)를 위해, 바뀐 파일의 상위 폴더 이름
  (`"engine"`, `"server/app"`)을 문자열 상수로 가진 테스트 파일도 고른다.
  precheck 테스트가 하나도 쓰지 않는 `server/` 모듈은 Django 쪽으로 보고(전체 검사에서도 돌지 않고 `server/tests`가 검사)
  전체로 넘기지 않는다. 그 밖에 쓰는 테스트가 없는 모듈은 전체로 돈다.
- `.md`와 `docs/` 아래 변경(증빙 수치 JSON 포함): 문서를 읽는 테스트 파일(본문에 `docs`·`README`가 있는 것)을 고른다.
- `web/` 아래 비 `.py` 변경(템플릿·정적 파일): 화면 파일을 읽는 테스트 파일(본문에 `web/`·`templates`·`"web"`이 있는 것).
  Django 렌더링 테스트(`server/tests`)는 전체 검사에도 없다.
- `server/tests/` 아래 변경(precheck가 돌리지 않는 Django·JS 테스트): `.py`는 가져다 쓰는 테스트, 그 밖의 파일은
  경로나 파일 이름을 적은 테스트만 고르고, 없으면 테스트를 돌리지 않는다(테스트 코드라 쓰는 곳이 없는 것이 정상).
- `tests/<폴더>/` 아래 변경: 그 테스트 파일과 그것을 가져다 쓰는 테스트(데이터 파일·`__init__.py`면 그 폴더 전체,
  지운 테스트 파일은 가져다 쓰던 테스트만).
- 결과는 폴더별 테스트 모듈 목록이다. `__init__.py`에 `load_tests`가 있는 폴더(서비스 테스트 거르기)는 그 규칙을 지키려고 폴더 전체로 돈다.
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
SERVER_PKG = "server."          # precheck 테스트가 쓰지 않는 server 모듈은 Django 쪽(server/tests에서 검사)
_DIR_LITERAL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:/[A-Za-z_][A-Za-z0-9_]*)*$")


def dir_literals(source: str) -> set[str]:
    """소스의 문자열 상수 중 폴더 경로처럼 생긴 것(`"engine"`, `"server/app"`). 폴더를 훑는 테스트를 찾는 데 쓴다.
    구문 오류면 빈 집합."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {n.value.strip("/") for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and _DIR_LITERAL_RE.match(n.value.strip("/"))}


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


def _is_test_file(path: str) -> bool:
    """unittest 기본 탐색 패턴(`test*.py`)에 걸리는 파일인지."""
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test") and name.endswith(".py")


def _prefixes(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts) + 1)]


Selection = dict[str, list[str] | None]  # 테스트 폴더 → 돌릴 테스트 모듈(점 이름). None이면 폴더 전체 탐색


def describe(selection: Selection) -> str:
    """선택 → 한 줄 설명(`tests/engine 3개·tests/ads 전체`). 비었으면 `없음`."""
    parts = [f"tests/{d} " + ("전체" if mods is None else f"{len(mods)}개") for d, mods in sorted(selection.items())]
    return "·".join(parts) or "없음"


def select_tests(changed: list[str], sources: dict[str, str],
                 test_dirs: list[str]) -> tuple[Selection | None, str]:
    """빠른 검사 대상 테스트(폴더별 테스트 모듈)와 설명. 전체로 돌아야 하면 (None, 이유).

    `changed`는 저장소 상대 경로(`/` 구분), `sources`는 저장소 `.py` 경로 → 소스, `test_dirs`는 `tests/` 아래 폴더 이름.
    패키지 `__init__.py`에 `load_tests`가 있는 폴더(서비스 테스트 거르기 등)는 그 규칙을 지키려고 폴더 전체 탐색으로 돈다.
    """
    if not changed:
        return {}, "바뀐 파일 없음"
    known = set(test_dirs)
    files: set[str] = set()          # 돌릴 테스트 파일 경로
    whole: set[str] = set()          # 폴더 전체로 돌 테스트 폴더
    targets: set[str] = set()        # 쓰는 테스트를 못 찾으면 전체로 도는 모듈
    test_mods: dict[str, str] = {}   # 테스트 코드 모듈 → 경로(쓰는 곳이 없어도 전체로 넘기지 않음)
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
            mod = module_name(path)
            # 데이터 파일·패키지 초기화 파일(테스트 모듈이 import 없이 실행): 그 폴더 전체
            if mod is None or path.endswith("/__init__.py"):
                whole.add(d)
                continue
            if _is_test_file(path) and path in sources:  # 지우거나 이름을 바꾼 옛 경로는 직접 돌리지 않는다
                files.add(path)
            test_mods[mod] = path
            continue
        if path.startswith(SERVER_TESTS):
            if module_name(path):
                test_mods[module_name(path)] = path
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

    test_files = {p: src for p, src in sources.items() if _test_dir_of(p, known) and _is_test_file(p)}
    for group in hints:
        files |= {p for p, src in test_files.items() if any(h in src for h in group)}

    django_only: list[str] = []
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
                            if p in test_files:
                                reached.add(p)
                affected |= nxt
                frontier = nxt
            return reached

        # 폴더를 통째로 훑는 테스트(의존 방향 검사 등): 상위 폴더 이름을 문자열 상수로 적은 테스트 파일
        scanners = [(p, dir_literals(src)) for p, src in test_files.items()]

        def scanned_by(path: str) -> set[str]:
            folders = {"/".join(path.split("/")[:i]) for i in range(1, path.count("/") + 1)}
            return {p for p, lits in scanners if folders & lits}

        paths = {module_name(p): p for p in changed if module_name(p)}
        for t in sorted(targets):  # 모듈마다 따로 본다: 하나라도 쓰는 테스트가 없으면 전체
            reached = reached_from(t) | scanned_by(paths[t])
            if not reached:
                if t.startswith(SERVER_PKG):  # Django 쪽: server/tests만 검사하고 전체 검사에서도 돌지 않는다
                    django_only.append(t)
                    continue
                return None, f"바뀐 모듈을 쓰는 테스트를 못 찾음({t})"
            files |= reached
        for t, path in test_mods.items():
            files |= reached_from(t) | scanned_by(path)

    selection: Selection = {d: None for d in whole}
    for p in files:
        d = _test_dir_of(p, known)
        if d is None or d in whole:
            continue
        if "def load_tests" in sources.get(f"tests/{d}/__init__.py", ""):
            selection[d] = None
            continue
        mods = selection.setdefault(d, [])
        if mods is not None:
            mods.append(module_name(p) or "")
    for d, mods in selection.items():
        if mods is not None:
            mods.sort()
    note = f"(Django 쪽 {len(django_only)}개는 server/tests에서)" if django_only else ""
    return selection, describe(selection) + note
