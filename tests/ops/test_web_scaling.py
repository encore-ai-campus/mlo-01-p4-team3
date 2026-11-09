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
    env.update(DJANGO_SETTINGS_MODULE="server.paldo_web.test_settings", PYTHONPATH=str(ROOT), **env_extra)
    return subprocess.run([sys.executable, "-B", "-c", "import django;django.setup();" + code], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


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
        for directive in ("lb_policy least_conn", "health_uri /healthz", "lb_try_duration", "fail_duration"):
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
        self.assertIn("127.0.0.1:9100 {", host)
        self.assertIn("reverse_proxy 127.0.0.1:9001 127.0.0.1:9002 {", host)
        self.assertIn("reverse_proxy host.docker.internal:9001 {", bench.caddyfile([9001], "127.0.0.1"))

    def test_result_metrics_per_case(self):
        from server.tests import multiproc_bench as bench
        metrics = bench.result_metrics([{"case": "p2_memory", "requests": 10, "p95_ms": 700.5, "throughput_rps": 3.0,
                                         "command_p95_ms": None}])
        self.assertEqual(metrics, {"p2_memory_requests": {"value": 10, "unit": "count"},
                                   "p2_memory_p95_ms": {"value": 700.5, "unit": "ms"},
                                   "p2_memory_throughput_rps": {"value": 3.0, "unit": "rps"}})

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
