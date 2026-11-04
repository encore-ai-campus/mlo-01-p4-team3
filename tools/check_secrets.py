"""비밀정보 검사(공통 완료 정의 ⑥, E-05-02 조건 5).

    python -m tools.check_secrets

Git이 추적하는 텍스트 파일(`git ls-files`, 바이너리 제외, `tests/fixtures/secrets/` 제외)에서
사설 IP, 개인 키 헤더, "비밀 키 이름 + 문자열 값"을 찾으면 파일:줄과 이유를 출력하고 실패한다.
비밀이 아닌데 걸리는 값은 `tools/secrets_allowlist.txt`(`경로 | 키 이름 | 사유`)에 적는다.
목록에 있지만 더 이상 걸리지 않는 항목(죽은 예외)도 실패다.

계층: `scan_text`·`parse_allowlist`·`apply_allowlist`(도메인) / `GitRepo`(인프라) / `run`(응용) / `main`(표현).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass

from tools.gitrepo import GitRepo
from tools.jsonlog import log

ALLOWLIST_PATH = "tools/secrets_allowlist.txt"
EXEMPT_PREFIX = "tests/fixtures/secrets/"
CODE_SUFFIXES = (".py",)
SECRET_LAST = {"password", "passwd", "pwd", "secret", "token", "apikey", "credential", "credentials"}
SECRET_LAST_TWO = {"api_key", "access_key", "private_key", "secret_key", "signing_key", "hmac_key"}

_IP_RE = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d]|\.\d)")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
_KEY = r"[A-Za-z_][A-Za-z0-9_.-]*"
# 따옴표 문자열 값: (키)(따옴표)?\s*[:=]\s*(접두)(따옴표)(값)(같은 따옴표)
_QUOTED_RE = re.compile(
    rf"""(?<![A-Za-z0-9_.-])(["']?)({_KEY})\1\s*(?::|=|:=)\s*([rRbBfFuU]{{0,2}})(["'])(.*?)(?<!\\)\4"""
)
# 줄 전체가 KEY=값 / KEY: 값(따옴표 없음). 앞의 `export `, YAML 목록 `- `는 허용
_BARE_RE = re.compile(rf"""^\s*(?:export\s+|-\s+)?({_KEY})\s*[:=]\s*([^\s"'#][^#]*?)\s*(?:\s#.*)?$""")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


@dataclass(frozen=True)
class Finding:
    """걸린 줄 하나. key는 키 이름 규칙일 때만 있다."""

    path: str
    line: int
    reason: str
    key: str | None = None

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.reason}"


def key_pieces(key: str) -> list[str]:
    """`_`·`-`·`.`·camelCase 경계로 나눈 소문자 조각."""
    pieces: list[str] = []
    for part in re.split(r"[_.\-]+", key):
        pieces += [p.lower() for p in _CAMEL_RE.split(part) if p]
    return pieces


def is_secret_key(key: str) -> bool:
    """마지막 조각이 비밀 이름이거나 마지막 두 조각이 api_key 등이면 참(부분 문자열 일치 아님)."""
    pieces = key_pieces(key)
    if not pieces:
        return False
    if pieces[-1] in SECRET_LAST:
        return True
    return len(pieces) >= 2 and f"{pieces[-2]}_{pieces[-1]}" in SECRET_LAST_TWO


def is_allowed_value(value: str) -> bool:
    """빈 값, `${VAR}`, `<자리표시자>`, `{`가 든 템플릿 값·보간 f-string은 허용."""
    v = value.strip()
    # f-string은 `{`(보간)가 있을 때만 허용. 보간 없는 상수 f-string은 일반 문자열과 같이 검사한다(11/4 PM 결정)
    return not v or "{" in v or bool(re.fullmatch(r"<[^<>]+>", v))


def _ip_is_private(octets: tuple[int, ...]) -> bool:
    a, b = octets[0], octets[1]
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def scan_text(path: str, text: str) -> list[Finding]:
    """한 파일의 내용에서 걸리는 줄을 찾는다(예외 목록은 적용하지 않는다)."""
    found: list[Finding] = []
    code = path.endswith(CODE_SUFFIXES)  # 코드에서 따옴표 없는 값은 변수·호출·타입 표기다
    for no, line in enumerate(text.splitlines(), 1):
        for m in _IP_RE.finditer(line):
            octets = tuple(int(g) for g in m.groups())
            if all(o <= 255 for o in octets) and _ip_is_private(octets):
                found.append(Finding(path, no, f"사설 IP {m.group(0)}"))
        if _PRIVATE_KEY_RE.search(line):
            found.append(Finding(path, no, "개인 키 헤더"))
        keys_on_line: set[str] = set()
        for m in _QUOTED_RE.finditer(line):
            key, value = m.group(2), m.group(5)
            if is_secret_key(key) and not is_allowed_value(value):
                keys_on_line.add(key)
                found.append(Finding(path, no, f"비밀 키 {key}에 문자열 값", key))
        bm = None if code else _BARE_RE.match(line)
        if bm and bm.group(1) not in keys_on_line:
            key, value = bm.group(1), bm.group(2)
            if is_secret_key(key) and not is_allowed_value(value):
                found.append(Finding(path, no, f"비밀 키 {key}에 값", key))
    return found


@dataclass(frozen=True)
class AllowEntry:
    """예외 목록 한 줄."""

    path: str
    key: str
    reason: str
    line: int


def parse_allowlist(text: str) -> tuple[list[AllowEntry], list[str]]:
    """`경로 | 키 이름 | 사유`. (항목, 형식 오류). `#`은 주석, 와일드카드·폴더 경로 금지."""
    entries: list[AllowEntry] = []
    errors: list[str] = []
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or not all(parts):
            errors.append(f"{ALLOWLIST_PATH}:{no}: `경로 | 키 이름 | 사유` 형식이 아님")
            continue
        path, key, reason = parts
        if any(ch in path for ch in "*?[") or path.endswith("/"):
            errors.append(f"{ALLOWLIST_PATH}:{no}: 와일드카드·폴더 경로는 쓸 수 없음: {path}")
            continue
        entries.append(AllowEntry(path, key, reason, no))
    return entries, errors


def apply_allowlist(findings: list[Finding], entries: list[AllowEntry]) -> tuple[list[Finding], list[str]]:
    """(남은 발견, 죽은 예외 오류). 예외는 그 파일의 그 키 이름만 값과 관계없이 허용한다."""
    allowed = {(e.path, e.key) for e in entries}
    used: set[tuple[str, str]] = set()
    remaining = []
    for f in findings:
        if f.key is not None and (f.path, f.key) in allowed:
            used.add((f.path, f.key))
        else:
            remaining.append(f)
    dead = [f"{ALLOWLIST_PATH}:{e.line}: 더 이상 걸리지 않는 예외(죽은 예외): {e.path} | {e.key}"
            for e in entries if (e.path, e.key) not in used]
    return remaining, dead


def decode_text(data: bytes) -> str | None:
    """텍스트면 문자열, 바이너리(NUL 포함 또는 UTF-8 아님)면 None."""
    if b"\0" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def run(repo) -> tuple[list[Finding], list[str], int]:
    """저장소 전체 검사. (남은 발견, 예외 목록 오류, 검사한 파일 수)."""
    findings: list[Finding] = []
    scanned = 0
    for path in repo.tracked_files():
        if path.startswith(EXEMPT_PREFIX):
            continue
        data = repo.read_bytes(path)
        text = decode_text(data) if data is not None else None
        if text is None:
            continue
        scanned += 1
        findings += scan_text(path, text)
    allow_text = repo.read_text(ALLOWLIST_PATH) or ""
    entries, errors = parse_allowlist(allow_text)
    remaining, dead = apply_allowlist(findings, entries)
    return remaining, errors + dead, scanned


def main(argv: list[str] | None = None, repo=None) -> int:
    """명령줄 진입점. 통과 0, 실패 1."""
    ap = argparse.ArgumentParser(prog="python -m tools.check_secrets", description="비밀정보 검사(공통 ⑥)")
    ap.parse_args(argv)
    repo = repo or GitRepo()
    started = time.monotonic()
    remaining, errors, scanned = run(repo)
    for f in remaining:
        print(f"FAIL {f}")
    for e in errors:
        print(f"FAIL {e}")
    total = len(remaining) + len(errors)
    print(f"check_secrets: 파일 {scanned}개 검사, {'통과' if not total else f'실패 {total}건'}")
    extra = {"duration_ms": int((time.monotonic() - started) * 1000), "files": scanned, "failures": total}
    if total:
        log("check_secrets", "error", "비밀정보 검사 실패", error_type="CheckFailed", **extra)
    else:
        log("check_secrets", "info", "비밀정보 검사 통과", **extra)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
