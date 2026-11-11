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
- 기본은 녹화 엔진이다. --actual-baseline은 실제 엔진의 의심 모델을 끄고, --mlruns와 --suspicion-version은 고정 모델을 켠다.
  두 모드 모두 시험 접두어 컬렉션을 사용하고 이 도구가 Caddy와 웹 프로세스를 직접 띄운다.
- B-09-02 준비: `--intent-every N`은 말하기 단계의 N번째 명령마다 해석·취소를 추가한다(0은 기존 부하).
  `--intent-factory`로 시험 해석기를 선택한다. 전체 API p95에는 두 요청을 포함하고 순수 해석 지연도 따로 기록한다.
  분리 부하는 서버 쪽에서 간격과 팩토리를 지정하고, 클라이언트는 교환 파일의 간격만 읽는다.
Mongo는 PALDO_MONGO_URI(api_bench.mongo_uri 규칙)로 받는다. 결과는 out/handoff/e0910-multiproc-report.json에 쓴다.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from ipaddress import IPv4Address
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
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from .api_bench import mongo_uri, percentile
from .asgi_bench import beacon_load, measure_game
from .bench_diagnostics import access_summary, cpu_sample, cpu_summary, with_ping

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


def free_port(host="127.0.0.1") -> int:
    """loopback의 빈 포트 하나를 고른다."""
    with socket.socket() as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def caddyfile(ports, front_host, listen=":8080", upstream_host="host.docker.internal", client_ip=None) -> str:
    """운영 Caddyfile(ops/proxy/Caddyfile)의 분배·상태 확인 설정을 평문 HTTP로 옮긴다."""
    upstreams = " ".join(f"{upstream_host}:{port}" for port in ports)
    bind_host = listen.rsplit(":", 1)[0]
    bind = f"    bind {bind_host}\n" if bind_host else ""
    restriction = (f"    @outside not remote_ip {IPv4Address(client_ip)} {IPv4Address(front_host)}\n"
                   "    respond @outside 403\n") if client_ip else ""
    return (f"{{\n    admin off\n    auto_https off\n}}\n\nhttp://{listen} {{\n{bind}"
            f"{restriction}"
            + ("    log\n" if client_ip else "")
            + f"    reverse_proxy {upstreams} {{\n"
            "        lb_policy least_conn\n        lb_try_duration 2s\n        lb_try_interval 250ms\n"
            "        fail_duration 10s\n        health_uri /healthz\n        health_interval 5s\n        health_timeout 5s\n"
            "        health_fails 3\n        health_passes 1\n"
            f"        health_headers {{\n            Host {front_host}\n        }}\n"
            "    }\n}\n")


RESULT_KEYS = ("requests", "errors", "throughput_rps", "p50_ms", "p95_ms", "p99_ms", "command_p95_ms",
               "beacon_acks", "beacon_errors", "intent_requests", "cancel_requests", "intent_http_p95_ms",
               "intent_parse_p95_ms", "inference_calls", "inference_failures", "inference_p95_upper_ms",
               "inference_fallback_percent", "resident_memory_bytes", "worker_count")


def result_metrics(rows) -> dict:
    """경우별 행을 증빙 결과 JSON metrics(`<경우>_<항목>`)로 바꾼다."""
    metrics = {}
    for row in rows:
        for key in RESULT_KEYS:
            if row.get(key) is not None:
                unit = ("ms" if key.endswith("_ms") else "bytes" if key.endswith("_bytes") else
                        "%" if key.endswith("_percent") else "rps" if key == "throughput_rps" else "count")
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
    engine_fallbacks = 0
    for scrape in scrapes.values():
        for family in text_string_to_metric_families(scrape):
            for sample in family.samples:
                if (sample.name == 'paldo_model_decisions_total'
                        and sample.labels.get('model_version') == version
                        and sample.labels.get('result') == 'fallback'):
                    engine_fallbacks += int(sample.value)
    committed = sum(
        int(sample.value)
        for scrape in scrapes.values()
        for family in text_string_to_metric_families(scrape)
        for sample in family.samples
        if sample.name == 'paldo_engine_suspicion_fallback_total'
    )
    total = sum(calls.values())
    rank = math.ceil(total * .95)
    upper_ms = next((upper * 1000 for upper in sorted(buckets)
                     if rank and math.isfinite(upper) and buckets[upper] >= rank), None)
    return dict(inference_calls=total, inference_failures=failures, inference_p95_upper_ms=upper_ms,
                inference_calls_by_process=calls, engine_fallback_decisions=engine_fallbacks,
                engine_committed_fallbacks=committed)


def model_load_passed(row) -> bool:
    """실제 게임 완료와 모든 프로세스의 추론을 확인하고 지연·오류 관문을 적용한다."""
    if row.get('secondary_model_version') and any(
            m['web_rss_bytes'] is None or m['web_rss_bytes'] <= 0 or len(m['worker_rss_bytes']) != 2
            or any(rss <= 0 for rss in m['worker_rss_bytes'].values())
            for m in row.get('memory_by_process', {}).values()):
        return False
    if row.get('secondary_model_version') and set(row.get('memory_by_process', {})) != set(
            row['inference_calls_by_process']):
        return False
    return (row["errors"] == 0 and row["finished_games"] > 0
            and row["beacon_errors"] == 0 and row["beacon_acks"] > 0 and row["p95_ms"] <= 1000
            and row["inference_calls"] > 0 and max(row["inference_failures"], row.get("engine_fallback_decisions", 0),
                    row.get("engine_committed_fallbacks", 0)) / row["inference_calls"] <= .01
            and row["inference_p95_upper_ms"] is not None and row["inference_p95_upper_ms"] <= 100
            and bool(row["inference_calls_by_process"]) and all(row["inference_calls_by_process"].values()))


def resident_bytes(pid):
    """Linux 시험 서버의 현재 RSS. 다른 OS나 종료된 PID는 측정 불가(None)."""
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def memory_metrics(scrapes):
    """웹과 worker의 RSS를 프로세스별로 보존한다. RSS는 공유 페이지를 중복 포함한다."""
    from prometheus_client.parser import text_string_to_metric_families
    rows = {}
    for port, scrape in scrapes.items():
        web, workers = None, {}
        for family in text_string_to_metric_families(scrape):
            for sample in family.samples:
                if sample.name == 'process_resident_memory_bytes':
                    web = sample.value
                elif sample.name == 'paldo_suspicion_worker_resident_memory_bytes':
                    workers[sample.labels['model_version']] = sample.value
        rows[port] = dict(web_rss_bytes=web, worker_rss_bytes=workers)
    return rows


def recovery_metrics(paths):
    """자체 시험 로그에서 timeout부터 다음 성공까지의 시간과 미복구 건수를 읽는다."""
    result = {}
    for path in paths:
        events, reasons = [], Counter()
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get('component') != 'suspicion_model':
                continue
            if item.get('reason'):
                reasons[item['reason']] += 1
            if item.get('msg') == 'timeout 이후 동일 버전 추론 복구':
                events.append(dict(model_version=item['model_version'], duration_ms=item['duration_ms']))
        timeouts = reasons['model timeout']
        result[path.name] = dict(timeouts=timeouts, recoveries=events, unresolved=timeouts - len(events),
                                 fallback_reasons=dict(reasons))
    return result


def redis_clients(url):
    """Redis 연결 수(connected_clients). Redis 경우가 아니면 None."""
    import redis
    client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        return int(client.info("clients")["connected_clients"])
    finally:
        client.close()


def drive_load(origin, cookies, steps, minutes, intent_every=0, redis_url=None, *, diagnostics=False):
    """격리 서버 주소에 게임·비콘 부하만 보낸다. DB와 서버 프로세스를 다루지 않는다."""
    opener = build_opener()
    for cookie in cookies:
        with opener.open(Request(origin + '/players/me/progress', headers={'Cookie': cookie}), timeout=5) as r:
            r.read()
    clients = {'before': redis_clients(redis_url)} if redis_url else {}
    started, started_at = time.monotonic(), datetime.now(timezone.utc).isoformat()
    stop = Event()
    with ThreadPoolExecutor(max_workers=len(cookies) * 2 + 2) as pool:
        beacons = []
        try:
            for cookie in cookies:
                ready = Event()
                beacons.append(pool.submit(beacon_load, origin, cookie, stop, ready))
                if not ready.wait(10):
                    raise RuntimeError('beacon_start_timeout')
            if redis_url:
                def sample_redis():
                    if not stop.wait(minutes * 30):
                        clients['during'] = redis_clients(redis_url)

                Thread(target=sample_redis, daemon=True).start()
            window = {}
            barrier = Barrier(len(cookies), action=lambda: window.update(start=time.time()))
            futures = [pool.submit(measure_game, origin, cookie, steps, minutes, barrier,
                                   intent_every=intent_every, diagnostics=diagnostics) for cookie in cookies]
            results = [future.result() for future in futures]
            window['end'] = time.time()
        finally:
            stop.set()
        beacon_rows = [future.result() for future in beacons]
    elapsed = time.monotonic() - started
    values = [sample for row in results for sample in row['samples']]
    statuses = {}
    for row in results:
        for status, count in row['statuses'].items():
            statuses[status] = statuses.get(status, 0) + count
    command, intent, cancel = ([v for row in results for v in row['by_route'][route]]
                               for route in ('command', 'utterance', 'cancel'))
    parses = [r['stages']['intent_parse'] for row in results for r in row['request_timings']
              if r['route'] == 'utterance' and 'intent_parse' in r['stages']]
    row = dict(users=len(cookies), minutes=minutes, started_at=started_at, elapsed_seconds=round(elapsed, 3),
                requests=len(values), throughput_rps=round(len(values) / elapsed, 2), statuses=statuses,
                errors=sum(r['errors'] for r in results), finished_games=sum(r['finished_games'] for r in results),
                p50_ms=round(percentile(values, .5), 3), p95_ms=round(percentile(values, .95), 3),
                p99_ms=round(percentile(values, .99), 3),
                command_p95_ms=round(percentile(command, .95), 3) if command else None,
                beacon_acks=sum(r['acks'] for r in beacon_rows), beacon_errors=sum(r['errors'] for r in beacon_rows),
                redis_clients=clients or None, intent_requests=len(intent), cancel_requests=len(cancel),
                intent_http_p95_ms=round(percentile(intent, .95), 3) if intent else None,
                intent_parse_p95_ms=round(percentile(parses, .95), 3) if parses else None)
    if diagnostics:
        row['window'] = window
        row['client_requests'] = [timing for result in results for timing in result['request_timings']]
    return row


def external_load(origin, cookies, args):
    """시험 계정 상태를 비공개 파일로 넘기고 별도 호스트의 결과를 기다린다."""
    folder = Path(args.serve_dir)
    folder.mkdir(mode=0o700)
    result = folder / 'load.json'
    state = folder / 'client.json'
    with state.open('x', encoding='utf-8') as stream:
        json.dump(dict(origin=origin, cookies=cookies, minutes=args.minutes,
                       intent_every=args.intent_every), stream)
    state.chmod(0o600)
    print(json.dumps(dict(client_state=str(state), origin=origin)), flush=True)
    deadline = time.monotonic() + args.minutes * 60 + 120
    try:
        while not result.exists() and time.monotonic() < deadline:
            time.sleep(.2)
        if not result.exists():
            raise RuntimeError('external_load_result_timeout')
        row = json.loads(result.read_text(encoding='utf-8'))
        reported_interval = row.get('intent_every', 0)
        if (row['users'] != len(cookies) or row['elapsed_seconds'] < args.minutes * 60
                or type(reported_interval) is not int or reported_interval != args.intent_every):
            raise RuntimeError('external_load_does_not_match_requested_workload')
        return row
    finally:
        state.unlink(missing_ok=True)


def private_ipv4(value):
    """부하 시험의 외부 바인딩과 클라이언트는 RFC1918 주소로 제한한다."""
    address = IPv4Address(value)
    a, b, *_ = address.packed
    if not (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)):
        raise ValueError('expected a private IPv4 address')
    return str(address)


def load_only(folder):
    """SSH로 받은 시험 전용 세션으로 실제 게임 부하를 실행하고 결과만 기록한다."""
    state = json.loads((folder / 'client.json').read_text(encoding='utf-8'))
    origin = urlsplit(state['origin'])
    if (origin.scheme != 'http' or origin.netloc != f'{private_ipv4(origin.hostname)}:{origin.port}'
            or not origin.port or origin.path or origin.query or origin.fragment):
        raise ValueError('invalid benchmark origin')
    cookies = state['cookies']
    if not 1 <= len(cookies) <= 60 or not all(re.fullmatch(r'sessionid=[a-z0-9]+', c) for c in cookies):
        raise ValueError('invalid benchmark sessions')
    minutes = float(state['minutes'])
    if not 0 < minutes <= 10:
        raise ValueError('invalid benchmark duration')
    intent_every = state.get('intent_every', 0)
    if type(intent_every) is not int or not 0 <= intent_every <= 100:
        raise ValueError('invalid benchmark intent interval')
    row = with_ping(origin.hostname, folder, lambda: drive_load(
        state['origin'], cookies, None, minutes, intent_every=intent_every, diagnostics=True))
    row['intent_every'] = intent_every
    row['load_generator'] = socket.gethostname()
    with (folder / 'load.json').open('x', encoding='utf-8') as stream:
        json.dump(row, stream)
    print(json.dumps({key: value for key, value in row.items() if key != 'client_requests'}), flush=True)
    return 0


def run_case(args, name, processes, layer, uri, users, steps, folder):
    """daphne N개·Caddy·부하를 실행하고 자기 자원만 정리한다."""
    from engine.testing.fakes import FixtureContentSource, ScriptedGameEngine
    from server.paldo_web.mongo import open_mongo_runtime
    host = args.listen_host
    ports, front = [free_port() for _ in range(processes)], free_port(host)
    origin = f"http://{host}:{front}"
    prefix = "b0705_" + uuid.uuid4().hex + "_"   # asgi_bench_app이 받는 시험 접두어 형식
    runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None, uri=uri, collection_prefix=prefix)
    servers, caddy = [], None
    try:
        content = FixtureContentSource().load("*")
        runtime.content.insert(content)
        startup_started_at = time.time()
        for port in ports:
            env = dict(os.environ, PALDO_MONGO_URI=uri, PALDO_PUBLIC_ORIGIN=origin, PALDO_BENCH_PREFIX=prefix,
                       PYTHONUNBUFFERED='1',
                       PALDO_LOG_DIR=str(folder / f'{name}-{port}-structured'),
                       PALDO_BENCH_PORT=str(port), PALDO_BENCH_SCENARIO="normal", PALDO_CHANNEL_LAYER=layer,
                       PALDO_WEB_INSTANCE=str(port), PALDO_CONTENT_VERSION=content.content_version,
                       PALDO_BENCH_ACTUAL_BASELINE=str(int(args.actual_baseline)),
                       PALDO_BENCH_MLRUNS=args.mlruns, PALDO_BENCH_SUSPICION_VERSION=args.suspicion_version,
                       PALDO_BENCH_SECONDARY_SUSPICION_VERSION=args.secondary_suspicion_version,
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
            text = caddyfile(ports, host, listen=f"{host}:{front}", upstream_host="127.0.0.1", client_ip=args.client_ip)
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
        server_ready_at = time.time()
        def scrape(phase):
            samples = {}
            for port in ports:
                with opener.open(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
                    samples[str(port)] = response.read().decode()
                if args.serve_dir:
                    (folder / f'{name}-{port}-{phase}.prom').write_text(samples[str(port)], encoding='utf-8')
            return samples

        if args.serve_dir:
            before = scrape('before')
            cpu = []
            stop = Event()

            def sample_cpu():
                while not stop.is_set():
                    cpu.append(cpu_sample())
                    stop.wait(0.5)

            monitor = Thread(target=sample_cpu, daemon=True)
            monitor.start()
            try:
                load = external_load(origin, users, args)
            finally:
                stop.set()
                monitor.join()
            (folder / f'{name}-cpu.json').write_text(json.dumps(cpu), encoding='utf-8')
            requests = load.pop('client_requests')
            (folder / f'{name}-requests.json').write_text(json.dumps(requests), encoding='utf-8')
            load.update(cpu_summary(cpu, load['window']))
            load.update(access_summary(folder / f'{name}-caddy.log', requests))
            load['counters_before'] = inference_metrics(before, args.suspicion_version)
        else:
            load = drive_load(origin, users, steps, args.minutes, args.intent_every,
                              args.redis_url if layer == 'redis' else None)
        scrapes = scrape('after')
        row = dict(load, case=name, processes=processes, channel_layer=layer,
                   server_started_at=startup_started_at, server_ready_at=server_ready_at,
                   warmup_seconds=round(server_ready_at - startup_started_at, 3),
                   per_process_commits={port: stage_counts(scrape) for port, scrape in scrapes.items()},
                   mongo_version=runtime.client.server_info()["version"])
        if args.serve_dir or args.suspicion_version:
            row.update(inference_metrics(scrapes, args.suspicion_version), model_version=args.suspicion_version or None)
        if args.suspicion_version:
            row['inference_fallback_percent'] = (100 * row['engine_fallback_decisions'] / row['inference_calls']
                                                 if row['inference_calls'] else 100)
            row['memory_by_process'] = memory_metrics(scrapes)
            memories = row['memory_by_process'].values()
            row['resident_memory_bytes'] = sum((m['web_rss_bytes'] or 0) + sum(m['worker_rss_bytes'].values())
                                               for m in memories)
            row['worker_count'] = sum(len(m['worker_rss_bytes']) for m in row['memory_by_process'].values())
            row['secondary_model_version'] = args.secondary_suspicion_version or None
            recovery = {}
            for port in ports:
                path = folder / f'{name}-{port}-structured' / 'ci.jsonl'
                if path.exists():
                    recovery[str(port)] = recovery_metrics([path])[path.name]
            row['timeout_recovery_by_process'] = recovery
            reasons = Counter()
            for item in recovery.values():
                reasons.update(item['fallback_reasons'])
            row['fallback_reasons'] = dict(reasons, unattributed=max(0, row['engine_fallback_decisions'] - sum(reasons.values())))
        row.update(engine='actual' if args.suspicion_version or args.actual_baseline else 'scripted', intent_every=args.intent_every,
                   intent_parser_factory=args.intent_factory if args.intent_every else None)
        print(json.dumps(row), flush=True)
        return row
    finally:
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
            for path in folder.glob(f"{name}-*"):
                if path.is_file():
                    (keep / path.name).write_bytes(path.read_bytes())
            for path in folder.glob(f'{name}-*-structured/ci.jsonl'):
                (keep / f'{path.parent.name}.jsonl').write_bytes(path.read_bytes())
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
    parser.add_argument("--actual-baseline", action="store_true", help="실제 엔진에 의심 모델을 주지 않는다")
    parser.add_argument("--mlruns", default="", help="실제 엔진 시험용 신뢰된 로컬 모델 디렉터리")
    parser.add_argument("--suspicion-version", default="", help="실제 엔진 시험용 고정 모델 버전")
    parser.add_argument('--secondary-suspicion-version', default='',
                        help='두 버전 공존 메모리 시험용 추가 worker(게임은 주 모델로 실행)')
    parser.add_argument("--redis-url", default="")
    parser.add_argument("--result-name", default="", help="증빙 결과 JSON 이름(30명·3분 이상)")
    parser.add_argument("--result-task", default="E-09-10", help="증빙 결과 JSON을 둘 태스크 ID")
    parser.add_argument("--compare-asgi", action="store_true", help="C-08-04 명령 호환용. 받기만 한다")
    parser.add_argument("--keep-logs", default="", help="daphne 로그를 복사할 폴더(out/ 아래 권장)")
    parser.add_argument('--intent-every', type=int, default=0,
                        help='N번째 명령의 말하기 단계마다 해석·취소 추가. 0은 기존 부하')
    parser.add_argument('--intent-factory', default='intent.parser.BaselineIntentParser',
                        help='시험 프로세스에서 한 번 생성할 IntentParser 팩토리. 운영 설정은 변경하지 않음')
    roles = parser.add_mutually_exclusive_group()
    roles.add_argument('--serve-dir', help='웹만 실행하고 새 비공개 교환 폴더에 client.json을 기록')
    roles.add_argument('--load-dir', help='SSH로 받은 client.json으로 부하만 생성하고 load.json을 기록')
    parser.add_argument('--listen-host', default='127.0.0.1', help='서버 전용 모드의 사설 IPv4 바인딩')
    parser.add_argument('--client-ip', help='서버 전용 모드에서 허용하는 부하 생성기 사설 IPv4')
    args = parser.parse_args(argv)
    if args.load_dir:
        if args.intent_every:
            parser.error('--intent-every는 --serve-dir 쪽에서 지정한다')
        return load_only(Path(args.load_dir))
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
    if args.actual_baseline and args.suspicion_version:
        parser.error('--actual-baseline과 --suspicion-version을 함께 쓸 수 없다')
    if bool(args.mlruns) != bool(args.suspicion_version):
        parser.error("--mlruns와 --suspicion-version은 함께 지정해야 한다")
    if args.secondary_suspicion_version and (not args.suspicion_version or
                                            args.secondary_suspicion_version == args.suspicion_version):
        parser.error('추가 worker는 주 모델과 다른 등록 버전이 필요하다')
    if args.serve_dir:
        try:
            args.listen_host = private_ipv4(args.listen_host)
            args.client_ip = private_ipv4(args.client_ip)
        except ValueError as error:
            parser.error(str(error))
        if (args.client_ip == args.listen_host or args.front != 'binary' or len(cases) != 1
                or not (args.suspicion_version or args.actual_baseline)
                or any(layer != 'memory' for _, layer in cases.values())):
            parser.error('서버 전용 모드는 외부 호스트, binary, 단일 memory 구성과 실제 엔진을 사용한다')
    elif args.listen_host != '127.0.0.1' or args.client_ip:
        parser.error('외부 주소는 서버 전용 모드에서만 사용한다')
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
        steps = None if args.suspicion_version or args.actual_baseline else make_service().engine.steps[1:]
        rows = []
        try:
            for name in names:
                processes, layer = cases[name]
                print(json.dumps({"starting_case": name}), flush=True)
                rows.append(run_case(args, name, processes, layer, uri, users, steps, folder))
            report = {"cpu_count": os.cpu_count(), "front": front_mode(args.front), "rows": rows}
            report_name = "d0801-model-load-report.json" if args.suspicion_version or args.actual_baseline else "e0910-multiproc-report.json"
            (root / "out/handoff" / report_name).write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.result_name:
                from tools.evidence_result import write_result
                command = (f"python -B tools/perf/api_bench.py --web-processes "
                           f"{','.join(str(cases[n][0]) for n in names)} --channel-layer {args.channel_layer} "
                           f"--users {args.users} --minutes {args.minutes} --front {args.front} "
                           f"--result-name {args.result_name}") if args.web_processes else \
                    (f"python -B -m server.tests.multiproc_bench --cases {','.join(names)} --users {args.users} "
                     f"--minutes {args.minutes} --front {args.front} --result-name {args.result_name}")
                if args.actual_baseline:
                    command += ' --actual-baseline'
                if args.suspicion_version:
                    command += f' --mlruns "{args.mlruns}" --suspicion-version {args.suspicion_version}'
                if args.secondary_suspicion_version:
                    command += f' --secondary-suspicion-version {args.secondary_suspicion_version}'
                if args.serve_dir:
                    command += ' --serve-dir <private-dir> --listen-host <private-ip> --client-ip <generator-ip>'
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
