"""E-09-10 게임 웹 프로세스 수·채널 레이어 비교 부하(로컬 시험 전용, 운영에서 쓰지 않음).

실제 daphne N개를 띄우고 앞단 Caddy(공식 Docker 이미지 `caddy:2.10.2`, 운영 Caddyfile과 같은 분배·상태 확인 설정)를
거쳐 B-07-05와 같은 부하(사용자당 초당 한 요청의 시작·명령 + 사용자별 웹소켓 비콘 1개)를 준다.
부하 생성기(이 프로세스)와 서버(daphne 프로세스)는 분리된다. 모든 경우가 같은 Caddy 경로를 지나므로 경우끼리 비교한다.

python tools/perf/api_bench.py --web-processes 2,1 --users 30 --minutes 3            # 2개·1개, memory
python tools/perf/api_bench.py --web-processes 2 --channel-layer redis --redis-url redis://127.0.0.1:6379/0
python tools/perf/api_bench.py --web-processes 2 --front binary --mlruns mlruns --suspicion-version <버전>
python -B -m server.tests.multiproc_bench --cases p2_memory,p1_memory,p2_redis --redis-url ...   # 경우 이름으로 지정
- `--web-processes`: 띄울 daphne 수 목록(1~4, 쉼표). `--channel-layer`(memory 기본·redis)와 묶어 경우 `p<N>_<layer>`가 된다.
- `--result-name <이름>`: 증빙 결과 JSON(`docs/evidence/perf/<--result-task, 기본 E-09-10>/<이름>.json`)도 쓴다.
  30명·3분 이상일 때만. `--compare-asgi`는 C-08-04 명령과 맞추려고 받기만 한다(이 도구는 늘 앞단 경유 비교다).
- `--front`: 앞단 Caddy를 띄우는 방법. `docker`(기본, 공식 이미지. Linux는 host 네트워크로 loopback daphne에 닿는다)
  또는 `binary`(이미 설치된 caddy 실행 파일, `--caddy-bin`. 시험 서버처럼 Docker가 없는 곳).
- 기본은 녹화 엔진이다. --mlruns와 --suspicion-version을 함께 지정하면 실제 엔진과 고정 버전 모델을 사용한다.
  두 모드 모두 시험 접두어 컬렉션을 사용하고 이 도구가 Caddy와 웹 프로세스를 직접 띄운다.
- B-09-02 준비: `--intent-every N`은 말하기 단계의 N번째 명령마다 해석·취소를 추가한다(0은 기존 부하).
  `--intent-factory`로 시험 해석기를 선택한다. 전체 API p95에는 두 요청을 포함하고 순수 해석 지연도 따로 기록한다.
Mongo는 PALDO_MONGO_URI(api_bench.mongo_uri 규칙)로 받는다. 결과는 out/handoff/e0910-multiproc-report.json에 쓴다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import os
import re
from pathlib import Path
import secrets
import signal
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
    bind_host = listen.rsplit(":", 1)[0]
    bind = f"    bind {bind_host}\n" if bind_host else ""
    return (f"{{\n    admin off\n    auto_https off\n}}\n\nhttp://{listen} {{\n{bind}"
            f"    reverse_proxy {upstreams} {{\n"
            "        lb_policy least_conn\n        lb_try_duration 2s\n        lb_try_interval 250ms\n"
            "        fail_duration 10s\n        health_uri /healthz\n        health_interval 5s\n        health_timeout 5s\n"
            "        health_fails 3\n        health_passes 1\n"
            f"        health_headers {{\n            Host {front_host}\n        }}\n"
            "    }\n}\n")


RESULT_KEYS = ("requests", "errors", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "command_p95_ms",
               "beacon_acks", "beacon_errors", "intent_requests", "cancel_requests", "intent_http_p95_ms",
               "intent_parse_p95_ms", "inference_calls", "inference_failures", "inference_p95_upper_ms")


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
    counts = {"commit": 0.0}
    for line in metrics_text.splitlines():
        for stage in ('commit', 'intent_parse'):
            if line.startswith("paldo_bench_stage_seconds_count") and f'stage="{stage}"' in line:
                counts[stage] = float(line.rsplit(" ", 1)[1])
    return counts


def inference_metrics(scrapes, version) -> dict:
    """같은 버전의 모든 결과·프로세스를 합산해 p95가 속한 버킷 상한을 계산한다."""
    from prometheus_client.parser import text_string_to_metric_families

    buckets = {}
    calls = dict.fromkeys(scrapes, 0)
    failures = 0
    for process, scrape in scrapes.items():
        for family in text_string_to_metric_families(scrape):
            for sample in family.samples:
                if (sample.name != "paldo_model_inference_duration_seconds_bucket"
                        or sample.labels["model_version"] != version):
                    continue
                upper = float(sample.labels["le"])
                count = int(sample.value)
                buckets[upper] = buckets.get(upper, 0) + count
                if math.isinf(upper):
                    calls[process] += count
                    if sample.labels["result"] != "ok":
                        failures += count
    total = sum(calls.values())
    rank = math.ceil(total * .95)
    upper_ms = next((upper * 1000 for upper in sorted(buckets)
                     if rank and math.isfinite(upper) and buckets[upper] >= rank), None)
    return dict(inference_calls=total, inference_failures=failures, inference_p95_upper_ms=upper_ms,
                inference_calls_by_process=calls)


def model_load_passed(row) -> bool:
    """실제 게임 완료와 모든 프로세스의 추론을 확인하고 지연·오류 관문을 적용한다."""
    return (row["errors"] == 0 and row["finished_games"] > 0
            and row["beacon_errors"] == 0 and row["beacon_acks"] > 0 and row["p95_ms"] <= 1000
            and row["inference_calls"] > 0 and row["inference_failures"] == 0
            and row["inference_p95_upper_ms"] is not None and row["inference_p95_upper_ms"] <= 100
            and bool(row["inference_calls_by_process"]) and all(row["inference_calls_by_process"].values()))


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
        content = FixtureContentSource().load("*")
        runtime.content.insert(content)
        for port in ports:
            env = dict(os.environ, PALDO_MONGO_URI=uri, PALDO_PUBLIC_ORIGIN=origin, PALDO_BENCH_PREFIX=prefix,
                       PALDO_BENCH_PORT=str(port), PALDO_BENCH_SCENARIO="normal", PALDO_CHANNEL_LAYER=layer,
                       PALDO_WEB_INSTANCE=str(port), PALDO_CONTENT_VERSION=content.content_version,
                       PALDO_BENCH_MLRUNS=args.mlruns, PALDO_BENCH_SUSPICION_VERSION=args.suspicion_version,
                       PALDO_BENCH_INTENT_FACTORY=args.intent_factory if args.intent_every else '')
            if layer == "redis":
                env["PALDO_REDIS_URL"] = args.redis_url
            log = (folder / f"{name}-{port}.log").open("wb")
            servers.append((subprocess.Popen(
                [sys.executable, "-B", "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
                 "server.tests.asgi_bench_app:application"], env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
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
                start_new_session=sys.platform != "win32",
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
                futures = [pool.submit(measure_game, origin, cookie, steps, args.minutes, barrier,
                                       intent_every=args.intent_every) for cookie in users]
                results = [future.result() for future in futures]
            finally:
                stop.set()
            beacon_rows = [future.result() for future in beacons]
        elapsed = time.monotonic() - started
        scrapes = {}
        for port in ports:
            with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
                scrapes[str(port)] = r.read().decode()
        values = [sample for row in results for sample in row["samples"]]
        statuses = {}
        for row in results:
            for status, count in row["statuses"].items():
                statuses[status] = statuses.get(status, 0) + count
        command = [v for row in results for v in row["by_route"]["command"]]
        intent = [v for result in results for v in result['by_route']['utterance']]
        cancel = [v for result in results for v in result['by_route']['cancel']]
        parses = [r['stages']['intent_parse'] for result in results for r in result['request_timings']
                  if r['route'] == 'utterance' and 'intent_parse' in r['stages']]
        row = dict(case=name, processes=processes, channel_layer=layer, users=args.users, minutes=args.minutes,
                   started_at=started_at, elapsed_seconds=round(elapsed, 3), requests=len(values),
                   throughput_rps=round(len(values) / elapsed, 2), statuses=statuses,
                   errors=sum(r["errors"] for r in results), finished_games=sum(r["finished_games"] for r in results),
                   p50_ms=round(percentile(values, .5), 3), p95_ms=round(percentile(values, .95), 3),
                   p99_ms=round(percentile(values, .99), 3),
                   command_p95_ms=round(percentile(command, .95), 3) if command else None,
                   beacon_acks=sum(r["acks"] for r in beacon_rows), beacon_errors=sum(r["errors"] for r in beacon_rows),
                   per_process_commits={port: stage_counts(scrape) for port, scrape in scrapes.items()},
                   redis_clients=clients or None,
                   mongo_version=runtime.client.server_info()["version"])
        if args.suspicion_version:
            row.update(inference_metrics(scrapes, args.suspicion_version), model_version=args.suspicion_version)
        row.update(engine='actual' if args.suspicion_version else 'scripted', intent_every=args.intent_every,
                   intent_parser_factory=args.intent_factory if args.intent_every else None,
                   intent_requests=len(intent), cancel_requests=len(cancel),
                   intent_http_p95_ms=round(percentile(intent, .95), 3) if intent else None,
                   intent_parse_p95_ms=round(percentile(parses, .95), 3) if parses else None)
        print(json.dumps(row), flush=True)
        return row
    finally:
        stop.set()
        if caddy:
            subprocess.run(["docker", "rm", "-f", caddy], capture_output=True)
        for process, log in servers:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
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
            collections = [*runtime.collection_names]
            if args.intent_every:
                collections.append(prefix + 'b_interactions')
            for collection in collections:
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
    parser.add_argument("--mlruns", default="", help="실제 엔진 시험용 신뢰된 로컬 모델 디렉터리")
    parser.add_argument("--suspicion-version", default="", help="실제 엔진 시험용 고정 모델 버전")
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--result-name", default="", help="증빙 결과 JSON 이름(30명·3분 이상)")
    parser.add_argument("--result-task", default="E-09-10", help="증빙 결과 JSON을 둘 태스크 ID")
    parser.add_argument("--compare-asgi", action="store_true", help="C-08-04 명령 호환용. 받기만 한다")
    parser.add_argument("--keep-logs", default="", help="daphne 로그를 복사할 폴더(out/ 아래 권장)")
    parser.add_argument('--intent-every', type=int, default=0,
                        help='N번째 명령의 말하기 단계마다 해석·취소 추가. 0은 기존 부하')
    parser.add_argument('--intent-factory', default='intent.parser.BaselineIntentParser',
                        help='시험 프로세스에서 한 번 생성할 IntentParser 팩토리. 운영 설정은 변경하지 않음')
    args = parser.parse_args(argv)
    if not 0 <= args.intent_every <= 100:
        parser.error('intent-every=0..100')
    if args.intent_every and not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+', args.intent_factory):
        parser.error('intent-factory에는 Python 모듈의 공개 팩토리 경로가 필요하다')
    names = case_names(args.cases, args.web_processes, args.channel_layer)
    try:
        cases = {name: parse_case(name) for name in names}
    except ValueError as error:
        parser.error(f"경우 이름이 틀렸다: {error} (p1~p{MAX_PROCESSES}_memory|redis)")
    if not 1 <= args.users <= 60 or not 0 < args.minutes <= 10 or not names:
        parser.error("users=1..60, minutes=0..10, 경우가 하나 이상 필요하다")
    if bool(args.mlruns) != bool(args.suspicion_version):
        parser.error("--mlruns와 --suspicion-version은 함께 지정해야 한다")
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
        steps = None if args.suspicion_version else make_service().engine.steps[1:]
        rows = []
        try:
            for name in names:
                processes, layer = cases[name]
                print(json.dumps({"starting_case": name}), flush=True)
                rows.append(run_case(args, name, processes, layer, uri, users, steps, folder))
            report = {"cpu_count": os.cpu_count(), "front": front_mode(args.front), "rows": rows}
            report_name = "d0801-model-load-report.json" if args.suspicion_version else "e0910-multiproc-report.json"
            (root / "out/handoff" / report_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.result_name:
                from tools.evidence_result import write_result
                command = (f"python -B tools/perf/api_bench.py --web-processes "
                           f"{','.join(str(cases[n][0]) for n in names)} --channel-layer {args.channel_layer} "
                           f"--users {args.users} --minutes {args.minutes} --front {args.front} "
                           f"--result-name {args.result_name}") if args.web_processes else \
                    (f"python -B -m server.tests.multiproc_bench --cases {','.join(names)} --users {args.users} "
                     f"--minutes {args.minutes} --front {args.front} --result-name {args.result_name}")
                if args.suspicion_version:
                    command += f' --mlruns "{args.mlruns}" --suspicion-version {args.suspicion_version}'
                if args.intent_every:
                    command += f' --intent-every {args.intent_every} --intent-factory {args.intent_factory}'
                command += f' --result-task {args.result_task}'
                path = write_result(args.result_task, args.result_name, result_metrics(rows), command, root=root)
                print(json.dumps({"result": str(path.relative_to(root)).replace(os.sep, "/")}), flush=True)
            passed = not args.suspicion_version or all(model_load_passed(row) for row in rows)
            if args.intent_every and any(row['errors'] or row['beacon_errors'] or
                    not row['intent_requests'] or row['intent_requests'] != row['cancel_requests'] or
                    row['intent_parse_p95_ms'] is None for row in rows):
                passed = False
            print(json.dumps({"done": True, "passed": passed}), flush=True)
            return 0 if passed else 1
        finally:
            connections.close_all()


if __name__ == "__main__":
    raise SystemExit(main())
