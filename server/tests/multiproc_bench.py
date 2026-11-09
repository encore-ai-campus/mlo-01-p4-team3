"""E-09-10 게임 웹 프로세스 수·채널 레이어 비교 부하(로컬 시험 전용, 운영에서 쓰지 않음).

실제 daphne N개를 띄우고 앞단 Caddy(공식 Docker 이미지 `caddy:2.10.2`, 운영 Caddyfile과 같은 분배·상태 확인 설정)를
거쳐 B-07-05와 같은 부하(사용자당 초당 한 요청의 시작·명령 + 사용자별 웹소켓 비콘 1개)를 준다.
부하 생성기(이 프로세스)와 서버(daphne 프로세스)는 분리된다. 모든 경우가 같은 Caddy 경로를 지나므로 경우끼리 비교한다.

python tools/perf/api_bench.py --web-processes 2,1 --users 30 --minutes 3            # 2개·1개, memory
python tools/perf/api_bench.py --web-processes 2 --channel-layer redis --redis-url redis://127.0.0.1:6379/0
python -B -m server.tests.multiproc_bench --cases p2_memory,p1_memory,p2_redis --redis-url ...   # 경우 이름으로 지정
- `--web-processes`: 띄울 daphne 수 목록(1~4, 쉼표). `--channel-layer`(memory 기본·redis)와 묶어 경우 `p<N>_<layer>`가 된다.
- `--result-name <이름>`: 증빙 결과 JSON(`docs/evidence/perf/<--result-task, 기본 E-09-10>/<이름>.json`)도 쓴다.
  30명·3분 이상일 때만. `--compare-asgi`는 C-08-04 명령과 맞추려고 받기만 한다(이 도구는 늘 앞단 경유 비교다).
- `--front`: 앞단 Caddy를 띄우는 방법. `docker`(기본, 공식 이미지. Linux는 host 네트워크로 loopback daphne에 닿는다)
  또는 `binary`(이미 설치된 caddy 실행 파일, `--caddy-bin`. 시험 서버처럼 Docker가 없는 곳).
- 이미 떠 있는 앞단 주소로만 부하를 보내는 모드는 두지 않는다. 이 부하는 시험용 엔진(ScriptedGameEngine)·시험 접두어
  컬렉션을 쓰므로 실제 엔진이 도는 서버에 보낼 수 없다. 같은 서버에서 이 도구가 프로세스를 직접 띄워 비교한다.
Mongo는 PALDO_MONGO_URI(api_bench.mongo_uri 규칙)로 받는다. 결과는 out/handoff/e0910-multiproc-report.json에 쓴다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Thread
import time
import uuid
from urllib.request import Request, build_opener

from .api_bench import mongo_uri, percentile
from .asgi_bench import beacon_load, measure_game

CADDY_IMAGE = "caddy:2.10.2"
LAYERS = ("memory", "redis")
MAX_PROCESSES = 4


def parse_case(name: str):
    """경우 이름 `p<N>_<layer>`를 (프로세스 수, 채널 레이어)로 읽는다. 틀리면 ValueError."""
    head, _, layer = name.partition("_")
    if not (head[:1] == "p" and head[1:].isdigit() and 1 <= int(head[1:]) <= MAX_PROCESSES and layer in LAYERS):
        raise ValueError(name)
    return int(head[1:]), layer


def case_names(cases: str, web_processes: str, layer: str) -> list:
    """`--web-processes`(우선)나 `--cases`에서 경우 이름 목록을 만든다."""
    if web_processes:
        return [f"p{count.strip()}_{layer}" for count in web_processes.split(",") if count.strip()]
    return [name.strip() for name in cases.split(",") if name.strip()]


def front_mode(front: str, platform: str = sys.platform) -> str:
    """앞단 실행 방식: binary, docker-host(Linux host 네트워크), docker-desktop(host.docker.internal)."""
    if front == "binary":
        return "binary"
    return "docker-host" if platform.startswith("linux") else "docker-desktop"


def free_port() -> int:
    """loopback의 빈 포트 하나를 고른다."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def caddyfile(ports, front_host, listen=":8080", upstream_host="host.docker.internal") -> str:
    """운영 Caddyfile(ops/proxy/Caddyfile)의 분배·상태 확인 설정을 평문 HTTP로 옮긴다."""
    upstreams = " ".join(f"{upstream_host}:{port}" for port in ports)
    return ("{\n    admin off\n    auto_https off\n}\n\n" + listen + " {\n"
            f"    reverse_proxy {upstreams} {{\n"
            "        lb_policy least_conn\n        lb_try_duration 2s\n        lb_try_interval 250ms\n"
            "        fail_duration 10s\n        health_uri /healthz\n        health_interval 5s\n        health_timeout 2s\n"
            f"        health_headers {{\n            Host {front_host}\n        }}\n"
            "    }\n}\n")


RESULT_KEYS = ("requests", "errors", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "command_p95_ms",
               "beacon_acks", "beacon_errors")


def result_metrics(rows) -> dict:
    """경우별 행을 증빙 결과 JSON metrics(`<경우>_<항목>`)로 바꾼다."""
    metrics = {}
    for row in rows:
        for key in RESULT_KEYS:
            if row.get(key) is not None:
                unit = "ms" if key.endswith("_ms") else ("rps" if key == "throughput_rps" else "count")
                metrics[f"{row['case']}_{key}"] = {"value": row[key], "unit": unit}
    return metrics


def stage_counts(metrics_text: str) -> dict:
    """프로세스별 bench 단계 지표에서 명령 저장(commit) 수를 읽어 분배를 본다."""
    for line in metrics_text.splitlines():
        if line.startswith("paldo_bench_stage_seconds_count") and 'stage="commit"' in line:
            return {"commit": float(line.rsplit(" ", 1)[1])}
    return {"commit": 0.0}


def redis_clients(url):
    """Redis 연결 수(connected_clients). Redis 경우가 아니면 None."""
    import redis
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        return int(client.info("clients")["connected_clients"])
    finally:
        client.close()


def run_case(args, name, processes, layer, uri, users, steps, folder):
    """daphne N개·Caddy·부하를 실행하고 자기 자원만 정리한다."""
    from engine.testing.fakes import FixtureContentSource, ScriptedGameEngine
    from server.paldo_web.mongo import open_mongo_runtime
    ports, front = [free_port() for _ in range(processes)], free_port()
    origin = f"http://127.0.0.1:{front}"
    prefix = "b0705_" + uuid.uuid4().hex + "_"   # asgi_bench_app이 받는 시험 접두어 형식
    runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None, uri=uri, collection_prefix=prefix)
    servers, caddy, stop = [], None, Event()
    try:
        runtime.content.insert(FixtureContentSource().load("*"))
        for port in ports:
            env = dict(os.environ, PALDO_MONGO_URI=uri, PALDO_PUBLIC_ORIGIN=origin, PALDO_BENCH_PREFIX=prefix,
                       PALDO_BENCH_PORT=str(port), PALDO_BENCH_SCENARIO="normal", PALDO_CHANNEL_LAYER=layer,
                       PALDO_WEB_INSTANCE=str(port))
            if layer == "redis":
                env["PALDO_REDIS_URL"] = args.redis_url
            log = (folder / f"{name}-{port}.log").open("wb")
            servers.append((subprocess.Popen(
                [sys.executable, "-B", "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
                 "server.tests.asgi_bench_app:application"], env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), log))
        config = folder / f"caddy-{name}"
        config.mkdir()
        mode = front_mode(args.front)
        if mode == "docker-desktop":   # Docker Desktop은 host.docker.internal로 호스트 loopback에 닿는다
            text = caddyfile(ports, "127.0.0.1")
        else:                          # host 네트워크·실행 파일은 같은 loopback에서 바로 붙는다
            text = caddyfile(ports, "127.0.0.1", listen=f"127.0.0.1:{front}", upstream_host="127.0.0.1")
        (config / "Caddyfile").write_text(text, encoding="utf-8")
        if mode == "binary":
            caddy_log = (folder / f"{name}-caddy.log").open("wb")
            servers.append((subprocess.Popen(
                [args.caddy_bin, "run", "--config", str(config / "Caddyfile"), "--adapter", "caddyfile"],
                stdout=caddy_log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)), caddy_log))
        else:
            caddy = "paldo-e0910-caddy-" + uuid.uuid4().hex[:8]
            network = ["-p", f"127.0.0.1:{front}:8080"] if mode == "docker-desktop" else ["--network", "host"]
            subprocess.run(["docker", "run", "-d", "--name", caddy, *network,
                            "-v", f"{config.resolve()}:/etc/caddy:ro", CADDY_IMAGE,
                            "caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                           check=True, capture_output=True, env=dict(os.environ, MSYS_NO_PATHCONV="1"))
        opener = build_opener()
        for port in ports:   # 각 프로세스가 직접 뜬 뒤 Caddy를 거쳐 응답하는지 확인한다
            for _ in range(200):
                try:
                    with opener.open(f"http://127.0.0.1:{port}/accounts/login", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if any(process.poll() is not None for process, _ in servers):
                        raise RuntimeError("benchmark_server_failed")
                    time.sleep(.1)
            else:
                raise RuntimeError("benchmark_server_timeout")
        for _ in range(100):
            try:
                with opener.open(origin + "/accounts/login", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.2)
        else:
            raise RuntimeError("proxy_timeout")
        time.sleep(6)   # Caddy 첫 능동 상태 확인(5초 간격)이 끝난 뒤 시작한다
        for cookie in users:
            with opener.open(Request(origin + "/players/me/progress", headers={"Cookie": cookie}), timeout=5) as r:
                if r.status != 200:
                    raise RuntimeError("warmup_failed")
                r.read()
        clients = {}
        if layer == "redis":
            clients["before"] = redis_clients(args.redis_url)
        started, started_at = time.monotonic(), datetime.now(timezone.utc).isoformat()
        with ThreadPoolExecutor(max_workers=args.users * 2 + 2) as pool:
            beacons = []
            try:
                for cookie in users:
                    ready = Event()
                    beacons.append(pool.submit(beacon_load, origin, cookie, stop, ready))
                    if not ready.wait(10):
                        raise RuntimeError("beacon_start_timeout")
                if layer == "redis":
                    sampler = Thread(target=lambda: (time.sleep(args.minutes * 30),
                                                     clients.__setitem__("during", redis_clients(args.redis_url))),
                                     daemon=True)
                    sampler.start()
                barrier = Barrier(args.users)
                futures = [pool.submit(measure_game, origin, cookie, steps, args.minutes, barrier) for cookie in users]
                results = [future.result() for future in futures]
            finally:
                stop.set()
            beacon_rows = [future.result() for future in beacons]
        elapsed = time.monotonic() - started
        per_process = {}
        for port in ports:
            with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
                per_process[str(port)] = stage_counts(r.read().decode())
        values = [sample for row in results for sample in row["samples"]]
        statuses = {}
        for row in results:
            for status, count in row["statuses"].items():
                statuses[status] = statuses.get(status, 0) + count
        command = [v for row in results for v in row["by_route"]["command"]]
        row = dict(case=name, processes=processes, channel_layer=layer, users=args.users, minutes=args.minutes,
                   started_at=started_at, elapsed_seconds=round(elapsed, 3), requests=len(values),
                   throughput_rps=round(len(values) / elapsed, 2), statuses=statuses,
                   errors=sum(r["errors"] for r in results), finished_games=sum(r["finished_games"] for r in results),
                   p50_ms=round(percentile(values, .5), 3), p95_ms=round(percentile(values, .95), 3),
                   p99_ms=round(percentile(values, .99), 3),
                   command_p95_ms=round(percentile(command, .95), 3) if command else None,
                   beacon_acks=sum(r["acks"] for r in beacon_rows), beacon_errors=sum(r["errors"] for r in beacon_rows),
                   per_process_commits=per_process, redis_clients=clients or None,
                   mongo_version=runtime.client.server_info()["version"])
        print(json.dumps(row), flush=True)
        return row
    finally:
        stop.set()
        if caddy:
            subprocess.run(["docker", "rm", "-f", caddy], capture_output=True)
        for process, log in servers:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()
        if args.keep_logs:   # 실패 원인 확인용(out/ 아래, Git 추적 안 함)
            keep = Path(args.keep_logs)
            keep.mkdir(parents=True, exist_ok=True)
            for path in folder.glob(f"{name}-*.log"):
                (keep / path.name).write_bytes(path.read_bytes())
        try:
            for collection in runtime.collection_names:
                if not collection.startswith(prefix):
                    raise AssertionError("unsafe_cleanup")
                runtime.database.drop_collection(collection)
        finally:
            runtime.close()


def main(argv=None):
    """경우별로 같은 계정·같은 부하를 차례로 실행하고 비교 보고서를 쓴다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--cases", default="p2_memory,p1_memory", help="경우 이름 p<N>_<memory|redis> 쉼표 목록")
    parser.add_argument("--web-processes", default="", help="daphne 수 목록(예: 2,1). 주면 --cases 대신 쓴다")
    parser.add_argument("--channel-layer", default="memory", choices=LAYERS, help="--web-processes와 함께 쓸 채널 레이어")
    parser.add_argument("--front", default="docker", choices=("docker", "binary"), help="앞단 Caddy 실행 방식")
    parser.add_argument("--caddy-bin", default="caddy", help="--front binary일 때 caddy 실행 파일")
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--result-name", default="", help="증빙 결과 JSON 이름(30명·3분 이상)")
    parser.add_argument("--result-task", default="E-09-10", help="증빙 결과 JSON을 둘 태스크 ID")
    parser.add_argument("--compare-asgi", action="store_true", help="C-08-04 명령 호환용. 받기만 한다")
    parser.add_argument("--keep-logs", default="", help="daphne 로그를 복사할 폴더(out/ 아래 권장)")
    args = parser.parse_args(argv)
    names = case_names(args.cases, args.web_processes, args.channel_layer)
    try:
        cases = {name: parse_case(name) for name in names}
    except ValueError as error:
        parser.error(f"경우 이름이 틀렸다: {error} (p1~p{MAX_PROCESSES}_memory|redis)")
    if not 1 <= args.users <= 60 or not 0 < args.minutes <= 10 or not names:
        parser.error("users=1..60, minutes=0..10, 경우가 하나 이상 필요하다")
    if args.result_name:
        from tools.evidence_result import NAME_RE
        from tools.tasks_md import TASK_ID_RE
        if (not NAME_RE.fullmatch(args.result_name) or args.result_name in (".", "..")
                or not TASK_ID_RE.fullmatch(args.result_task) or args.users < 30 or args.minutes < 3):
            parser.error("증빙 결과에는 안전한 이름·태스크 ID, 30명·3분 이상이 필요하다")
    if any(layer == "redis" for _, layer in cases.values()) and not args.redis_url:
        parser.error("redis 경우에는 --redis-url이 필요하다")
    root = Path(__file__).resolve().parents[2]
    uri = mongo_uri(root)
    (root / "out/handoff").mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="e0910-bench-", dir=root / "out/handoff") as temporary:
        folder = Path(temporary)
        os.environ.update(DJANGO_SETTINGS_MODULE="server.tests.asgi_bench_settings",
                          PALDO_BENCH_AUTH_DB=str(folder / "auth.sqlite3"),
                          DJANGO_SECRET_KEY=secrets.token_urlsafe(48), PALDO_PUBLIC_ORIGIN="http://127.0.0.1:8765")
        import django
        django.setup()
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from django.db import connections
        from django.test import Client
        from django.utils import timezone as dj_timezone
        from server.accounts.models import AccountProfile
        from .support import make_service
        call_command("migrate", verbosity=0, interactive=False)
        users = []
        for number in range(args.users):
            user = User.objects.create_user(username=f"e0910-{number}")
            AccountProfile.objects.create(user=user, consented_at=dj_timezone.now())
            client = Client()
            client.force_login(user)
            users.append("sessionid=" + client.cookies["sessionid"].value)
        steps = make_service().engine.steps[1:]
        rows = []
        try:
            for name in names:
                processes, layer = cases[name]
                print(json.dumps({"starting_case": name}), flush=True)
                rows.append(run_case(args, name, processes, layer, uri, users, steps, folder))
            report = {"cpu_count": os.cpu_count(), "front": front_mode(args.front), "rows": rows}
            (root / "out/handoff/e0910-multiproc-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.result_name:
                from tools.evidence_result import write_result
                command = (f"python -B tools/perf/api_bench.py --web-processes "
                           f"{','.join(str(cases[n][0]) for n in names)} --channel-layer {args.channel_layer} "
                           f"--users {args.users} --minutes {args.minutes} --front {args.front} "
                           f"--result-name {args.result_name}") if args.web_processes else \
                    (f"python -B -m server.tests.multiproc_bench --cases {','.join(names)} --users {args.users} "
                     f"--minutes {args.minutes} --front {args.front} --result-name {args.result_name}")
                path = write_result(args.result_task, args.result_name, result_metrics(rows), command, root=root)
                print(json.dumps({"result": str(path.relative_to(root)).replace(os.sep, "/")}), flush=True)
            print(json.dumps({"done": True}), flush=True)
            return 0
        finally:
            connections.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
