"""로컬 개발 환경(C-05-03): .env 생성 규칙, 점검 판정, Compose 파일 규칙."""
from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ops.compose import devenv

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DEV_YML = (ROOT / "ops" / "compose" / "dev.yml").read_text(encoding="utf-8")
SERVICES = ("mongo", "kafka", "mysql", "garage", "prometheus", "grafana")


class EnvExample(unittest.TestCase):
    def test_values_are_placeholders_only(self):
        env = devenv.parse_env(EXAMPLE)
        self.assertGreaterEqual(len(env), 10)
        for key, value in env.items():
            self.assertRegex(value, r"^<[A-Z][A-Z0-9_]*>$", key)

    def test_dotenv_is_gitignored(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", [line.strip() for line in lines])

    def test_render_fills_every_placeholder_with_valid_format(self):
        rendered = devenv.render_env(EXAMPLE)
        self.assertEqual(devenv.find_placeholders(rendered), [])
        env = devenv.parse_env(rendered)
        self.assertEqual(devenv.env_problems(env, devenv.parse_env(EXAMPLE)), [])

    def test_render_same_name_same_value(self):
        calls = []

        def fake(name):
            calls.append(name)
            return f"v{len(calls)}"
        out = devenv.render_env("A=<X>\nB=<X>\nC=<Y>\n", fake)
        self.assertEqual(devenv.parse_env(out), {"A": "v1", "B": "v1", "C": "v2"})
        self.assertEqual(calls, ["X", "Y"])

    def test_two_renders_differ(self):
        self.assertNotEqual(devenv.render_env(EXAMPLE), devenv.render_env(EXAMPLE))

    def test_env_problems_detects_missing_placeholder_and_format(self):
        bad_hex = "z" * 2   # check_secrets 규칙(비밀 키 이름 + 문자열 리터럴)에 걸리지 않게 변수로 넣는다
        env = {"A": "<A>", "PALDO_S3_ACCESS_KEY": "GK123", "PALDO_S3_SECRET_KEY": bad_hex}
        problems = devenv.env_problems(env, ["A", "B"])
        self.assertTrue(any(p.startswith("A:") for p in problems))
        self.assertTrue(any(p.startswith("B:") for p in problems))   # 변수 자체가 없음
        self.assertTrue(any(p.startswith("PALDO_S3_ACCESS_KEY") for p in problems))
        self.assertTrue(any(p.startswith("PALDO_S3_SECRET_KEY") for p in problems))

    def test_app_variable_names_follow_integration_list(self):
        # integration.md 4.1 표의 변수 이름(`PALDO_ADS_DB_HOST`, `_PORT`처럼 줄임 표기 포함)이 모두 .env.example에 있다
        text = (ROOT / "docs" / "contracts" / "integration.md").read_text(encoding="utf-8")
        section = text[text.index("### 4.1"):text.index("### 4.2")]
        names = set()
        for row in section.splitlines():
            if not row.startswith("| `"):
                continue
            first = row.split("|")[1]
            full = re.findall(r"`([A-Z][A-Z0-9_]+)`", first)
            for short in re.findall(r"`(_[A-Z0-9_]+)`", first):
                full.append(full[0].rsplit("_", 1)[0] + short)
            names.update(full)
        self.assertIn("PALDO_ADS_DB_PORT", names)
        self.assertEqual(names - set(devenv.parse_env(EXAMPLE)), set())

    def test_rendered_values_are_consistent(self):
        env = devenv.parse_env(devenv.render_env(EXAMPLE))
        self.assertEqual(env["PALDO_ADS_DB_USER"], env["MYSQL_USER"])
        self.assertEqual(env["PALDO_ADS_DB_PASSWORD"], env["MYSQL_PASSWORD"])
        self.assertEqual(env["PALDO_MONGO_URI"],
                         f"mongodb://{env['MONGO_INITDB_ROOT_USERNAME']}:{env['MONGO_INITDB_ROOT_PASSWORD']}"
                         "@localhost:27117/?replicaSet=rs0&authSource=admin")
        self.assertEqual(env["PALDO_ADS_DB_PORT"], "3316")
        self.assertEqual(env["PALDO_MLFLOW_URI"], "")
        self.assertNotEqual(env["DJANGO_SECRET_KEY"], env["PALDO_ADS_TOKEN_KEY"])

    def test_compose_env_carries_only_compose_vars(self):
        env = devenv.parse_env(devenv.render_env(EXAMPLE))
        out = devenv.parse_env(devenv.compose_env(env))
        self.assertEqual(out, {"PALDO_ADS_DB_PORT": "3316"})

    def test_empty_allowed_only_for_unavailable_services(self):
        env = devenv.parse_env(devenv.render_env(EXAMPLE))
        env["PALDO_LLM_URL"] = ""
        env["PALDO_ADS_DB_HOST"] = ""
        problems = devenv.env_problems(env, devenv.parse_env(EXAMPLE))
        self.assertEqual(problems, ["PALDO_ADS_DB_HOST: 값이 없다"])

    def test_compose_mysql_port_uses_env_variable(self):
        self.assertIn("${PALDO_ADS_DB_PORT:-3316}:3306", DEV_YML)

    def test_parse_env_skips_comments(self):
        self.assertEqual(devenv.parse_env("# c\n\nA = 1=2\n"), {"A": "1=2"})


class Checks(unittest.TestCase):
    def test_garage_node_id(self):
        out = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9@127.0.0.1:3901\n"
        self.assertEqual(devenv.garage_node_id(out), out.split("@")[0])
        with self.assertRaises(ValueError):
            devenv.garage_node_id("error\n")

    def test_garage_needs_layout(self):
        self.assertTrue(devenv.garage_needs_layout("ID  Hostname  Address  Tags  Zone  Capacity\nx  h  a  NO ROLE ASSIGNED"))
        self.assertFalse(devenv.garage_needs_layout("ID  Hostname  Zone  Capacity\nx  h  dc1  1000.0 MB"))

    def test_compose_unhealthy_line_and_list_formats(self):
        lines = "\n".join(json.dumps({"Service": s, "State": "running", "Health": "healthy"}) for s in SERVICES[:5])
        self.assertEqual(devenv.compose_unhealthy(lines, SERVICES), {"grafana": "없음"})
        listed = json.dumps([{"Service": "mongo", "State": "running", "Health": "starting"}])
        self.assertEqual(devenv.compose_unhealthy(listed, ["mongo"]), {"mongo": "starting"})

    def test_prometheus_down_jobs(self):
        def res(*pairs):
            return {"data": {"result": [{"metric": {"job": j}, "value": [0, v]} for j, v in pairs]}}
        self.assertEqual(devenv.prometheus_down_jobs(res(("prometheus", "1"), ("grafana", "1"), ("garage", "1"))), [])
        self.assertEqual(devenv.prometheus_down_jobs(res(("prometheus", "1"), ("grafana", "0"), ("garage", "1"))),
                         ["grafana"])
        self.assertEqual(devenv.prometheus_down_jobs(res(("prometheus", "1"))), ["grafana", "garage"])
        self.assertEqual(devenv.prometheus_down_jobs({}), ["prometheus", "grafana", "garage"])

    def test_sigv4_shape_and_determinism(self):
        now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
        args = ("PUT", "127.0.0.1:3900", "/b/k.txt", b"x", "GK" + "0" * 24, "f" * 64, "garage", now)
        h1, h2 = devenv.sigv4_headers(*args), devenv.sigv4_headers(*args)
        self.assertEqual(h1, h2)
        self.assertEqual(h1["x-amz-date"], "20261104T120000Z")
        self.assertRegex(h1["Authorization"], r"^AWS4-HMAC-SHA256 Credential=GK0{24}/20261104/garage/s3/aws4_request, "
                                              r"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature=[0-9a-f]{64}$")
        other = devenv.sigv4_headers("GET", *args[1:])
        self.assertNotEqual(h1["Authorization"], other["Authorization"])

    def test_log_record_required_fields(self):
        rec = json.loads(devenv.log_record("info", "smoke", "ok", duration_ms=3))
        for key in ("ts", "level", "service", "component", "msg"):
            self.assertIn(key, rec)
        self.assertTrue(rec["ts"].endswith("Z"))
        self.assertEqual(rec["duration_ms"], 3)


class ComposeFile(unittest.TestCase):
    def test_services_present(self):
        for s in SERVICES:
            self.assertRegex(DEV_YML, rf"(?m)^  {s}:\s*$", s)

    def test_project_name_and_no_container_name(self):
        self.assertRegex(DEV_YML, r"(?m)^name: paldo-dev\s*$")
        self.assertNotRegex(DEV_YML, r"(?m)^\s*container_name\s*:")

    def test_ports_bound_to_localhost(self):
        ports = re.findall(r'(?m)^\s*-\s*"([^"]+:\d+)"\s*(?:#.*)?$', DEV_YML)
        self.assertGreaterEqual(len(ports), 7)
        for p in ports:
            self.assertTrue(p.startswith("127.0.0.1:"), p)

    def test_images_pinned(self):
        for image in re.findall(r"(?m)^\s*image:\s*(\S+)", DEV_YML):
            self.assertRegex(image, r":v?\d+\.\d+\.\d+$", image)

    def test_no_inline_secrets(self):
        self.assertNotRegex(DEV_YML, r"(?im)(PASSWORD|SECRET|TOKEN)\w*\s*:\s*\S")

    def test_mongo_replica_set(self):
        self.assertIn("--replSet rs0", DEV_YML)


class Layers(unittest.TestCase):
    def test_ops_compose_stdlib_only(self):
        # 호스트에서 돌리는 도구의 모듈 최상위 import만 검사한다(패키지 설치 전에도 실행돼야 한다).
        # pycheck.py는 확인 대상 패키지를 함수 안에서 불러온다. ops/compose/spark/는 Spark 컨테이너 안에서 실행한다.
        stdlib = set(sys.stdlib_module_names)
        for f in [*(ROOT / "ops").glob("*.py"), *(ROOT / "ops" / "compose").glob("*.py")]:
            for node in ast.parse(f.read_text(encoding="utf-8")).body:
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                for n in names:
                    self.assertTrue(n.split(".")[0] in stdlib | {"ops"}, f"{f.relative_to(ROOT)}: {n}")

    def test_devenv_has_no_io_imports(self):
        tree = ast.parse((ROOT / "ops" / "compose" / "devenv.py").read_text(encoding="utf-8"))
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse(mods & {"subprocess", "socket", "urllib.request", "os"}, mods)


if __name__ == "__main__":
    unittest.main()
