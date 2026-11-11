"""E-09-10 게임 웹 다중 프로세스·채널 레이어 설정 시험.

- 설정 전환(memory 기본·redis 선택), Redis 연결 실패 시 기동 거부, 프로세스별 로그 파일
- 실행 도구 포트, Caddy upstream·상태 확인, Prometheus 수집 대상이 서로 맞는지
- Redis 통합(두 프로세스 사이 그룹 메시지): `PALDO_TEST_REDIS_URL`이 있을 때만 실행한다.
  로컬 예: docker compose -p paldo-dev -f ops/compose/dev.yml --profile redis up -d redis 뒤
  PALDO_TEST_REDIS_URL=redis://127.0.0.1:6379/15. 없으면 외부 서비스가 없는 환경이라 건너뛴다.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ops.mysql import service_runtime
from server.paldo_web.channel_layers import (
    ChannelLayerConfigError, channel_layers, check_reachable, layer_kind, uses_redis)

ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = (ROOT / "ops" / "proxy" / "Caddyfile").read_text(encoding="utf-8")
PROMETHEUS = (ROOT / "ops" / "compose" / "prometheus.yml").read_text(encoding="utf-8")
HAS_REDIS_PACKAGE = importlib.util.find_spec("channels_redis") is not None
TEST_REDIS_URL = os.environ.get("PALDO_TEST_REDIS_URL", "")


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _django(env_extra: dict, code: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PALDO_CHANNEL", "PALDO_REDIS", "PALDO_WEB_INSTANCE"))}
    env = {k: v for k, v in env.items() if k != "PALDO_EXTRA_ALLOWED_HOSTS"}
    # 하위 프로세스 출력(한글 오류 문구)을 시스템 코드 페이지와 상관없이 UTF-8로 주고받는다.
    env.update(DJANGO_SETTINGS_MODULE="server.paldo_web.test_settings", PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8",
               **env_extra)
    return subprocess.run([sys.executable, "-B", "-c", "import django;django.setup();" + code], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


class ChannelLayerConfig(unittest.TestCase):
    def test_memory_is_default_and_explicit(self):
        for env in ({}, {"PALDO_CHANNEL_LAYER": ""}, {"PALDO_CHANNEL_LAYER": "memory"}, {"PALDO_CHANNEL_LAYER": " Memory "}):
            layers = channel_layers(env)
            self.assertEqual(layers, {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}, env)
            self.assertFalse(uses_redis(layers))

    def test_redis_needs_valid_url(self):
        for url in ("", "127.0.0.1:6379", "http://127.0.0.1:6379/0", "redis://"):
            with self.assertRaises(ChannelLayerConfigError) as raised:
                channel_layers({"PALDO_CHANNEL_LAYER": "redis", "PALDO_REDIS_URL": url})
            self.assertIn("PALDO_REDIS_URL", str(raised.exception))

    def test_redis_config(self):
        layers = channel_layers({"PALDO_CHANNEL_LAYER": "redis", "PALDO_REDIS_URL": "redis://127.0.0.1:6379/0"})
        self.assertTrue(uses_redis(layers))
        self.assertEqual(layers["default"]["BACKEND"], "channels_redis.core.RedisChannelLayer")
        self.assertEqual(layers["default"]["CONFIG"], {"hosts": ["redis://127.0.0.1:6379/0"], "prefix": "paldo-web"})
        custom = channel_layers({"PALDO_CHANNEL_LAYER": "REDIS", "PALDO_REDIS_URL": "rediss://cache.test:6380/1",
                                 "PALDO_REDIS_PREFIX": "pj4"})
        self.assertEqual(custom["default"]["CONFIG"]["prefix"], "pj4")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ChannelLayerConfigError):
            layer_kind({"PALDO_CHANNEL_LAYER": "kafka"})

    def test_memory_needs_no_connection_check(self):
        check_reachable(channel_layers({}))

    @unittest.skipUnless(HAS_REDIS_PACKAGE, "channels_redis 미설치(requirements/web.txt)")
    def test_unreachable_redis_stops_startup_without_leaking_url(self):
        url = f"redis://:secret-value@127.0.0.1:{_closed_port()}/0"
        layers = channel_layers({"PALDO_CHANNEL_LAYER": "redis", "PALDO_REDIS_URL": url})
        with self.assertRaises(RuntimeError) as raised:
            check_reachable(layers, timeout=0.5)
        self.assertIn("PALDO_CHANNEL_LAYER=memory", str(raised.exception))
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)


class SettingsWiring(unittest.TestCase):
    def test_settings_default_memory_and_log_file(self):
        result = _django({}, "from django.conf import settings as s;print(s.CHANNEL_LAYERS['default']['BACKEND'], s.WEB_LOG_FILE)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["channels.layers.InMemoryChannelLayer", "web.jsonl"])

    def test_second_process_writes_own_log_file(self):
        result = _django({"PALDO_WEB_INSTANCE": "8002"}, "from django.conf import settings as s;print(s.WEB_LOG_FILE)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "web-8002.jsonl")
        bad = _django({"PALDO_WEB_INSTANCE": "../x"}, "pass")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("PALDO_WEB_INSTANCE", bad.stderr)

    def test_extra_allowed_hosts_for_metrics_scrape(self):
        code = "from server.paldo_web import settings as s;print(','.join(s.ALLOWED_HOSTS))"  # 시험 설정이 덮기 전 값
        base = _django({}, code)
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertNotIn("host.docker.internal", base.stdout)
        extra = _django({"PALDO_EXTRA_ALLOWED_HOSTS": " Host.Docker.Internal ,127.0.0.1"}, code)
        self.assertEqual(extra.returncode, 0, extra.stderr)
        self.assertTrue(extra.stdout.strip().endswith(",host.docker.internal,127.0.0.1"))
        for bad in ("*", ".example.com", "host:8000", "a b"):
            result = _django({"PALDO_EXTRA_ALLOWED_HOSTS": bad}, "pass")
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("PALDO_EXTRA_ALLOWED_HOSTS", result.stderr)

    def test_redis_without_url_is_improperly_configured(self):
        result = _django({"PALDO_CHANNEL_LAYER": "redis"}, "pass")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ImproperlyConfigured", result.stderr)
        self.assertIn("PALDO_REDIS_URL", result.stderr)

    @unittest.skipUnless(HAS_REDIS_PACKAGE, "channels_redis 미설치(requirements/web.txt)")
    def test_asgi_refuses_to_start_when_redis_unreachable(self):
        result = _django({"PALDO_CHANNEL_LAYER": "redis", "PALDO_REDIS_URL": f"redis://127.0.0.1:{_closed_port()}/0"},
                         "import server.paldo_web.asgi")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Redis 채널 레이어에 연결할 수 없습니다", result.stderr)


class RuntimePorts(unittest.TestCase):
    def _run(self, *args):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / "profile.json"
            profile.write_text(json.dumps({"source": folder, "environment": {"PALDO_X": "1"}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(service_runtime.os, "chdir"), \
                    mock.patch.object(service_runtime.os, "execv") as execv:
                service_runtime.main(["--profile", str(profile), *args])
                return execv.call_args[0][1], dict(os.environ)

    def test_web_default_port_and_second_port(self):
        argv, env = self._run("web")
        self.assertEqual(argv[-4:], ["127.0.0.1", "-p", "8000", "server.paldo_web.asgi:application"])
        self.assertEqual(env["PALDO_WEB_INSTANCE"], "8000")
        argv, env = self._run("web", "--port", "8002")
        self.assertIn("8002", argv)
        self.assertEqual(env["PALDO_WEB_INSTANCE"], "8002")

    def test_web_rejects_unknown_port(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            self._run("web", "--port", "8001")

    def test_caddy_upstreams_match_runtime_ports(self):
        block = re.search(r"reverse_proxy ([^{\n]+)\{(.*?)\n        \}", CADDYFILE, re.S)
        self.assertIsNotNone(block)
        upstreams = block.group(1).split()
        self.assertEqual(upstreams, [f"127.0.0.1:{port}" for port in service_runtime.WEB_PORTS])
        body = block.group(2)
        for directive in ("lb_policy least_conn", "health_uri /healthz", "lb_try_duration", "fail_duration",
                          "health_timeout 5s", "health_fails 3"):
            self.assertIn(directive, body)
        # Django ALLOWED_HOSTS는 공개 호스트만 허용하므로 상태 확인도 같은 Host를 보낸다.
        self.assertRegex(body, r"health_headers \{\s*Host \{\$PALDO_PROXY_HOST\}\s*\}")
        self.assertIn("header_up X-Forwarded-For {remote_host}", body)

    def test_prometheus_scrapes_each_web_process(self):
        for port, label in zip(service_runtime.WEB_PORTS, ("web-1", "web-2")):
            self.assertRegex(PROMETHEUS, rf'targets: \["host\.docker\.internal:{port}"\]\s*\n\s*labels: \{{process: {label}\}}')


class BenchOptions(unittest.TestCase):
    """C-08-04 측정용 비교 부하 옵션(`--web-processes`·`--front`)."""

    def test_web_processes_build_case_names(self):
        from server.tests import multiproc_bench as bench
        self.assertEqual(bench.case_names("p2_memory", "2,1", "memory"), ["p2_memory", "p1_memory"])
        self.assertEqual(bench.case_names("p2_memory,p1_redis", "", "memory"), ["p2_memory", "p1_redis"])
        self.assertEqual(bench.parse_case("p4_redis"), (4, "redis"))
        for bad in ("p0_memory", "p5_memory", "p2_kafka", "x2_memory", "p_memory"):
            with self.assertRaises(ValueError):
                bench.parse_case(bad)

    def test_front_mode_reaches_loopback_daphne(self):
        from server.tests import multiproc_bench as bench
        self.assertEqual(bench.front_mode("docker", "linux"), "docker-host")
        self.assertEqual(bench.front_mode("docker", "win32"), "docker-desktop")
        self.assertEqual(bench.front_mode("binary", "win32"), "binary")
        host = bench.caddyfile([9001, 9002], "127.0.0.1", listen="127.0.0.1:9100", upstream_host="127.0.0.1")
        self.assertIn("http://127.0.0.1:9100 {\n    bind 127.0.0.1", host)
        self.assertIn("reverse_proxy 127.0.0.1:9001 127.0.0.1:9002 {", host)
        desktop = bench.caddyfile([9001], "127.0.0.1")
        self.assertIn("http://:8080 {", desktop)
        self.assertIn("reverse_proxy host.docker.internal:9001 {", desktop)

    def test_bench_caddy_matches_production_tuning(self):
        from server.tests import multiproc_bench as bench
        generated = bench.caddyfile([9001], "127.0.0.1")
        for line in CADDYFILE.splitlines():
            if line.strip().startswith(("lb_", "fail_duration", "health_uri", "health_interval", "health_timeout",
                                        "health_fails", "health_passes")):
                self.assertIn(line.strip(), generated)

    def test_result_metrics_per_case(self):
        from server.tests import multiproc_bench as bench
        metrics = bench.result_metrics([{"case": "p2_memory", "requests": 10, "p95_ms": 700.5, "throughput_rps": 3.0,
                                         "command_p95_ms": None}])
        self.assertEqual(metrics, {"p2_memory_requests": {"value": 10, "unit": "count"},
                                   "p2_memory_p95_ms": {"value": 700.5, "unit": "ms"},
                                   "p2_memory_throughput_rps": {"value": 3.0, "unit": "rps"}})

    def test_model_inference_uses_weighted_buckets_and_counts_failures(self):
        """두 프로세스의 누적 버킷과 실패를 같은 표본 집합으로 합산한다."""
        from server.tests.multiproc_bench import inference_metrics

        def scrape(version, result, fast, slow):
            labels = f'model_version="{version}",result="{result}"'
            return ('# TYPE paldo_model_inference_duration_seconds histogram\n'
                    f'paldo_model_inference_duration_seconds_bucket{{{labels},le="0.1"}} {fast}\n'
                    f'paldo_model_inference_duration_seconds_bucket{{{labels},le="0.25"}} {slow}\n'
                    f'paldo_model_inference_duration_seconds_bucket{{{labels},le="+Inf"}} {slow}\n')

        version = "model-v0"
        rows = {"8001": scrape(version, "ok", 100, 100),
                "8002": scrape(version, "fallback", 0, 4) + scrape("other", "ok", 0, 100)}
        rows["8002"] += (
            '# TYPE paldo_model_decisions_total counter\n'
            'paldo_model_decisions_total{model_version="model-v0",result="fallback"} 6\n'
            '# TYPE paldo_engine_suspicion_fallback_total counter\n'
            'paldo_engine_suspicion_fallback_total 5\n'
        )
        self.assertEqual(inference_metrics(rows, version), {
            "inference_calls": 104, "inference_failures": 4, "inference_p95_upper_ms": 100,
            "inference_calls_by_process": {"8001": 100, "8002": 4},
            "engine_fallback_decisions": 6, "engine_committed_fallbacks": 5,
        })
        self.assertIsNone(inference_metrics({"8001": ""}, version)["inference_p95_upper_ms"])

    def test_real_model_verdict_requires_games_and_inference_on_every_process(self):
        """미호출·오류·미완주를 빠른 응답으로 위장할 수 없다."""
        from server.tests.multiproc_bench import model_load_passed

        row = dict(errors=0, finished_games=1, beacon_errors=0, beacon_acks=30, p95_ms=1000,
                   inference_calls=20, inference_failures=0, inference_p95_upper_ms=100,
                   inference_calls_by_process={"8000": 10, "8002": 10})
        self.assertTrue(model_load_passed(row))
        self.assertTrue(model_load_passed(dict(row, inference_calls=100, inference_failures=1)))
        self.assertFalse(model_load_passed(dict(row, inference_calls=100, inference_failures=2)))
        memory = {port: dict(web_rss_bytes=4096, worker_rss_bytes={'v0': 1024, 'v1': 2048})
                  for port in row['inference_calls_by_process']}
        secondary = dict(row, secondary_model_version='v1', memory_by_process=memory)
        self.assertTrue(model_load_passed(secondary))
        self.assertFalse(model_load_passed(dict(secondary, memory_by_process={})))
        self.assertFalse(model_load_passed(dict(secondary, memory_by_process={'8000': memory['8000']})))
        self.assertFalse(model_load_passed(dict(secondary, memory_by_process={
            **memory, '8002': dict(web_rss_bytes=None, worker_rss_bytes={'v0': 1024})})))
        for change in (dict(errors=1), dict(finished_games=0), dict(inference_calls=0),
                       dict(inference_failures=1), dict(inference_p95_upper_ms=None),
                       dict(inference_p95_upper_ms=250), dict(p95_ms=1000.1),
                       dict(inference_calls_by_process={"8000": 20, "8002": 0})):
            with self.subTest(change=change):
                self.assertFalse(model_load_passed(dict(row, **change)))

    def test_api_bench_delegates_process_comparison(self):
        sys.path.insert(0, str(ROOT / "tools" / "perf"))
        try:
            import api_bench as cli
        finally:
            sys.path.remove(str(ROOT / "tools" / "perf"))
        with mock.patch.object(cli.subprocess, "call", return_value=0) as call:
            cli.main(["--web-processes", "2,1", "--users", "3"])
            self.assertIn("server.tests.multiproc_bench", call.call_args[0][0])
            cli.main(["--compare-asgi", "--web-processes=2"])
            self.assertIn("server.tests.multiproc_bench", call.call_args[0][0])
            cli.main(["--users", "3"])
            self.assertIn("server.tests.api_bench", call.call_args[0][0])
            cli.main(["--load-dir", "out/test"])
            self.assertIn("server.tests.multiproc_bench", call.call_args[0][0])

    def test_external_client_uses_only_load_driver(self):
        from server.tests import multiproc_bench as bench
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            host = '.'.join(map(str, (10, 0, 0, 1)))  # 실제 인프라 주소를 쓰지 않는 시험용 RFC1918 주소.
            state = dict(origin=f'http://{host}:1234', cookies=['sessionid=abc'], minutes=3)
            (path / 'client.json').write_text(json.dumps(state), encoding='utf-8')
            with mock.patch.object(bench, 'drive_load', return_value={'users': 1}) as load, \
                    mock.patch.object(bench, 'mongo_uri', side_effect=AssertionError('client must not use Mongo')), \
                    mock.patch.object(bench, 'with_ping', side_effect=lambda host, folder, drive: drive()):
                self.assertEqual(bench.main(['--load-dir', directory]), 0)
                load.assert_called_once_with(state['origin'], state['cookies'], None, 3, diagnostics=True)
                self.assertEqual(json.loads((path / 'load.json').read_text())['users'], 1)

    def test_proxy_samples_match_client_ids_and_cpu_uses_the_load_window(self):
        from server.tests.bench_diagnostics import access_summary, cpu_summary
        requests = [dict(bench_id='a', stages={'server_total': 10})]
        event = dict(logger='http.log.access', request={'headers': {'X-Paldo-Bench-Request-Id': ['a']}},
                     duration=.012)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'caddy.log'
            path.write_text(json.dumps(event), encoding='utf-8')
            self.assertEqual(access_summary(path, requests),
                             dict(caddy_samples=1, caddy_p95_ms=12, django_samples=1, django_p95_ms=10))
        result = cpu_summary([(0, 0, 0), (1, 100, 40), (2, 200, 60), (3, 400, 100)],
                             dict(start=1, end=2))
        self.assertEqual(result['host_cpu_percent'], 80)
        self.assertEqual(result['cpu_samples'], 2)

    def test_memory_and_timeout_recovery_preserve_missing_measurements(self):
        from server.tests.multiproc_bench import memory_metrics, recovery_metrics, resident_bytes
        scrape = ('# TYPE process_resident_memory_bytes gauge\nprocess_resident_memory_bytes 4096\n'
                  '# TYPE paldo_suspicion_worker_resident_memory_bytes gauge\n'
                  'paldo_suspicion_worker_resident_memory_bytes{model_version="a"} 1024\n'
                  'paldo_suspicion_worker_resident_memory_bytes{model_version="b"} 2048\n')
        self.assertEqual(memory_metrics({'9000': scrape, '9001': ''}), {
            '9000': dict(web_rss_bytes=4096, worker_rss_bytes={'a': 1024, 'b': 2048}),
            '9001': dict(web_rss_bytes=None, worker_rss_bytes={})})
        with mock.patch('pathlib.Path.read_text', return_value='VmRSS:\t12 kB\n'):
            self.assertEqual(resident_bytes(123), 12288)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.log'
            path.write_text('not json\n' + '\n'.join(json.dumps(row, ensure_ascii=False) for row in (
                dict(component='suspicion_model', reason='model timeout'),
                dict(component='suspicion_model', msg='timeout 이후 동일 버전 추론 복구',
                     model_version='a', duration_ms=42),
                dict(component='suspicion_model', reason='model timeout'))), encoding='utf-8')
            self.assertEqual(recovery_metrics([path])['worker.log'], dict(timeouts=2, unresolved=1,
                fallback_reasons={'model timeout': 2},
                recoveries=[dict(model_version='a', duration_ms=42)]))


@unittest.skipUnless(HAS_REDIS_PACKAGE and TEST_REDIS_URL, "PALDO_TEST_REDIS_URL 없음(로컬 Redis가 없는 환경)")
class RedisAcrossProcesses(unittest.TestCase):
    def test_group_message_reaches_other_process(self):
        import asyncio
        from channels_redis.core import RedisChannelLayer
        prefix = "paldo-test-" + os.urandom(4).hex()
        config = {"hosts": [TEST_REDIS_URL], "prefix": prefix}

        async def scenario():
            layer = RedisChannelLayer(**config)
            channel = await layer.new_channel()
            await layer.group_add("game-room", channel)
            sender = (
                "import asyncio,json,sys\n"
                "from channels_redis.core import RedisChannelLayer\n"
                "async def m():\n"
                f"    l=RedisChannelLayer(**json.loads({json.dumps(json.dumps(config))}))\n"
                "    await l.group_send('game-room',{'type':'game.update','pid':__import__('os').getpid()})\n"
                "    await l.close_pools()\n"
                "asyncio.run(m())\n")
            child = await asyncio.create_subprocess_exec(sys.executable, "-B", "-c", sender, cwd=str(ROOT))
            self.assertEqual(await child.wait(), 0)
            message = await asyncio.wait_for(layer.receive(channel), 5)
            await layer.group_discard("game-room", channel)
            await layer.close_pools()
            return message, child.pid

        message, child_pid = asyncio.run(scenario())
        self.assertEqual(message["type"], "game.update")
        self.assertNotEqual(message["pid"], os.getpid())

    def test_idle_connection_channel_survives_blocking_wait(self):
        """채널 수신 대기가 블로킹 읽기 시한(5초)을 넘겨도 끊기지 않는다. redis 8.1.0에서 실패해 7.4.1로 고정."""
        import asyncio
        from channels_redis.core import RedisChannelLayer

        async def scenario():
            layer = RedisChannelLayer(hosts=[TEST_REDIS_URL], prefix="paldo-test-" + os.urandom(4).hex())
            channel = await layer.new_channel()
            try:
                await asyncio.wait_for(layer.receive(channel), 8)
            except asyncio.TimeoutError:
                return "idle_ok"
            finally:
                await layer.close_pools()
            return "unexpected_message"

        self.assertEqual(asyncio.run(scenario()), "idle_ok")


if __name__ == "__main__":
    unittest.main()
