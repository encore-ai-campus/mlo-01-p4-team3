"""실제 루프백 HTTP·웹소켓과 Compose Mongo의 B-07-05 비교 시험.

python -B tools/perf/api_bench.py --compare-asgi --users 10 --minutes 3 --result-name compose_10x3
WSGI/Daphne 기준선, 정상 광고·비콘, 3초 광고 지연, 광고 실패를 같은 명령 부하로 비교한다.
계정 DB·Mongo 컬렉션·서버는 시험 전용이며 URI·쿠키·로그인 ID는 결과에 넣지 않는다.
"""
import argparse
import base64
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
from random import Random
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import time
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from .api_bench import mongo_uri, percentile
from .ws_smoke import _send, _receive, _recv_exact


def actual_command(public_view, rng):
    """직전 API 응답의 공개 상태에서 합법 명령과 낙관적 잠금 버전을 만든다."""
    from engine.domain.serde import from_jsonable, to_jsonable
    from engine.ports import PlayerView
    from tools.perf.engine_bench import bench_player_command

    view = from_jsonable(PlayerView, public_view)
    return {"expected_state_version": view.state_version,
            "command": to_jsonable(bench_player_command(view, rng))}


def measure_game(origin, cookie, steps, minutes, barrier, *, intent_every=0):
    """독립 판을 초당 한 주기로 진행한다. steps=None이면 실제 공개 상태를 따른다."""
    csrf = secrets.token_hex(16)
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {"Cookie": cookie + "; csrftoken=" + csrf, "X-CSRFToken": csrf,
               "Accept": "application/json", "Content-Type": "application/json"}
    samples, statuses, errors, finished = [], {}, 0, 0
    by_route = {"start": [], "command": [], "utterance": [], "cancel": []}
    request_timings = []

    def send(route, path, body):
        # 해석·취소도 오류 표본까지 전체 게임 API p95에 포함한다.
        started = time.monotonic()
        server_timing = {}
        try:
            with opener.open(Request(origin + path, json.dumps(body).encode(), headers), timeout=5) as response:
                status, data = response.status, json.load(response)
                server_timing = json.loads(response.headers.get('X-Paldo-Bench-Timing', '{}'))
                if not isinstance(data, dict):
                    data = {}
        except HTTPError as error:
            status, data = error.code, {}
            error.close()
        except (OSError, ValueError):
            status, data = 0, {}
        elapsed = (time.monotonic() - started) * 1000
        samples.append(elapsed)
        request_timings.append(dict(route=route, client_ms=round(elapsed, 3), stages=server_timing))
        by_route[route].append(elapsed)
        statuses[str(status)] = statuses.get(str(status), 0) + 1
        return status, data

    game, step, view, command_number = None, 0, None, 0
    actual = steps is None
    rng = Random(0)
    barrier.wait(timeout=30)
    deadline = time.monotonic() + minutes * 60
    while time.monotonic() < deadline:
        started = time.monotonic()
        if game is None:
            path, body = "/games", dict(preset_id="base6", session_id=None if actual else "test-session",
                                        mudang_variant="f")
            if actual:
                body["start_token"] = str(uuid.uuid4())
                body["replace_open"] = True  # 오류 뒤 재시작은 진행 판을 중단하고 새로 시작한다(11/10 사용자 결정).
        else:
            from .intent_load import probe_intent, should_probe
            command_number += 1
            if should_probe(view, command_number, intent_every):
                errors += probe_intent(view, send)
            path = f"/games/{game}/commands"
            request = actual_command(view, rng) if actual else steps[step]["request"]
            body = dict(request, game_id=game, command_id=str(uuid.uuid4()))
        status, data = send('start' if game is None else 'command', path, body)
        view = data.get('view')
        if status != 200 or not data.get("view") or game is not None and not data.get("accepted"):
            errors += 1
            game, step = None, 0
        elif actual:
            view = data["view"]
            game = view["game_id"]
            if view["result"] is not None:
                finished += 1
                game = None
        elif game is None:
            game = data["view"]["game_id"]
        else:
            step += 1
            if step == len(steps):
                finished += 1
                game, step = None, 0
        time.sleep(max(0, min(1 - (time.monotonic() - started), deadline - time.monotonic())))
    return {"samples": samples, "statuses": statuses, "errors": errors, "finished_games": finished,
            "by_route": by_route, 'request_timings': request_timings}


def beacon_load(origin, cookie, stop, ready, token=None):
    """사용자별 인증 소켓에서 초당 비콘 한 개를 보내 ack를 확인한다."""
    host = origin.removeprefix("http://")
    port = int(host.split(":")[1])
    counts = {"acks": 0, "relayed": 0, "failed": 0, "errors": 0}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            key = base64.b64encode(secrets.token_bytes(16)).decode()
            sock.sendall((f"GET /ws/game/ HTTP/1.1\r\nHost: {host}\r\nOrigin: {origin}\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                          f"Sec-WebSocket-Key: {key}\r\nCookie: {cookie}\r\n\r\n").encode())
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += _recv_exact(sock, 1)
            if not header.startswith(b"HTTP/1.1 101"):
                raise RuntimeError("websocket_handshake_failed")
            _send(sock, {"type": "hello", "dropped": 0, "dropped_by_reason": {"queue_full": 0, "page_exit": 0}})
            ready.set()
            while not stop.is_set():
                started = time.monotonic()
                msg_id = str(uuid.uuid4())
                _send(sock, {"type": "ad.impression", "msg_id": msg_id, "token": token or msg_id,
                             "visible_ratio": .5, "visible_ms": 1000, "occurred_at_ms": int(time.time() * 1000)})
                opcode, payload = _receive(sock)
                while opcode == 9:
                    # Daphne의 연결 유지 ping에는 마스킹한 pong으로 응답한다.
                    mask = secrets.token_bytes(4)
                    sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask +
                                 bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
                    opcode, payload = _receive(sock)
                ack = json.loads(payload)
                if opcode != 1 or ack.get("type") != "ack" or ack.get("msg_id") != msg_id:
                    raise RuntimeError("unexpected_beacon_response")
                counts["acks"] += 1
                counts["relayed" if ack["relayed"] else "failed"] += 1
                stop.wait(max(0, 1 - (time.monotonic() - started)))
    except Exception:
        counts["errors"] += 1
    finally:
        ready.set()
    return counts


def ad_load(origin, cookie, stop):
    """게임 명령과 별도 HTTP로 광고를 매초 읽는다. 빈 광고도 정상 응답이다."""
    counts = {"requests": 0, "errors": 0}
    opener = build_opener()
    while not stop.is_set():
        started = time.monotonic()
        try:
            with opener.open(Request(origin + "/ads/slot/lobby_banner", headers={"Cookie": cookie}), timeout=5) as r:
                if r.status != 200:
                    counts["errors"] += 1
                r.read()
        except OSError:
            counts["errors"] += 1
        counts["requests"] += 1
        stop.wait(max(0, 1 - (time.monotonic() - started)))
    return counts


def assess(rows):
    """모든 시나리오의 오류·p95·장애 증가분을 판정한다. 오류 표본도 포함한다."""
    baseline = next(row for row in rows if row["case"] == "daphne_ads")
    for row in rows:
        row["p95_increase_ms"] = round(row["p95_ms"] - baseline["p95_ms"], 3)
    return (all(row["errors"] == 0 and row["p95_ms"] <= 1000 and row["finished_games"] > 0 for row in rows)
            and all(row["p95_increase_ms"] <= 50 for row in rows if row["case"] in {"daphne_delay", "daphne_failure"})
            and all(row["beacon_acks"] > 0 and row["beacon_errors"] == 0 and row["ad_errors"] == 0
                    for row in rows if row["case"] not in {"wsgi", "daphne"})
            and baseline["beacon_relayed"] > 0
            and all(row["beacon_failed"] > 0 for row in rows if row["case"] in {"daphne_delay", "daphne_failure"}))



def stage_counts(lines):
    """단계 히스토그램 줄에서 단계별 호출 수(load=게임 상태 조회 등)만 꺼낸다."""
    counts = {}
    for line in lines:
        if line.startswith('paldo_bench_stage_seconds_count{stage="'):
            stage = line.split('stage="', 1)[1].split('"', 1)[0]
            counts[stage] = int(float(line.rsplit(' ', 1)[1]))
    return counts

def run_case(args, name, transport, scenario, mixed, uri, users, steps, folder,
             *, extra_env=None, ad_worker=None, beacon_worker=None):
    """시나리오별 서버·부하·Mongo 감사를 실행하고 자기 자원만 정리한다."""
    from engine.testing.fakes import ScriptedGameEngine, FixtureContentSource
    from server.paldo_web.mongo import open_mongo_runtime
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    prefix = "b0705_" + uuid.uuid4().hex + "_"
    runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None, uri=uri, collection_prefix=prefix)
    process, stop = None, Event()
    try:
        runtime.content.insert(FixtureContentSource().load("*"))
        env = dict(os.environ, PALDO_MONGO_URI=uri, PALDO_PUBLIC_ORIGIN=origin,
                   PALDO_BENCH_PREFIX=prefix, PALDO_BENCH_PORT=str(port), PALDO_BENCH_SCENARIO=scenario)
        env.update(extra_env or {})
        command = ([sys.executable, "-B", "-m", "server.tests.asgi_bench_app"] if transport == "wsgi" else
                   [sys.executable, "-B", "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
                    "server.tests.asgi_bench_app:application"])
        with (folder / f"{name}.log").open("wb") as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            opener = build_opener()
            for _ in range(150):
                try:
                    with opener.open(origin + "/accounts/login", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("benchmark_server_failed")
                    time.sleep(.1)
            else:
                raise RuntimeError("benchmark_server_timeout")
            # 측정 전 같은 계정 DB의 사용자별 진척을 읽어 예열한다.
            for cookie in users:
                with opener.open(Request(origin + "/players/me/progress", headers={"Cookie": cookie}), timeout=5) as r:
                    if r.status != 200:
                        raise RuntimeError("warmup_failed")
                    r.read()
            started = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()
            with ThreadPoolExecutor(max_workers=args.users * 3) as pool:
                beacons, ads = [], []
                try:
                    if mixed:
                        for cookie in users:
                            ready = Event()
                            beacons.append(pool.submit(beacon_worker or beacon_load, origin, cookie, stop, ready))
                            if not ready.wait(10):
                                raise RuntimeError("beacon_start_timeout")
                            ads.append(pool.submit(ad_worker or ad_load, origin, cookie, stop))
                    barrier = Barrier(args.users)
                    futures = [pool.submit(measure_game, origin, cookie, steps, args.minutes, barrier) for cookie in users]
                    results = [future.result() for future in futures]
                finally:
                    stop.set()
                beacon_rows, ad_rows = [f.result() for f in beacons], [f.result() for f in ads]
            with opener.open(origin + "/metrics", timeout=5) as r:
                metrics = r.read().decode()
            values = [sample for row in results for sample in row["samples"]]
            documents = list(runtime.game.repository.collection.find({}, {"outbox": 1}))
            events = [row["envelope"] for doc in documents for row in doc["outbox"]]
            if not events or not all(event["load_test"] for event in events):
                raise AssertionError("load_test_mark_missing")
            statuses = {}
            for row in results:
                for status, count in row["statuses"].items():
                    statuses[status] = statuses.get(status, 0) + count
            row = dict(case=name, transport=transport, users=args.users, minutes=args.minutes,
                       elapsed_seconds=round(time.monotonic() - started, 3), requests=len(values), statuses=statuses,
                       errors=sum(r["errors"] for r in results), finished_games=sum(r["finished_games"] for r in results),
                       p50_ms=round(percentile(values, .5), 3), p95_ms=round(percentile(values, .95), 3),
                       beacon_acks=sum(r["acks"] for r in beacon_rows), beacon_relayed=sum(r["relayed"] for r in beacon_rows),
                       beacon_failed=sum(r["failed"] for r in beacon_rows), beacon_errors=sum(r["errors"] for r in beacon_rows),
                       ad_requests=sum(r["requests"] for r in ad_rows), ad_errors=sum(r["errors"] for r in ad_rows),
                       load_test_events=len(events), mongo_version=runtime.client.server_info()["version"],
                       relay_metrics=[line for line in metrics.splitlines() if line.startswith("paldo_ad_relay_total{")])
            if ad_worker:
                row['click_requests'] = sum(r['clicks'] for r in ad_rows)
                row['click_errors'] = sum(r['click_errors'] for r in ad_rows)
                if all('outcomes' in r for r in ad_rows):
                    row['click_outcomes'] = {k: sum(r['outcomes'][k] for r in ad_rows) for k in ad_rows[0]['outcomes']}
                    click_ms = [v for r in ad_rows for v in r['click_ms']]
                    row['click_p95_ms'] = round(percentile(click_ms, .95), 3) if click_ms else None
            row['started_at'] = started_at
            row['ended_at'] = datetime.now(timezone.utc).isoformat()
            row['stage_metrics'] = [line for line in metrics.splitlines() if line.startswith('paldo_bench_stage_seconds_') and not line.startswith('paldo_bench_stage_seconds_created')]
            row['stage_counts'] = stage_counts(row['stage_metrics'])
            row['routes'] = {}
            row['request_timings'] = [sample for result in results for sample in result['request_timings']]
            for route in ('start', 'command'):
                route_values = [value for result in results for value in result['by_route'][route]]
                row['routes'][route] = dict(requests=len(route_values), p95_ms=round(percentile(route_values, .95), 3)) if route_values else dict(requests=0)
            print(json.dumps(row), flush=True)
            return row
    finally:
        stop.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            for collection in runtime.collection_names:
                if not collection.startswith(prefix):
                    raise AssertionError("unsafe_cleanup")
                runtime.database.drop_collection(collection)
        finally:
            runtime.close()


def main(argv=None):
    """다섯 시나리오를 비교해 오류·p95·장애 증가분 기준 위반 시 실패한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--result-name")
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 60 or not 0 < args.minutes <= 10:
        parser.error("users=1..60, minutes=0..10")
    if args.result_name:
        from tools.evidence_result import NAME_RE
        if not NAME_RE.fullmatch(args.result_name) or args.result_name in (".", "..") or args.users < 10 or args.minutes < 3:
            parser.error("evidence requires safe name, >=10 users and >=3 minutes")
    root = Path(__file__).resolve().parents[2]
    uri = mongo_uri(root)
    (root / "out/handoff").mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="b0705-auth-", dir=root / "out/handoff") as temporary:
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
        from django.utils import timezone
        from server.accounts.models import AccountProfile
        from .support import make_service
        call_command("migrate", verbosity=0, interactive=False)
        users = []
        for number in range(args.users):
            user = User.objects.create_user(username=f"b0705-{number}")
            AccountProfile.objects.create(user=user, consented_at=timezone.now())
            client = Client()
            client.force_login(user)
            users.append("sessionid=" + client.cookies["sessionid"].value)
        steps = make_service().engine.steps[1:]
        rows = []
        try:
            for name, transport, scenario, mixed in [("wsgi", "wsgi", "normal", False),
                    ("daphne", "daphne", "normal", False), ("daphne_ads", "daphne", "normal", True),
                    ("daphne_delay", "daphne", "delay", True), ("daphne_failure", "daphne", "failure", True)]:
                print(json.dumps({"starting_case": name}), flush=True)
                rows.append(run_case(args, name, transport, scenario, mixed, uri, users, steps, folder))
            passed = assess(rows)
            report = {"passed": passed, "rows": rows}
            (root / "out/handoff/b0705-network-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            if args.result_name:
                from tools.evidence_result import write_result
                metrics = {}
                for row in rows:
                    for key in ("requests", "errors", "finished_games", "p50_ms", "p95_ms", "p95_increase_ms",
                                "beacon_acks", "beacon_relayed", "beacon_failed", "beacon_errors", "ad_requests", "ad_errors"):
                        metrics[row["case"] + "_" + key] = {"value": row[key], "unit": "ms" if key.endswith("ms") else "count"}
                write_result("B-07-05", args.result_name, metrics,
                             f"python -B tools/perf/api_bench.py --compare-asgi --users {args.users} --minutes {args.minutes} --result-name {args.result_name}")
            print(json.dumps({"passed": passed}), flush=True)
            return 0 if passed else 1
        finally:
            connections.close_all()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"error": "network_benchmark_failed", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(2)
