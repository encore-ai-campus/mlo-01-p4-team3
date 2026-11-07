"""D-05-03: 실제 localhost HTTP와 MySQL 캠페인 조회를 포함한 serve 지연 측정."""
import argparse
import hashlib
import json
import math
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from tools.evidence_result import write_result

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None):
    """단일 WSGI 프로세스를 잠시 띄워 순차 HTTP 요청을 측정하고 종료한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    args = parser.parse_args(argv)
    if args.n < 1 or args.warmup < 0:
        parser.error("n >= 1, warmup >= 0")
    run_id = uuid.uuid4().hex
    samples, filled = [], 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    logdir = ROOT / "out" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / "ads_bench_server.txt").open("w", encoding="utf-8") as logfile:
        command = [sys.executable, "-X", "utf8", str(ROOT / "ads/manage.py"), "runserver",
                   f"127.0.0.1:{port}", "--noreload", "--nothreading"]
        process = subprocess.Popen(command, cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError("광고 서버 시작 실패: out/logs/ads_bench_server.txt 확인")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=.2):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("광고 서버 시작 제한 시간 초과")
                    time.sleep(.1)
            for i in range(args.n + args.warmup):
                payload = dict(request_id=f"{run_id}-{i}", slot="lobby_banner",
                    session_pseudonym=f"bench-{run_id}-{i}", context={})
                request = Request(f"http://127.0.0.1:{port}/ads/v1/serve",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", "X-Request-ID": f"bench-{run_id}-{i}"})
                start = time.perf_counter_ns()
                with urlopen(request, timeout=5) as response:
                    out = json.load(response)
                    if response.status != 200:
                        raise RuntimeError("non-200 serve")
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
                if i >= args.warmup:
                    samples.append(elapsed)
                    filled += int(out["filled"])
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    ordered = sorted(samples)
    metrics = dict(serve_p50={"value": ordered[math.ceil(args.n * .5) - 1], "unit": "ms"},
        serve_p95={"value": ordered[math.ceil(args.n * .95) - 1], "unit": "ms"},
        serve_calls={"value": args.n, "unit": "회"},
        filled_calls={"value": filled, "unit": "회"})
    if filled != args.n:
        raise RuntimeError("filled 측정에 실패: 캠페인 기간·상태·예산과 load_ad_fixtures 실행을 확인하세요.")
    digest = hashlib.sha256()
    for path in sorted((ROOT / "ads").rglob("*.py")) + [Path(__file__).resolve()]:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    raw = dict(samples_ms=samples, warmup=args.warmup, source_sha256=digest.hexdigest(),
               campaign_fixture_sha256=hashlib.sha256((ROOT / "fixtures/ads/campaigns.json").read_bytes()).hexdigest())
    outdir = ROOT / "out" / "perf"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ads_bench.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    source = f"python -m tools.perf.ads_bench --n {args.n} --warmup {args.warmup}"
    result = write_result("D-05-03", "serve", metrics, source)
    print(json.dumps(dict(result=str(result.relative_to(ROOT)), metrics=metrics,
                         source_sha256=raw["source_sha256"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
