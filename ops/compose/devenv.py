"""로컬 개발 환경(ops/compose/dev.yml)의 규칙 부분: .env 생성 규칙, 점검 판정, S3 서명, 로그 레코드.

외부 호출(docker, HTTP, 파일)은 하지 않는다. 그 부분은 `ops/compose/run.py`가 맡는다.
표준 라이브러리만 쓴다(Python 3.14).
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
S3_KEY_ID_RE = re.compile(r"^GK[0-9a-f]{24}$")   # Garage 키 ID 형식
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SQL_SAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")   # dev.yml initdb SQL에 넣는 값(계정 저장소 이름·계정·비밀번호)

# 로컬 Compose에서 고정인 값(자리표시자 이름 → 값). 빈 값은 "로컬 Compose에 없음, 가짜 구현을 쓴다"는 뜻이다.
LOCAL_FIXED = {
    "KAFKA_BOOTSTRAP": "localhost:9092",
    "S3_ENDPOINT": "http://localhost:3900",
    "PROMETHEUS_URL": "http://localhost:9090",
    "ADS_DB_HOST": "127.0.0.1",
    "ADS_DB_PORT": "3316",          # Windows MySQL(3306)과 겹치지 않게(11/4 PM 결정, integration.md 4.1)
    "ADS_DB_NAME": "paldo_ads",
    "AUTH_DB_NAME": "paldo_auth",   # 계정 저장소 스키마(광고와 같은 MySQL, 다른 스키마·계정. 11/4 PM 답)
    "AUTH_DB_USER": "paldo_auth_dev",
    "PUBLIC_ORIGIN": "http://localhost:8000",   # Django 개발 서버 기본 포트. 브라우저도 이 주소로(127.0.0.1이면 웹소켓 거부)
    "MLFLOW_URI": "",               # 로컬은 mlruns/(가짜 → 진짜 교체표)
    "MLFLOW_MODELS": "",            # 로컬은 기존 정적 레지스트리
    "MLFLOW_MODEL_CACHE": "mlruns",
    "MLFLOW_TRACKING_URI": "",
    "LLM_URL": "",                  # 로컬은 LLM 폴백 템플릿
    "ADS_INTERNAL_URL": "",         # 로컬은 FakeAdProvider
}
# 다른 자리표시자 값으로 만드는 값.
DERIVED = {
    "MONGO_URI": "mongodb://{MONGO_USER}:{MONGO_PASSWORD}@localhost:27117/?replicaSet=rs0&authSource=admin",
}
# Compose 변수 치환에 필요해 ops/compose/.env로 복사하는 변수(Compose는 저장소 루트 .env를 치환에 쓰지 않는다).
COMPOSE_VARS = ("PALDO_ADS_DB_PORT",)
# 로컬에서 비어 있어도 되는 변수.
MAY_BE_EMPTY = ("PALDO_MLFLOW_URI", "PALDO_LLM_URL", "PALDO_ADS_INTERNAL_URL",
                "PALDO_MLFLOW_MODELS", "MLFLOW_TRACKING_URI",
                "PALDO_SIGNUP_INVITE_CODE")   # 초대 코드: 비면 공개 가입(파일럿)


# ---------------------------------------------------------------- .env 생성 규칙

def placeholder_value(name: str, token_hex: Callable[[int], str] = secrets.token_hex,
                      token_urlsafe: Callable[[int], str] = secrets.token_urlsafe) -> str:
    """자리표시자 이름 하나에 넣을 값을 만든다(`DERIVED`에 있는 이름은 `render_env`가 따로 채운다).

    로컬 고정값(`LOCAL_FIXED`) → 그 값, `*_USER` → `paldo_dev`, S3 키 ID → `GK` + 16진수 24자,
    S3 비밀 키·Garage RPC 비밀·서명 키 → 16진수 64자, 그 밖(비밀번호·토큰·Django 키) → URL 안전 문자.
    """
    if name in LOCAL_FIXED:
        return LOCAL_FIXED[name]
    if name.endswith("_USER"):
        return "paldo_dev"
    if name == "S3_ACCESS_KEY":
        return "GK" + token_hex(12)
    if name in ("S3_SECRET_KEY", "GARAGE_RPC_SECRET", "ADS_TOKEN_KEY"):
        return token_hex(32)
    if name == "DJANGO_SECRET_KEY":
        return token_urlsafe(48)
    return token_urlsafe(24)


def find_placeholders(text: str) -> list[str]:
    """`<NAME>` 자리표시자 이름을 처음 나온 순서대로 중복 없이 돌려준다."""
    seen: dict[str, None] = {}
    for m in PLACEHOLDER_RE.finditer(text):
        seen.setdefault(m.group(1), None)
    return list(seen)


def render_env(template: str, make_value: Callable[[str], str] = placeholder_value) -> str:
    """`.env.example` 내용의 자리표시자를 값으로 바꾼 `.env` 내용을 돌려준다.

    같은 이름은 같은 값이다(예: `MYSQL_USER`와 `PALDO_ADS_DB_USER`가 같은 `<MYSQL_USER>`를 쓴다).
    `DERIVED`의 이름은 다른 자리표시자 값으로 만든다(예: Mongo 연결 문자열).
    """
    names = find_placeholders(template)
    values = {name: make_value(name) for name in names if name not in DERIVED}
    for name in names:
        if name in DERIVED:
            values[name] = DERIVED[name].format(**values)
    return PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)


def compose_env(env: dict[str, str]) -> str:
    """`ops/compose/.env`(Compose 변수 치환용) 내용. 저장소 루트 `.env`에서 `COMPOSE_VARS`만 옮긴다."""
    lines = ["# 생성 파일(python -m ops.compose.run make-env). 직접 고치지 말고 저장소 루트 .env를 고친 뒤 다시 실행한다."]
    lines += [f"{k}={env[k]}" for k in COMPOSE_VARS if env.get(k)]
    return "\n".join(lines) + "\n"


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
    """`.env` 값의 문제 목록. 빠진 변수, 남은 자리표시자, S3 키·Garage 비밀 형식 오류를 찾는다."""
    problems = []
    for key in required:
        if key not in env:
            problems.append(f"{key}: 변수가 없다")
            continue
        value = env[key]
        if not value and key not in MAY_BE_EMPTY:
            problems.append(f"{key}: 값이 없다")
        elif PLACEHOLDER_RE.search(value):
            problems.append(f"{key}: 자리표시자가 남아 있다")
    if env.get("PALDO_S3_ACCESS_KEY") and not S3_KEY_ID_RE.match(env["PALDO_S3_ACCESS_KEY"]):
        problems.append("PALDO_S3_ACCESS_KEY: GK + 16진수 24자가 아니다(Garage 키 ID 형식)")
    if env.get("PALDO_ADS_DB_PORT") and not env["PALDO_ADS_DB_PORT"].isdigit():
        problems.append("PALDO_ADS_DB_PORT: 숫자가 아니다")
    for key in ("PALDO_ADS_DB_USER", "PALDO_ADS_DB_NAME"):
        auth = key.replace("_ADS_", "_AUTH_")
        if env.get(key) and env.get(auth) == env[key]:
            problems.append(f"{auth}: 광고와 같다(계정 저장소는 전용 스키마·계정, privacy.md 5절)")
    for key in ("PALDO_ADS_DB_HOST", "PALDO_ADS_DB_PORT"):
        auth = key.replace("_ADS_", "_AUTH_")
        if key in env and auth in env and env[auth] != env[key]:
            problems.append(f"{auth}: {key}와 다르다(로컬은 같은 MySQL. 포트를 바꾸면 둘 다 고친다)")
    for key in ("PALDO_AUTH_DB_NAME", "PALDO_AUTH_DB_USER", "PALDO_AUTH_DB_PASSWORD"):
        if env.get(key) and not SQL_SAFE_RE.match(env[key]):
            problems.append(f"{key}: 영문·숫자·_·-만 쓴다(MySQL 초기화 SQL에 그대로 들어간다)")
    for key in ("PALDO_S3_SECRET_KEY", "GARAGE_RPC_SECRET"):
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


PROMETHEUS_JOBS = ("prometheus", "grafana", "garage")   # ops/compose/prometheus.yml의 수집 대상


def prometheus_down_jobs(query_result: dict, expected: Iterable[str] = PROMETHEUS_JOBS) -> list[str]:
    """`/api/v1/query?query=up` 응답에서 없거나 `up != 1`인 job 이름 목록(빈 목록이면 모두 수집 중)."""
    up: dict[str, bool] = {}
    for r in query_result.get("data", {}).get("result", []):
        job = r.get("metric", {}).get("job", "")
        up[job] = up.get(job, True) and r.get("value", [None, "0"])[1] == "1"
    return [j for j in expected if not up.get(j, False)]


def mysql_access_denied(output: str) -> bool:
    """mysql 클라이언트 출력이 스키마 접근 거부(ERROR 1044 또는 1142)인지."""
    return bool(re.search(r"ERROR (1044|1142) ", output))


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
