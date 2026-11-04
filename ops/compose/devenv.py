"""로컬 개발 환경(ops/compose/dev.yml)의 규칙 부분: .env 생성 규칙, 점검 판정, S3 서명, 로그 레코드.

외부 호출(docker, HTTP, 파일)은 하지 않는다. 그 부분은 `ops/compose/run.py`가 맡는다.
표준 라이브러리만 쓴다(Python 3.12·3.14 모두).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import quote

PROJECT = "paldo-dev"
SERVICE_NAME = "devenv"
PLACEHOLDER_RE = re.compile(r"<([A-Z][A-Z0-9_]*)>")
GARAGE_KEY_ID_RE = re.compile(r"^GK[0-9a-f]{24}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------- .env 생성 규칙

def placeholder_value(name: str, token_hex: Callable[[int], str] = secrets.token_hex,
                      token_urlsafe: Callable[[int], str] = secrets.token_urlsafe) -> str:
    """자리표시자 이름 하나에 넣을 값을 만든다.

    `*_USER` → 고정 계정 이름 `paldo_dev`, Garage 키 ID → `GK` + 16진수 24자,
    Garage 비밀 키·RPC 비밀 → 16진수 64자, 그 밖(비밀번호·토큰) → URL 안전 문자 32자.
    """
    if name.endswith("_USER"):
        return "paldo_dev"
    if name == "GARAGE_KEY_ID":
        return "GK" + token_hex(12)
    if name in ("GARAGE_SECRET_KEY", "GARAGE_RPC_SECRET"):
        return token_hex(32)
    return token_urlsafe(24)


def find_placeholders(text: str) -> list[str]:
    """`<NAME>` 자리표시자 이름을 처음 나온 순서대로 중복 없이 돌려준다."""
    seen: dict[str, None] = {}
    for m in PLACEHOLDER_RE.finditer(text):
        seen.setdefault(m.group(1), None)
    return list(seen)


def render_env(template: str, make_value: Callable[[str], str] = placeholder_value) -> str:
    """`.env.example` 내용의 자리표시자를 값으로 바꾼 `.env` 내용을 돌려준다(같은 이름은 같은 값)."""
    values = {name: make_value(name) for name in find_placeholders(template)}
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def parse_env(text: str) -> dict[str, str]:
    """`KEY=VALUE` 줄을 읽는다. 빈 줄과 `#` 주석은 건너뛴다. 따옴표는 해석하지 않는다."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def env_problems(env: dict[str, str], required: Iterable[str]) -> list[str]:
    """`.env` 값의 문제 목록. 빠진 변수, 남은 자리표시자, Garage 키 형식 오류를 찾는다."""
    problems = []
    for key in required:
        value = env.get(key, "")
        if not value:
            problems.append(f"{key}: 값이 없다")
        elif PLACEHOLDER_RE.search(value):
            problems.append(f"{key}: 자리표시자가 남아 있다")
    if env.get("GARAGE_KEY_ID") and not GARAGE_KEY_ID_RE.match(env["GARAGE_KEY_ID"]):
        problems.append("GARAGE_KEY_ID: GK + 16진수 24자가 아니다")
    for key in ("GARAGE_SECRET_KEY", "GARAGE_RPC_SECRET"):
        if env.get(key) and not HEX64_RE.match(env[key]):
            problems.append(f"{key}: 16진수 64자가 아니다")
    return problems


# ---------------------------------------------------------------- 점검 판정

@dataclass(frozen=True)
class CheckResult:
    """서비스 하나의 상태 확인 결과."""

    service: str
    ok: bool
    detail: str
    duration_ms: int


def garage_node_id(output: str) -> str:
    """`garage node id -q` 출력(`<id>@<주소>`)에서 노드 ID만 꺼낸다."""
    for line in output.splitlines():
        line = line.strip()
        if re.match(r"^[0-9a-f]{16,}(@.*)?$", line):
            return line.split("@", 1)[0]
    raise ValueError(f"garage 노드 ID를 찾지 못했다: {output!r}")


def garage_needs_layout(status_output: str) -> bool:
    """`garage status` 출력으로 레이아웃(역할 배정)이 아직 없는지 판단한다."""
    return "NO ROLE ASSIGNED" in status_output.upper()


def compose_unhealthy(ps_json_lines: str, expected: Iterable[str]) -> dict[str, str]:
    """`docker compose ps --format json` 출력에서 healthy가 아닌 서비스와 그 상태를 돌려준다."""
    states: dict[str, str] = {}
    for line in ps_json_lines.splitlines():
        line = line.strip()
        if not line:
            continue
        rows = json.loads(line)
        for row in rows if isinstance(rows, list) else [rows]:
            states[row["Service"]] = row.get("Health") or row.get("State", "")
    return {s: states.get(s, "없음") for s in expected if states.get(s) != "healthy"}


# ---------------------------------------------------------------- S3 서명(AWS SigV4, 경로 방식)

def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(method: str, host: str, path: str, payload: bytes, access_key: str, secret_key: str,
                  region: str, now: datetime, query: dict[str, str] | None = None) -> dict[str, str]:
    """S3 요청 하나에 붙일 서명 헤더(`Authorization`, `x-amz-date`, `x-amz-content-sha256`)를 만든다.

    `path`는 `/<버킷>/<키>` 형태(경로 방식). `now`는 UTC 시각.
    """
    amz_date = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted((query or {}).items()))
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([method, quote(path, safe="/-_.~"), canonical_query,
                                   canonical_headers, signed_headers, payload_hash])
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode("utf-8"), datestamp), region), "s3"), "aws4_request")
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "Authorization": f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                         f"SignedHeaders={signed_headers}, Signature={signature}",
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }


# ---------------------------------------------------------------- 로그(README 5절)

def log_record(level: str, component: str, msg: str, now: datetime | None = None, **fields) -> str:
    """구조화 로그 한 줄(JSON). 필수 필드 ts·level·service·component·msg를 채운다."""
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="milliseconds")
    rec = {"ts": ts.replace("+00:00", "Z"), "level": level, "service": SERVICE_NAME,
           "component": component, "msg": msg}
    rec.update(fields)
    return json.dumps(rec, ensure_ascii=False)
