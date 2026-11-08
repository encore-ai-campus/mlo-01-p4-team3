"""INT-07 #9 통합 환경 동시 기동 확인과 그 위의 #1 1판·#2 웹 재현(인프라 실행 도구, PM 판정용 증빙).

로컬 Compose(`ops/compose/dev.yml`의 Mongo·Kafka·MySQL)를 띄운 상태에서 게임 웹(daphne)·게임 아웃박스 발행기·
광고 서버·광고 작업자 3개를 이 노트북의 Python 3.14로 동시에 띄우고, 같은 시간대에 구성요소마다 `/metrics`의
필수 지표와 프로세스 Python 버전을 모은다. 게임 웹에는 HTTP로만 판을 진행한다:
#1 버튼 명령 1판(`int07_web_run`과 같은 명령 선택, 시드 70301), #2 반례 3개(`tools.perf.int07_scenarios`가 찾은
시드·명령 순서). 판마다 참가자 1명·고정 회차 1개를 `server.tests.int07_setup`으로 따로 만든다(계정 DB는 Compose MySQL).

    python -m server.tests.int07_stack --scenarios <int07_scenarios 결과> --kafka-config <발행기 TLS JSON> --out <폴더> [--record]

- 연결값은 루트 `.env`(make-env, 셸 환경 변수가 우선)에서 읽는다. `PALDO_MONGO_URI`에 DB 이름이 없으면 `paldo_web`을 붙인다.
- 게임 아웃박스 발행기는 검증된 TLS만 허용하므로(`server/infra/outbox_publisher.py`) 로컬 Kafka에 TLS 리스너를 더한
  상태여야 한다(INT-07 증빙 "11/8 통합 환경 동시 기동" 절의 순서). 광고 작업자는 `PALDO_KAFKA_BOOTSTRAP`(평문)을 쓴다.
- 로컬 단일 브로커에는 토픽 계획(`pipeline/topics.yaml`)의 토픽을 복제 1로 만든다(운영 브로커는 복제 3으로 미리 생성).
- 결과: `docs/evidence/perf/INT-07/stack_run.json`(수치)·`stack_run_detail.json`(구성요소·판별 기록). 접속값은 쓰지 않는다.

계층: `required_present`·`python_info`·`scenario_holds`·`summarize`(도메인, 문자열·사전만) / `Stack`(인프라, 프로세스·HTTP·Kafka) /
`play_*`·`run`(응용) / `main`(표현).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
TASK = "INT-07"
SOURCE = ("python -m server.tests.int07_stack --scenarios <int07_scenarios 결과> --kafka-config <발행기 TLS JSON> "
          "--out <폴더> --record")
WEB_PORT = 8010
INT1_SEED = 70301
GOALS = ("false_reveal", "last_possessed", "confession_switch")
SERVICES = ("mongo", "kafka", "mysql")   # 이 확인에 필요한 Compose 서비스
CONTAINER_PORTS = {"mongo": 27117, "kafka": 9092, "mysql": 3306}   # dev.yml의 컨테이너 쪽 포트
COMPONENTS = {   # 이름: (지표 주소, 필수 지표)
    "web": (f"http://localhost:{WEB_PORT}/metrics", ["paldo_repo_commit_duration_seconds"]),
    "outbox_publisher": ("http://localhost:9108/metrics", ["paldo_outbox_publish_lag_seconds"]),
    "ads_web": ("http://127.0.0.1:8001/metrics", ["paldo_ad_serve_total"]),
    "ad_worker_expire": ("http://127.0.0.1:9101/metrics", ["paldo_ad_worker_processed_total"]),
    "ad_worker_publish": ("http://127.0.0.1:9102/metrics", ["paldo_ad_worker_processed_total"]),
    "ad_worker_refund": ("http://127.0.0.1:9103/metrics", ["paldo_ad_worker_processed_total"]),
}


# ---------------------------------------------------------------- 도메인(판정 규칙)
def required_present(text: str, names: list[str]) -> dict[str, bool]:
    """지표 응답 본문에 필수 지표 이름이 있는지."""
    return {n: bool(re.search(rf"^{re.escape(n)}(_bucket|_count|_sum|_total)?[{{ ]", text, re.M)) for n in names}


def python_info(text: str) -> str | None:
    """prometheus_client 기본 수집기의 `python_info` 버전(없으면 None)."""
    m = re.search(r"python_info\{([^}]*)\}", text)
    if not m:
        return None
    d = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    return f"{d.get('major')}.{d.get('minor')}.{d.get('patchlevel')}"


def scenario_holds(goal: str, view: dict, target: str) -> bool:
    """웹 응답의 화면 정보로 반례 상황이 성립했는지(`tools.perf.int07_scenarios`의 판정과 같은 기준)."""
    pub, divs = view["public"], view["player"]["divinations"]
    if goal == "false_reveal":
        reveals = [c for c in pub["claims"] if c.get("divination_result")]
        if not reveals:
            return False
        c = reveals[-1]
        return (c["subject"] == target and c["divination_result"] == "clear"
                and divs.get(str(c["night"]), {}).get(target) == "murky")
    if goal == "last_possessed":
        res = view.get("result") or {}
        return res.get("winner") == "village" and res.get("reason") == "all_possessed_locked" and target in view["locked"]
    conf = [c for c in pub.get("confessions", []) if c.get("npc_id") == target]
    if not conf:
        return False
    day = conf[0]["day"]
    mine = {int(n): d[target] for n, d in divs.items() if target in d}
    return any(r == "murky" and n < day for n, r in mine.items()) and any(r == "clear" and n >= day for n, r in mine.items())


def summarize(result: dict) -> dict:
    """측정값에서 항목별 확인 결과. 최종 통과는 PM 판정이라 '확인 완료'까지만 적는다."""
    comps = result["components"]
    missing = [n for n, c in comps.items() if not (c["alive"] and c["metrics_status"] == 200 and all(c["required"].values()))]
    not314 = [n for n, c in comps.items() if not c["process_python"].startswith("3.14.")]
    status = {c["service"]: c["status"] for c in result["containers"]}
    ports = {c["service"]: c.get("port_matches") for c in result["containers"]}
    # 없거나 healthy가 아니거나, 그 프로젝트의 호스트 포트가 구성요소가 붙은 주소와 다르면 실패
    unhealthy = [s for s in SERVICES if "(healthy)" not in status.get(s, "") or ports.get(s) is not True]
    grew = {t: result["kafka_offsets_after"].get(t, 0) - result["kafka_offsets_before"].get(t, 0)
            for t in result["kafka_offsets_after"]}
    out = {"9": ("확인 완료, PM 판정 대기" if not missing and not not314 and not unhealthy else "실패",
                 f"Python 구성요소 {len(comps)}개 지표 누락 {len(missing)}{missing or ''}, 3.14 아닌 프로세스 {len(not314)}, "
                 f"필수 컨테이너 {len(SERVICES)}개 중 없음·비정상·주소 불일치 {len(unhealthy)}{unhealthy or ''}, 토픽 증가 "
                 f"{ {t: n for t, n in grew.items() if n} }")}
    i1 = result["games"]["int1"]
    out["1"] = ("확인 완료" if i1["winner"] and not i1["errors"] and result["http_5xx"] == 0 else "실패",
                f"통합 환경 1판 명령 {i1['commands']}개, 결과 {i1['winner']}, 500·503 {result['http_5xx']}건")
    rep = [g for g in GOALS if result["games"][g]["reproduced"]]
    out["2"] = ("자동 웹 재현 완료, 사람 브라우저 확인 대기" if len(rep) == len(GOALS) else "실패",
                f"웹 재현 {len(rep)}/{len(GOALS)}: {rep}")
    return out


# ---------------------------------------------------------------- 인프라(설정·프로세스·HTTP·Kafka)
def load_env() -> dict[str, str]:
    """루트 `.env` 위에 셸 환경 변수를 얹는다. Mongo URI에 DB 이름이 없으면 `paldo_web`을 붙인다."""
    from ops.compose.devenv import parse_env
    env = {**parse_env((ROOT / ".env").read_text(encoding="utf-8")), **os.environ}
    u = urlsplit(env["PALDO_MONGO_URI"])
    if u.path in ("", "/"):
        env["PALDO_MONGO_URI"] = urlunsplit(u._replace(path="/paldo_web"))
    return env


def http_get(url: str, timeout: float = 5) -> tuple[int | None, str]:
    """(상태, 본문). 연결 실패는 (None, 예외 이름)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return None, type(e).__name__


class Stack:
    """구성요소 프로세스 묶음. 로그는 `<out>/logs/`에 남긴다(저장소 밖 권장)."""

    def __init__(self, out: Path):
        self.out, self.procs = out, {}
        (out / "logs").mkdir(parents=True, exist_ok=True)

    def run(self, name: str, args: list[str], env: dict) -> None:
        """끝날 때까지 기다리는 준비 명령. 실패하면 멈춘다."""
        with (self.out / "logs" / f"{name}.txt").open("w", encoding="utf-8") as log:
            r = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT)
        if r.returncode:
            raise SystemExit(f"{name} 실패(로그 {self.out / 'logs' / (name + '.txt')})")

    def start(self, name: str, args: list[str], env: dict) -> None:
        """상시 프로세스를 띄운다."""
        log = (self.out / "logs" / f"{name}.txt").open("w", encoding="utf-8")
        self.procs[name] = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                            stdout=log, stderr=subprocess.STDOUT)

    def wait(self, name: str, url: str, seconds: float = 90) -> None:
        """지표 주소가 응답할 때까지 기다린다."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.procs[name].poll() is not None:
                raise SystemExit(f"{name} 종료됨(로그 {self.out / 'logs' / (name + '.txt')})")
            if http_get(url, 2)[0] is not None:
                return
            time.sleep(0.5)
        raise SystemExit(f"{name} 준비 시간 초과")

    def python_of(self, name: str) -> str:
        """그 프로세스를 띄운 Python 실행 파일(`Popen` 인자 첫 칸)에 직접 물은 버전."""
        exe = self.procs[name].args[0]
        return subprocess.run([exe, "-c", "import sys;print(sys.version.split()[0])"], capture_output=True,
                              text=True).stdout.strip()

    def stop(self) -> None:
        """모든 프로세스를 끝낸다."""
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()


def docker_cli() -> str:
    """docker 실행 파일(PATH, 없으면 Docker Desktop 사용자 설치 경로)."""
    return shutil.which("docker") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe")


def endpoint_ports(env: dict) -> dict[str, int | None]:
    """구성요소가 실제로 붙는 호스트 포트(Mongo URI·Kafka 부트스트랩·광고 DB)."""
    def port_of(hostport: str):
        try:
            return int(hostport.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            return None
    return {"mongo": urlsplit(env["PALDO_MONGO_URI"]).port, "kafka": port_of(env.get("PALDO_KAFKA_BOOTSTRAP", "")),
            "mysql": int(env["PALDO_ADS_DB_PORT"]) if env.get("PALDO_ADS_DB_PORT", "").isdigit() else None}


def published_port(project: str, service: str) -> int | None:
    """그 Compose 프로젝트에서 서비스의 컨테이너 포트가 열린 호스트 포트."""
    out = subprocess.run([docker_cli(), "compose", "-p", project, "-f", "ops/compose/dev.yml", "port", service,
                          str(CONTAINER_PORTS[service])], capture_output=True, text=True, cwd=ROOT)
    try:
        return int(out.stdout.strip().splitlines()[0].rsplit(":", 1)[1]) if out.returncode == 0 else None
    except (IndexError, ValueError):
        return None


def containers(project: str, env: dict | None = None) -> list[dict]:
    """Compose 서비스 상태(서비스·이미지·상태). `env`가 있으면 호스트 포트가 구성요소 연결 주소와 같은지도 적는다."""
    out = subprocess.run([docker_cli(), "compose", "-p", project, "-f", "ops/compose/dev.yml", "ps", "--format",
                          "{{.Service}}|{{.Image}}|{{.Status}}"], capture_output=True, text=True, cwd=ROOT)
    if out.returncode:
        return []   # 명령 실패는 빈 목록 → `summarize`가 필수 서비스 없음으로 실패 처리
    rows = [dict(zip(("service", "image", "status"), line.split("|"))) for line in out.stdout.splitlines() if line]
    if env is not None:
        want = endpoint_ports(env)
        for row in rows:
            if row["service"] in CONTAINER_PORTS:
                row["port_matches"] = want.get(row["service"]) is not None and                     published_port(project, row["service"]) == want[row["service"]]
    return rows


def kafka_conf(env: dict) -> dict:
    """평문 리스너 클라이언트 설정(로컬)."""
    return {"bootstrap.servers": env.get("PALDO_KAFKA_BOOTSTRAP", "localhost:9092"), "broker.address.family": "v4"}


def ensure_topics(env: dict) -> int:
    """토픽 계획의 토픽 중 없는 것을 복제 1로 만든다. 만든 수."""
    from confluent_kafka.admin import AdminClient, NewTopic
    plan = json.loads((ROOT / "pipeline/topics.yaml").read_text(encoding="utf-8"))
    admin = AdminClient(kafka_conf(env))
    have = admin.list_topics(timeout=15).topics
    new = [NewTopic(t["name"], num_partitions=plan["defaults"]["partitions"], replication_factor=1)
           for t in plan["topics"] if t["name"] not in have]
    for _, f in (admin.create_topics(new).items() if new else []):
        f.result(30)
    return len(new)


def kafka_offsets(env: dict) -> dict[str, int]:
    """토픽별 끝 오프셋 합. 막 만든 토픽은 리더가 정해질 때까지 다시 묻는다."""
    from confluent_kafka import Consumer, TopicPartition
    for _ in range(20):
        c = Consumer({**kafka_conf(env), "group.id": "int07-stack-probe", "enable.auto.commit": False})
        try:
            md = c.list_topics(timeout=10)
            return {t: sum(c.get_watermark_offsets(TopicPartition(t, p), timeout=10)[1] for p in tm.partitions)
                    for t, tm in sorted(md.topics.items()) if not t.startswith("__")}
        except Exception:
            time.sleep(1)
        finally:
            c.close()
    raise SystemExit("Kafka 오프셋을 읽지 못함")


def content_loaded(env: dict) -> bool:
    """`content` 컬렉션에 적재된 콘텐츠가 있는지."""
    from pymongo import MongoClient
    with MongoClient(env["PALDO_MONGO_URI"], serverSelectionTimeoutMS=5000) as client:
        return client.get_default_database()["content"].count_documents({}) > 0


# ---------------------------------------------------------------- 응용(흐름)
def participant(stack: Stack, env: dict, key: str, seed: int) -> dict:
    """참가자 1명과 고정 회차(시드 하나)를 만든다."""
    path = stack.out / f"setup_{key}.json"
    stack.run(f"setup_{key}", ["-m", "server.tests.int07_setup", str(path), str(seed)], env)
    return json.loads(path.read_text(encoding="utf-8"))


def start_game(http, setup: dict) -> dict:
    """고정 회차의 판을 시작한다."""
    s, b = http.request("POST", "/games", {"preset_id": "base6", "session_id": setup["session_id"], "mudang_variant": "f"})
    if s != 200:
        raise SystemExit(f"판 시작 {s}")
    return b["view"]


def send(http, view: dict, command: dict) -> tuple[int, dict]:
    """명령 API 호출."""
    return http.request("POST", f"/games/{view['game_id']}/commands",
                        {"game_id": view["game_id"], "command_id": str(uuid.uuid4()),
                         "expected_state_version": view["state_version"], "command": command})


def play_scenario(http, setup: dict, found: dict) -> dict:
    """찾은 명령 순서를 웹 명령 API로 그대로 보내고 상황 성립을 화면 정보로 확인한다."""
    view = start_game(http, setup)
    steps = []
    for cmd in found["commands"]:
        s, b = send(http, view, cmd)
        ok = s == 200 and bool(b.get("accepted"))
        steps.append({"kind": cmd["kind"], "status": s, "accepted": ok})
        if not ok:
            break
        view = b["view"]
    hs, _ = http.request("GET", f"/games/{view['game_id']}", accept="text/html")
    accepted = len(steps) == len(found["commands"]) and all(x["accepted"] for x in steps)
    return {"seed": found["seed"], "target": found["target"], "commands": len(steps), "all_accepted": accepted,
            "html_status": hs, "reproduced": accepted and hs == 200 and scenario_holds(found["goal"], view, found["target"]),
            "result": view.get("result"), "steps": steps}


def play_int1(http, setup: dict) -> dict:
    """버튼 명령만으로 1판(`int07_web_run`과 같은 명령 선택)."""
    from tools.perf.int07_web_run import command_body
    view, rng, n, errors = start_game(http, setup), random.Random(1), 0, []
    while view["result"] is None and n < 400:
        s, b = send(http, view, command_body(view, rng)["command"])
        if s != 200 or not b.get("accepted"):
            errors.append(str(s))
            break
        view, n = b["view"], n + 1
    return {"seed": INT1_SEED, "commands": n, "winner": (view["result"] or {}).get("winner"), "errors": errors}


def run(scenarios: Path, kafka_config: Path, out: Path, record: bool, project: str) -> dict:
    """준비 → 동시 기동 → 판 진행 → 같은 시간대 수집 → 정리."""
    from tools.perf.int07_web_run import HttpClient, Run
    env = load_env()
    base = {**env, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT), "PALDO_LOG_DIR": str(out / "logs")}
    web = {**base, "DJANGO_SETTINGS_MODULE": "server.paldo_web.settings", "PALDO_PUBLIC_ORIGIN": f"http://localhost:{WEB_PORT}",
           "PALDO_GAME_SERVICE_FACTORY": "server.infra.game_factory.build_game_service", "PALDO_CONTENT_VERSION": "content-1.0"}
    ads = {**base, "DJANGO_SETTINGS_MODULE": "ads.project.settings"}
    pub = {k: v for k, v in base.items() if k != "PALDO_KAFKA_BOOTSTRAP"}   # 발행기는 TLS 설정 파일의 주소
    found = json.loads(scenarios.read_text(encoding="utf-8"))
    stack = Stack(out)
    stack.run("web_migrate", ["server/manage.py", "migrate", "--noinput"], web)
    if not content_loaded(web):
        stack.run("load_content", ["-m", "tools.load_content", "content/bundle_v1.json"], web)
    stack.run("ads_migrate", ["ads/manage.py", "migrate", "--noinput"], ads)
    stack.run("ads_fixtures", ["ads/manage.py", "load_ad_fixtures"], ads)
    setups = {g: participant(stack, web, g, found[g]["seed"]) for g in GOALS}
    setups["int1"] = participant(stack, web, "int1", INT1_SEED)
    result = {"compose_project": project, "topics_created_rf1": ensure_topics(env), "kafka_offsets_before": kafka_offsets(env)}
    state = Run()
    try:   # 띄우기도 정리 범위 안에 둔다(중간에 실패해도 먼저 띄운 프로세스를 끈다)
        stack.start("web", ["-m", "daphne", "-b", "127.0.0.1", "-p", str(WEB_PORT), "server.paldo_web.asgi:application"], web)
        stack.start("outbox_publisher", ["-m", "server.infra.outbox_publisher", "--kafka-config", str(kafka_config),
                                         "--metrics-port", "9108"], pub)
        stack.start("ads_web", ["-X", "utf8", "ads/manage.py", "runserver", "127.0.0.1:8001", "--noreload"], ads)
        for i, role in enumerate(("expire", "publish", "refund")):
            stack.start(f"ad_worker_{role}", ["-X", "utf8", "ads/manage.py", "ad_worker", role, "--metrics-port",
                                              str(9101 + i)], ads)
        for name, (url, _) in COMPONENTS.items():
            stack.wait(name, url)
        result["started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        origin = f"http://localhost:{WEB_PORT}"
        games = {g: play_scenario(HttpClient(origin, tuple(setups[g]["cookie"]), state.record), setups[g],
                                  dict(found[g], goal=g)) for g in GOALS}
        games["int1"] = play_int1(HttpClient(origin, tuple(setups["int1"]["cookie"]), state.record), setups["int1"])
        req = urllib.request.Request("http://127.0.0.1:8001/ads/v1/serve", data=json.dumps(
            {"request_id": f"int07-stack-{uuid.uuid4().hex[:6]}", "slot": "lobby_banner", "session_pseudonym": "int07-stack",
             "context": {}}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            result["ad_serve_status"] = r.status
        time.sleep(8)   # 발행기가 아웃박스를 Kafka로 보낼 시간
        comps = {}
        for name, (url, need) in COMPONENTS.items():
            s, text = http_get(url)
            comps[name] = {"metrics_status": s, "required": required_present(text, need), "python_info": python_info(text),
                           "process_python": stack.python_of(name), "alive": stack.procs[name].poll() is None}
        result.update(components=comps, containers=containers(project, env), kafka_offsets_after=kafka_offsets(env),
                      http_5xx=state.http_5xx, forbidden_hits=state.hits, responses_checked=state.responses, games=games,
                      collected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    finally:
        stack.stop()
    result["verdicts"] = {k: {"result": v[0], "detail": v[1]} for k, v in summarize(result).items()}
    if record:
        write_evidence(result)
    return result


def write_evidence(result: dict) -> None:
    """수치(`stack_run.json`)와 판별 기록(`stack_run_detail.json`)을 쓴다."""
    from tools.evidence_result import write_result
    from tools.gitrepo import GitRepo
    comps = result["components"]
    metrics = {
        "components_up": (sum(c["alive"] and c["metrics_status"] == 200 and all(c["required"].values()) for c in comps.values()), "개"),
        "components_total": (len(comps), "개"),
        "non_314_processes": (sum(not c["process_python"].startswith("3.14.") for c in comps.values()), "개"),
        "containers_healthy": (sum("(healthy)" in c["status"] for c in result["containers"] if c["service"] in SERVICES), "개"),
        "kafka_events_published": (sum(result["kafka_offsets_after"].values()) - sum(result["kafka_offsets_before"].values()), "건"),
        "scenarios_reproduced": (sum(result["games"][g]["reproduced"] for g in GOALS), "개"),
        "int1_commands": (result["games"]["int1"]["commands"], "개"),
        "http_5xx": (result["http_5xx"], "건"),
        "forbidden_hits": (len(result["forbidden_hits"]), "건"),
    }
    path = write_result(TASK, "stack_run", {k: {"value": v, "unit": u} for k, (v, u) in metrics.items()}, SOURCE)
    detail = dict(task=TASK, commit=GitRepo(ROOT).head(), source=SOURCE, **result)
    path.with_name("stack_run_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
                                                       encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- 표현(CLI)
def main(argv=None) -> int:
    """CLI. 자동 확인이 실패한 항목이 있으면 종료 코드 1."""
    ap = argparse.ArgumentParser(prog="python -m server.tests.int07_stack")
    ap.add_argument("--scenarios", type=Path, required=True, help="tools.perf.int07_scenarios 결과 JSON")
    ap.add_argument("--kafka-config", type=Path, required=True, help="게임 아웃박스 발행기 TLS 설정 JSON(저장소 밖)")
    ap.add_argument("--out", type=Path, default=Path("out/int07_stack"), help="로그·준비 파일 폴더(저장소 밖 또는 out/)")
    ap.add_argument("--project", default="paldo-dev", help="Compose 프로젝트 이름(상태 확인용)")
    ap.add_argument("--record", action="store_true", help="docs/evidence/perf/INT-07/stack_run*.json에 쓴다")
    a = ap.parse_args(argv)
    result = run(a.scenarios, a.kafka_config, a.out, a.record, a.project)
    for item, v in result["verdicts"].items():
        print(f"#{item} {v['result']}: {v['detail']}")
    return 1 if any(v["result"] == "실패" for v in result["verdicts"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
