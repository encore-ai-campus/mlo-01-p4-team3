"""E-09-10 게임 웹 프로세스 수·채널 레이어 비교 부하(로컬 시험 전용, 운영에서 쓰지 않음).

실제 daphne N개를 띄우고 앞단 Caddy(공식 Docker 이미지 `caddy:2.10.2`, 운영 Caddyfile과 같은 분배·상태 확인 설정)를
거쳐 B-07-05와 같은 부하(사용자당 초당 한 요청의 시작·명령 + 사용자별 웹소켓 비콘 1개)를 준다.
부하 생성기(이 프로세스)와 서버(daphne 프로세스)는 분리된다. 모든 경우가 같은 Caddy 경로를 지나므로 경우끼리 비교한다.

python -B -m server.tests.multiproc_bench --users 30 --minutes 3 --cases p2_memory,p1_memory,p2_redis \
    --redis-url redis://127.0.0.1:6379/0
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
CASES = {"p1_memory": (1, "memory"), "p2_memory": (2, "memory"), "p2_redis": (2, "redis"), "p1_redis": (1, "redis")}


def free_port() -> int:
    """loopback의 빈 포트 하나를 고른다."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def caddyfile(ports, front_host) -> str:
    """운영 Caddyfile(ops/proxy/Caddyfile)의 분배·상태 확인 설정을 평문 HTTP로 옮긴다."""
    upstreams = " ".join(f"host.docker.internal:{port}" for port in ports)
    return ("{\n    admin off\n    auto_https off\n}\n\n:8080 {\n"
            f"    reverse_proxy {upstreams} {{\n"
            "        lb_policy least_conn\n        lb_try_duration 2s\n        lb_try_interval 250ms\n"
            "        fail_duration 10s\n        health_uri /healthz\n        health_interval 5s\n        health_timeout 2s\n"
            f"        health_headers {{\n            Host {front_host}\n        }}\n"
            "    }\n}\n")


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
        (config / "Caddyfile").write_text(caddyfile(ports, "127.0.0.1"), encoding="utf-8")
        caddy = "paldo-e0910-caddy-" + uuid.uuid4().hex[:8]
        subprocess.run(["docker", "run", "-d", "--name", caddy, "-p", f"127.0.0.1:{front}:8080",
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
    parser.add_argument("--cases", default="p2_memory,p1_memory")
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--keep-logs", default="", help="daphne 로그를 복사할 폴더(out/ 아래 권장)")
    args = parser.parse_args(argv)
    names = [name.strip() for name in args.cases.split(",") if name.strip()]
    if not 1 <= args.users <= 60 or not 0 < args.minutes <= 10 or not names or set(names) - set(CASES):
        parser.error(f"users=1..60, minutes=0..10, cases ⊂ {sorted(CASES)}")
    if any(CASES[name][1] == "redis" for name in names) and not args.redis_url:
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
                processes, layer = CASES[name]
                print(json.dumps({"starting_case": name}), flush=True)
                rows.append(run_case(args, name, processes, layer, uri, users, steps, folder))
            report = {"cpu_count": os.cpu_count(), "rows": rows}
            (root / "out/handoff/e0910-multiproc-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"done": True}), flush=True)
            return 0
        finally:
            connections.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
