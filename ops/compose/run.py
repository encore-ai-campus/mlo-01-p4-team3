"""로컬 개발 환경 명령: `.env` 만들기, Garage 초기화, 서비스 상태 확인(스모크).

사용법(저장소 루트에서):
    python -m ops.compose.run make-env       # .env.example → .env(무작위 값). 이미 있으면 그대로 둔다
    python -m ops.compose.run garage-init    # Garage 레이아웃·키·버킷(멱등)
    python -m ops.compose.run smoke          # 6개 서비스 상태 확인. 하나라도 실패하면 종료 코드 1

계층: 이 파일은 표현(CLI)·응용(순서 조립)·인프라(docker·HTTP 호출)이고, 판정 규칙은 `devenv.py`에 있다.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ops.compose import devenv

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "ops" / "compose" / "dev.yml"
ENV_FILE = ROOT / ".env"
COMPOSE_ENV_FILE = ROOT / "ops" / "compose" / ".env"   # Compose 변수 치환용(make-env가 루트 .env에서 만든다)
ENV_EXAMPLE = ROOT / ".env.example"
LOG_FILE = ROOT / "out" / "logs" / "devenv.jsonl"
SERVICES = ("mongo", "kafka", "mysql", "garage", "prometheus", "grafana")
BUCKETS = ("paldo-lake", "paldo-mlflow")
GARAGE_KEY_NAME = "paldo-dev"


def port(name: str, default: int) -> int:
    """호스트 포트. dev.yml과 같은 환경 변수(`PALDO_*_PORT`)를 읽는다."""
    return int(os.environ.get(name, default))


# ---------------------------------------------------------------- 인프라: 로그·docker·HTTP

class Log:
    """구조화 로그를 표준 출력과 `out/logs/devenv.jsonl`에 함께 쓴다."""

    def __init__(self, path: Path = LOG_FILE):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, level: str, component: str, msg: str, **fields) -> None:
        """한 줄 기록."""
        line = devenv.log_record(level, component, msg, **fields)
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class Compose:
    """`docker compose -p paldo-dev -f ops/compose/dev.yml …` 호출."""

    def __init__(self, timeout: int = 120):
        self.base = ["docker", "compose", "-p", devenv.PROJECT, "-f", str(COMPOSE_FILE)]
        self.timeout = timeout

    def run(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """compose 하위 명령을 실행하고 결과를 돌려준다(예외 대신 종료 코드)."""
        return subprocess.run(self.base + list(args), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout or self.timeout)

    def exec(self, service: str, *argv: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """실행 중인 서비스 컨테이너 안에서 명령을 실행한다."""
        return self.run("exec", "-T", service, *argv, timeout=timeout)

    def sh(self, service: str, script: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """컨테이너 안에서 `sh -c`로 실행한다. 비밀값은 컨테이너 환경 변수로 참조한다."""
        return self.exec(service, "sh", "-c", script, timeout=timeout)


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None,
         timeout: int = 10) -> tuple[int, bytes]:
    """HTTP 요청 하나. 상태 코드와 본문을 돌려준다(4xx·5xx도 예외 없이)."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def tcp_open(p: int, host: str = "127.0.0.1") -> bool:
    """호스트 포트가 열려 있는지."""
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex((host, p)) == 0


def load_env() -> dict[str, str]:
    """`.env`를 읽고 형식과 `ops/compose/.env` 동기화를 검사한다. 문제가 있으면 종료한다."""
    if not ENV_FILE.exists():
        sys.exit(".env가 없다. 먼저 python -m ops.compose.run make-env")
    env = devenv.parse_env(ENV_FILE.read_text(encoding="utf-8"))
    required = devenv.parse_env(ENV_EXAMPLE.read_text(encoding="utf-8")).keys()
    problems = devenv.env_problems(env, required)
    compose_now = COMPOSE_ENV_FILE.read_text(encoding="utf-8") if COMPOSE_ENV_FILE.exists() else ""
    if compose_now != devenv.compose_env(env):
        problems.append("ops/compose/.env가 루트 .env와 다르다(python -m ops.compose.run make-env로 다시 만든 뒤 up -d)")
    if problems:
        sys.exit(".env 문제: " + "; ".join(problems))
    return env


def env_port(env: dict[str, str], name: str, default: int) -> int:
    """`.env`에 있으면 그 값, 없으면 셸 환경 변수, 그것도 없으면 기본값."""
    return int(env.get(name) or os.environ.get(name) or default)


# ---------------------------------------------------------------- 응용: 명령별 흐름

def cmd_make_env(_args) -> int:
    """루트 `.env`를 만들고(이미 있으면 그대로) Compose 치환용 `ops/compose/.env`를 거기서 다시 만든다."""
    if ENV_FILE.exists():
        print(f"{ENV_FILE.name}가 이미 있어 그대로 둔다(지우고 다시 만들면 기존 볼륨의 계정과 달라진다).")
    else:
        ENV_FILE.write_text(devenv.render_env(ENV_EXAMPLE.read_text(encoding="utf-8")), encoding="utf-8")
        print(f"{ENV_FILE.name}를 만들었다. Git은 이 파일을 추적하지 않는다(.gitignore).")
    env = devenv.parse_env(ENV_FILE.read_text(encoding="utf-8"))
    COMPOSE_ENV_FILE.write_text(devenv.compose_env(env), encoding="utf-8")
    print("ops/compose/.env를 루트 .env에서 다시 만들었다(" + ", ".join(
        f"{k}={env.get(k, '')}" for k in devenv.COMPOSE_VARS) + ").")
    return 0


def cmd_garage_init(args) -> int:
    """Garage 1노드에 레이아웃을 배정하고, .env의 키를 등록하고, 버킷을 만든다(여러 번 실행해도 같다).

    `up -d` 직후에도 쓸 수 있게 `garage status`가 성공할 때까지 최대 `args.wait`초 기다린다.
    """
    env, dc, log = load_env(), Compose(), Log()

    def garage(*argv: str) -> subprocess.CompletedProcess:
        return dc.exec("garage", "/garage", *argv)

    deadline = time.monotonic() + args.wait
    status = garage("status")
    while status.returncode != 0 and time.monotonic() < deadline:
        time.sleep(3)
        status = garage("status")
    if status.returncode != 0:
        log.write("error", "garage_init", "garage status 실패", error_type="garage_status", stderr=status.stderr[-500:])
        return 1
    if devenv.garage_needs_layout(status.stdout):
        node = devenv.garage_node_id(garage("node", "id", "-q").stdout)
        for argv in (("layout", "assign", "-z", "dc1", "-c", "1G", node), ("layout", "apply", "--version", "1")):
            r = garage(*argv)
            if r.returncode != 0:
                log.write("error", "garage_init", "레이아웃 배정 실패", error_type="garage_layout",
                          stderr=r.stderr[-500:])
                return 1
        log.write("info", "garage_init", "레이아웃 배정", node=node[:16])
    if garage("key", "info", env["PALDO_S3_ACCESS_KEY"]).returncode != 0:
        r = garage("key", "import", "--yes", "-n", GARAGE_KEY_NAME, env["PALDO_S3_ACCESS_KEY"], env["PALDO_S3_SECRET_KEY"])
        if r.returncode != 0:
            log.write("error", "garage_init", "키 등록 실패", error_type="garage_key", stderr=r.stderr[-500:])
            return 1
        log.write("info", "garage_init", "키 등록", key_name=GARAGE_KEY_NAME)
    for bucket in BUCKETS:
        if garage("bucket", "info", bucket).returncode != 0:
            garage("bucket", "create", bucket)
        r = garage("bucket", "allow", "--read", "--write", "--owner", bucket, "--key", env["PALDO_S3_ACCESS_KEY"])
        if r.returncode != 0:
            log.write("error", "garage_init", "버킷 권한 실패", error_type="garage_bucket", bucket=bucket,
                      stderr=r.stderr[-500:])
            return 1
        log.write("info", "garage_init", "버킷 준비", bucket=bucket)
    return 0


def _timed(fn):
    start = time.monotonic()
    ok, detail = fn()
    return ok, detail, int((time.monotonic() - start) * 1000)


def check_mongo(dc: Compose, env: dict) -> tuple[bool, str]:
    """레플리카셋 상태(PRIMARY)와 w=majority 쓰기 1건."""
    js = ("const s = rs.status();"
          "const w = db.getSiblingDB('paldo_smoke').smoke.insertOne({ts: new Date()}, {writeConcern: {w: 'majority'}});"
          "print(JSON.stringify({version: db.version(), set: s.set,"
          " members: s.members.map(m => m.name + ' ' + m.stateStr), majority_write: w.acknowledged}))")
    r = dc.sh("mongo", 'mongosh --port 27117 --quiet -u "$MONGO_INITDB_ROOT_USERNAME" '
                       f'-p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --eval "{js}"')
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0 and "PRIMARY" in out and '"majority_write":true' in out, out[-400:]


def check_kafka(dc: Compose, env: dict) -> tuple[bool, str]:
    """토픽 생성(없으면) → 메시지 1건 발행 → 처음부터 1건 소비."""
    marker = f"smoke-{int(time.time())}"
    bin_ = "/opt/kafka/bin"
    # 내부 리스너(kafka:19092). 호스트 쪽 리스너는 smoke 끝의 호스트 포트 확인이 맡는다.
    script = (f'export KAFKA_HEAP_OPTS="-Xmx128m"; B=kafka:19092; '
              f'{bin_}/kafka-topics.sh --version && '
              f'{bin_}/kafka-topics.sh --bootstrap-server $B --create --if-not-exists --topic paldo.smoke '
              f'--partitions 1 --replication-factor 1 && '
              f'echo {marker} | {bin_}/kafka-console-producer.sh --bootstrap-server $B --topic paldo.smoke && '
              f'{bin_}/kafka-console-consumer.sh --bootstrap-server $B --topic paldo.smoke --from-beginning '
              f'--timeout-ms 20000 2>/dev/null | grep -m1 {marker}')
    r = dc.sh("kafka", script, timeout=180)
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0 and marker in out, out[-400:]


MYSQL_ACCOUNTS = {   # 이름 → (사용자 변수, 비밀번호 변수, 스키마 변수). 컨테이너 안 환경 변수(env_file) 이름과 같다
    "ads": ("PALDO_ADS_DB_USER", "PALDO_ADS_DB_PASSWORD", "PALDO_ADS_DB_NAME"),
    "auth": ("PALDO_AUTH_DB_USER", "PALDO_AUTH_DB_PASSWORD", "PALDO_AUTH_DB_NAME"),
}


def _mysql_as(dc: Compose, account: str, schema_var: str):
    user, pw, _ = MYSQL_ACCOUNTS[account]
    return dc.sh("mysql", f'MYSQL_PWD="${{{pw}}}" mysql -h127.0.0.1 -u"${{{user}}}" "${{{schema_var}}}" -N '
                          '-e "SELECT VERSION(), DATABASE()"')


def check_mysql(dc: Compose, env: dict) -> tuple[bool, str]:
    """광고·계정 DB 계정이 각자 스키마에는 붙고, 서로의 스키마에는 거부되는지(두 방향)."""
    parts, ok = [], True
    for account, (_, _, own) in MYSQL_ACCOUNTS.items():
        r = _mysql_as(dc, account, own)
        good = r.returncode == 0 and env[own] in r.stdout
        ok &= good
        parts.append(f"{account}→{env[own]} " + ("접속" if good else "실패: " + (r.stdout + r.stderr).strip()[-200:]))
        other = MYSQL_ACCOUNTS["auth" if account == "ads" else "ads"][2]
        r = _mysql_as(dc, account, other)
        denied = r.returncode != 0 and devenv.mysql_access_denied(r.stderr)
        ok &= denied
        parts.append(f"{account}→{env[other]} " + ("거부" if denied else "거부 안 됨: " + (r.stdout + r.stderr).strip()[-200:]))
    return ok, ", ".join(parts)


def check_garage(dc: Compose, env: dict) -> tuple[bool, str]:
    """노드 상태, 그리고 호스트에서 S3 API로 객체 PUT → GET → DELETE."""
    st = dc.exec("garage", "/garage", "status")
    if st.returncode != 0 or devenv.garage_needs_layout(st.stdout):
        return False, "garage status 실패 또는 레이아웃 없음(garage-init 필요): " + (st.stdout + st.stderr)[-300:]
    p = port("PALDO_S3_PORT", 3900)
    host, path = f"127.0.0.1:{p}", f"/{BUCKETS[0]}/smoke/{int(time.time())}.txt"
    body = b"paldo smoke"
    results = []
    for method, payload in (("PUT", body), ("GET", b""), ("DELETE", b"")):
        headers = devenv.sigv4_headers(method, host, path, payload, env["PALDO_S3_ACCESS_KEY"], env["PALDO_S3_SECRET_KEY"],
                                       "garage", datetime.now(timezone.utc))
        code, data = http(method, f"http://{host}{path}", headers, payload if method == "PUT" else None)
        results.append((method, code, data))
    ok = (results[0][1] == 200 and results[1][1] == 200 and results[1][2] == body and results[2][1] == 204)
    return ok, "S3 " + ", ".join(f"{m} {c}" for m, c, _ in results) + f" (bucket {BUCKETS[0]})"


def check_prometheus(dc: Compose, env: dict) -> tuple[bool, str]:
    """준비 상태와, 수집 대상(prometheus·grafana·garage)이 모두 `up == 1`인지. 첫 수집 전이면 최대 30초 다시 본다."""
    base = f"http://127.0.0.1:{port('PALDO_PROMETHEUS_PORT', 9090)}"
    code, _ = http("GET", base + "/-/ready")
    deadline = time.monotonic() + 30
    while True:
        qc, data = http("GET", base + "/api/v1/query?query=up")
        result = json.loads(data) if qc == 200 else {}
        down = devenv.prometheus_down_jobs(result)
        if not down or time.monotonic() >= deadline:
            break
        time.sleep(5)
    ups = [f"{r['metric']['job']}={r['value'][1]}" for r in result.get("data", {}).get("result", [])]
    return code == 200 and qc == 200 and not down, f"ready {code}, up: {' '.join(sorted(ups))}, down: {down}"


def check_grafana(dc: Compose, env: dict) -> tuple[bool, str]:
    """`/api/health`(DB ok·버전)와 관리자 계정으로 데이터 소스 조회(Prometheus 연결)."""
    base = f"http://127.0.0.1:{port('PALDO_GRAFANA_PORT', 3000)}"
    code, data = http("GET", base + "/api/health")
    health = json.loads(data) if code == 200 else {}
    cred = base64.b64encode(f"{env['GF_SECURITY_ADMIN_USER']}:{env['GF_SECURITY_ADMIN_PASSWORD']}".encode()).decode()
    dc_code, ds = http("GET", base + "/api/datasources", {"Authorization": "Basic " + cred})
    names = [d["type"] for d in json.loads(ds)] if dc_code == 200 else []
    ok = code == 200 and health.get("database") == "ok" and "prometheus" in names
    return ok, f"health {code} db={health.get('database')} version={health.get('version')}, datasources {names}"


CHECKS = {"mongo": (check_mongo, "PALDO_MONGO_PORT", 27117), "kafka": (check_kafka, "PALDO_KAFKA_PORT", 9092),
          "mysql": (check_mysql, "PALDO_ADS_DB_PORT", 3316), "garage": (check_garage, "PALDO_S3_PORT", 3900),
          "prometheus": (check_prometheus, "PALDO_PROMETHEUS_PORT", 9090),
          "grafana": (check_grafana, "PALDO_GRAFANA_PORT", 3000)}


def cmd_smoke(args) -> int:
    """compose 상태(healthy) → 호스트 포트 → 서비스별 기능 확인. 전부 성공해야 종료 코드 0."""
    env, dc, log = load_env(), Compose(), Log()
    deadline = time.monotonic() + args.wait
    while True:
        bad = devenv.compose_unhealthy(dc.run("ps", "--format", "json").stdout, SERVICES)
        if not bad or time.monotonic() >= deadline:
            break
        time.sleep(5)
    log.write("info" if not bad else "error", "smoke", "compose 상태", unhealthy=bad,
              **({"error_type": "unhealthy"} if bad else {}))
    failed = 0
    for service, (fn, port_env, default) in CHECKS.items():
        p = env_port(env, port_env, default)
        try:
            ok, detail, ms = _timed(lambda: fn(dc, env))
        except Exception as e:  # 점검 하나의 예외가 나머지 점검을 막지 않게 기록하고 계속한다
            ok, detail, ms = False, f"{type(e).__name__}: {e}", 0
        ok = ok and tcp_open(p)
        failed += not ok
        log.write("info" if ok else "error", "smoke", f"{service} {'OK' if ok else 'FAIL'}", target=service,
                  host_port=p, duration_ms=ms, detail=detail, **({} if ok else {"error_type": "check_failed"}))
    log.write("info" if not failed else "error", "smoke", f"결과 {len(CHECKS) - failed}/{len(CHECKS)} 성공")
    return 1 if failed or bad else 0


def main(argv: list[str] | None = None) -> int:
    """명령줄 진입점."""
    if hasattr(sys.stdout, "reconfigure"):   # 파이프·Git Bash에서도 한글 로그가 깨지지 않게(Windows 기본 cp949)
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m ops.compose.run", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make-env", help=".env.example → .env").set_defaults(fn=cmd_make_env)
    gp = sub.add_parser("garage-init", help="Garage 레이아웃·키·버킷")
    gp.add_argument("--wait", type=int, default=90, help="Garage가 응답할 때까지 기다릴 최대 초(기본 90)")
    gp.set_defaults(fn=cmd_garage_init)
    sp = sub.add_parser("smoke", help="6개 서비스 상태 확인")
    sp.add_argument("--wait", type=int, default=180, help="모두 healthy가 될 때까지 기다릴 최대 초(기본 180)")
    sp.set_defaults(fn=cmd_smoke)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
