"""INT-07 웹 자동 재현(#1·#3·#4·#6): 실제 HTTP 게임 웹 + 실제 Mongo 레플리카셋 + 운영 조립으로 버튼 명령만 써서 판을 진행한다.

사용법(저장소 루트, Mongo 레플리카셋은 미리 띄워 둔다. 연결 문자열에는 DB 이름 `/paldo_web`을 넣는다):
    PALDO_MONGO_URI=<레플리카셋 URI> python -m tools.perf.int07_web_run --out <임시 폴더> --record

하는 일:
1. 실행용 Django 설정은 `server.tests.int07_run_settings`다. 계정 DB는 `<out>` 안의 SQLite 파일(MySQL 아님), 게임·진척·
   콘텐츠·회차는 실제 Mongo(`PALDO_MONGO_URI`), 게임 서비스는 운영 조립 `server.infra.game_factory.build_game_service`.
2. 하위 프로세스로 콘텐츠 적재(`tools.load_content`)와 준비(`server.tests.int07_setup`: 계정 DB 이주, 동의한 참가자 1명과
   로그인 세션, 고정 회차 등록)를 한다. `tools/`는 제3자 라이브러리를 import하지 않으므로 Django·pymongo는 하위 프로세스에만 있다.
3. daphne로 게임 웹을 띄우고 HTTP로만 3판을 진행한다(합법 명령 고르기는 `bench_player_command`, 규칙 판단은 엔진).
   - #1 판마다 결과가 날 때까지, 500·503 0건
   - #3 날이 바뀔 때마다 회의 화면 HTML에 지금까지 공개된 밤마다 "N일째 아침:"과 피해 장소 문장
   - #4 첫 판에서 같은 명령 재전송(재생)·오래된 버전(409), 둘째 판 중간에 서버 프로세스를 재시작하고 같은 화면으로 이어 하기
   - #6 받은 모든 JSON 응답의 키와 HTML 본문에 비공개 키 이름 0
4. 결과 JSON(`docs/evidence/perf/INT-07/web_run.json`, 판별 기록은 `web_run_detail.json`)을 쓰고, 합격선을 자동으로 확인한 항목과 사람 확인이 남은 항목을 나눠 적는다(최종 판정은 PM).

계층: `json_keys`·`forbidden_hits`·`morning_missing`·`metrics_of`·`summarize`(도메인, 문자열·사전만) / `HttpClient`·`WebServer`(인프라, HTTP·프로세스;
준비는 `server/tests/int07_setup.py`) / `play_game`·`run`(응용) / `main`(표현).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import secrets
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("claim_truths", "npc_states", "beliefs", "private", "night_plans")
SEEDS = (70301, 70302, 70303)  # A-07-03 서버 시험(`test_game_factory`)과 같은 고정 회차
MAX_COMMANDS = 400
RESTART_AFTER = 3          # 둘째 판에서 이 명령 수 뒤 서버 프로세스를 재시작한다
TASK = "INT-07"
SOURCE = "python -m tools.perf.int07_web_run --out <임시 폴더> --record"


# ---------------------------------------------------------------- 도메인(판정 규칙)
def json_keys(value):
    """JSON 값의 모든 키."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from json_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from json_keys(item)


def forbidden_hits(body, forbidden=FORBIDDEN) -> list[str]:
    """응답 하나에서 비공개 키 이름이 나온 목록. JSON(사전·목록)은 키를, 문자열(HTML)은 본문 전체를 본다."""
    if isinstance(body, (dict, list)):
        keys = set(json_keys(body))
        return sorted(k for k in forbidden if k in keys)
    return sorted(k for k in forbidden if k in body)


def morning_missing(html: str, nights: list[int]) -> list[str]:
    """아침 화면 HTML에 빠진 피해 장소 문장. 공개된 밤 n마다 한 줄(태그 없이) 안에 "n+1일째 아침: …에서 발견됐다"가 있어야 한다
    (`server/api/viewmodels.py`의 실종 기록 문장)."""
    return [f"{n + 1}일째 아침: …에서 발견됐다" for n in nights
            if not re.search(rf"{n + 1}일째 아침:[^<\n]*?에서 발견됐다", html)]


def metrics_of(result: dict) -> dict:
    """결과 JSON(README 4절)의 수치. 판정은 `summarize`가 하고, 여기는 계측값만 옮긴다."""
    games = result["games"]
    values = {
        "games_finished": (sum(g["winner"] is not None for g in games), "판"),
        "commands_total": (sum(g["commands"] for g in games), "개"),
        "renders_total": (sum(g["renders"] for g in games), "개"),
        "http_5xx": (result["http_5xx"], "건"),
        "mornings_checked": (sum(g["mornings_checked"] for g in games), "회"),
        "max_night_seen": (max((max(g["nights_seen"]) for g in games if g["nights_seen"]), default=-1), ""),
        "morning_missing": (sum(len(g["morning_missing"]) for g in games), "건"),
        "stale_status": (result["duplicate"]["stale_status"] or 0, ""),
        "restart_same_view": (int(bool(result["restart"]["same_view"])), ""),
        "responses_checked": (result["responses_checked"], "개"),
        "forbidden_hits": (len(result["forbidden_hits"]), "건"),
        "elapsed": (result["elapsed_s"], "s"),
    }
    return {k: {"value": v, "unit": u} for k, (v, u) in values.items()}


def summarize(result: dict) -> dict:
    """측정값에서 항목별 자동 확인 결과를 만든다. 합격선에 사람의 웹 재현이 들어 있으면 통과가 아니라 '사람 확인 대기'."""
    games = result["games"]
    finished = [g for g in games if g["winner"] is not None]
    errors = result["http_5xx"]
    game_errors = sum(len(g["errors"]) for g in games)
    final_ok = all(g.get("view_result_matches") for g in games)
    all_done = len(games) == 3 and len(finished) == 3 and game_errors == 0
    out = {}
    ok1 = all_done and errors == 0 and final_ok and result.get("progress_has_games")
    out["1"] = ("자동 확인 통과" if ok1 else "실패",
                f"완주 {len(finished)}/3, 명령 {[g['commands'] for g in games]}, 500·503 {errors}건, 판 오류 {game_errors}건, "
                f"최종 화면 결과 일치 {final_ok}, 진척 기록 {result.get('progress_has_games')}")
    nights_seen = max((max(g["nights_seen"]) for g in games if g["nights_seen"]), default=-1)
    morning_bad = sum(len(g["morning_missing"]) for g in games)
    out["3"] = ("자동 확인 완료, 사람 브라우저 확인 대기" if morning_bad == 0 and nights_seen >= 1 else "실패",
                f"아침 화면 확인 {sum(g['mornings_checked'] for g in games)}회, 최대 공개 밤 {nights_seen}, 빠진 문장 {morning_bad}")
    d = result["duplicate"]
    r = result["restart"]
    ok4 = d["replayed"] and d["same_view"] and d["stale_status"] == 409 and r["same_view"] and r["finished_after"]
    out["4"] = ("자동 확인 완료, 사람 브라우저 확인 대기" if ok4 else "실패",
                f"재전송 재생 {d['replayed']}·같은 화면 {d['same_view']}, 오래된 버전 {d['stale_status']}, "
                f"서버 재시작 뒤 같은 화면 {r['same_view']}·완주 {r['finished_after']}")
    hits = result["forbidden_hits"]
    out["6"] = ("자동 확인 통과" if all_done and not hits and result["responses_checked"] > 0 else "실패",
                f"응답 {result['responses_checked']}개(JSON {result['json_checked']}, HTML {result['html_checked']}) 금지 키 {len(hits)}건")
    return out


# ---------------------------------------------------------------- 인프라(HTTP·프로세스·Django)
class HttpClient:
    """로그인 세션 쿠키와 CSRF 토큰을 붙여 게임 웹에 요청한다. 모든 응답을 금지 키 검사에 넘긴다."""

    def __init__(self, origin: str, session_cookie: tuple[str, str], recorder):
        self.origin = origin.rstrip("/")
        self.csrf = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
        self.cookie = f"{session_cookie[0]}={session_cookie[1]}; csrftoken={self.csrf}"
        self.recorder = recorder

    def request(self, method: str, path: str, data=None, accept="application/json"):
        """(상태 코드, 본문) — JSON이면 사전, 아니면 문자열. 연결 오류는 그대로 올린다."""
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.origin + path, data=body, method=method)
        req.add_header("Accept", accept)
        req.add_header("Cookie", self.cookie)
        if body is not None:
            req.add_header("Content-Type", "application/json")
            req.add_header("X-CSRFToken", self.csrf)
            req.add_header("Origin", self.origin)
            req.add_header("Referer", self.origin + "/")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, raw, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as err:
            status, raw, ctype = err.code, err.read(), err.headers.get("Content-Type", "")
        text = raw.decode("utf-8") if raw else ""
        parsed = json.loads(text) if text and "json" in ctype else text
        self.recorder(method, path, status, parsed)
        return status, parsed


def free_port() -> int:
    """비어 있는 로컬 포트."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebServer:
    """daphne 게임 웹 프로세스. `/healthz`가 응답할 때까지 기다린다."""

    def __init__(self, port: int, env: dict, log_path: Path):
        self.port, self.env, self.log_path, self.proc = port, env, log_path, None

    def start(self) -> None:
        """서버를 띄우고 준비될 때까지 기다린다(최대 90초)."""
        log = self.log_path.open("a", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(self.port),
                                      "server.paldo_web.asgi:application"], cwd=ROOT, env=self.env,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"게임 웹이 종료됨(로그 {self.log_path})")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=2):
                    return
            except OSError:
                time.sleep(0.5)
        self.stop()  # 준비되지 않은 프로세스를 남기지 않는다
        raise TimeoutError(f"게임 웹 준비 시간 초과(로그 {self.log_path})")

    def stop(self) -> None:
        """서버 프로세스를 끝낸다."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


# ---------------------------------------------------------------- 응용(흐름)
class Run:
    """한 번의 실행 상태: 응답 검사 집계와 HTTP 클라이언트."""

    def __init__(self):
        self.responses = self.json_checked = self.html_checked = self.http_5xx = 0
        self.hits: list[dict] = []

    def record(self, method, path, status, body):
        """응답마다 금지 키·5xx를 센다."""
        self.responses += 1
        if isinstance(body, (dict, list)):
            self.json_checked += 1
        elif body:
            self.html_checked += 1
        if status in (500, 503):
            self.http_5xx += 1
        found = forbidden_hits(body) if body else []
        if found:
            self.hits.append({"method": method, "path": path, "keys": found})


def command_body(view: dict, rng: random.Random) -> dict:
    """화면 정보의 합법 명령 하나를 명령 API 본문으로 만든다(선택은 `bench_player_command`)."""
    from engine.domain.serde import from_jsonable, to_jsonable
    from engine.ports import PlayerView
    from tools.perf.engine_bench import bench_player_command
    cmd = bench_player_command(from_jsonable(PlayerView, view), rng)
    return {"game_id": view["game_id"], "command_id": str(uuid.uuid4()),
            "expected_state_version": view["state_version"], "command": to_jsonable(cmd)}


def screen_html(http: HttpClient, view: dict) -> tuple[int, str]:
    """게임 화면 HTML. 받은 본문은 기록기가 금지 키 검사를 한다(#6)."""
    return http.request("GET", f"/games/{view['game_id']}", accept="text/html")


def check_morning(status: int, html: str, view: dict, game: dict) -> None:
    """회의 화면 HTML에서 공개된 밤마다 피해 장소 문장을 확인한다."""
    nights = [d["night"] for d in view["public"]["disappearances"]]
    game["mornings_checked"] += 1
    game["nights_seen"] = sorted(set(game["nights_seen"]) | set(nights))
    if status != 200:
        game["morning_missing"].append(f"HTML {status}")
        return
    game["morning_missing"] += morning_missing(html, nights)


def send(http: HttpClient, view: dict, body: dict) -> tuple[int, dict]:
    """명령 API 호출."""
    return http.request("POST", f"/games/{view['game_id']}/commands", body)


def play_game(http: HttpClient, run_state: Run, session_id: str, number: int, server: WebServer, result: dict) -> dict:
    """한 판을 시작해 결과가 날 때까지 버튼 명령으로 진행한다. 첫 판은 재전송·오래된 버전, 둘째 판은 서버 재시작을 끼운다."""
    game = {"number": number, "commands": 0, "renders": 0, "winner": None, "mornings_checked": 0,
            "nights_seen": [], "morning_missing": [], "errors": []}
    status, body = http.request("POST", "/games", {"preset_id": "base6", "session_id": session_id, "mudang_variant": "f"})
    if status != 200:
        game["errors"].append(f"start {status} {str(body)[:200]}")
        return game
    view = body["view"]
    game["game_id"] = view["game_id"]
    page = screen_html(http, view)
    if page[0] != 200:
        game["errors"].append(f"html {page[0]}")
    check_morning(*page, view, game)
    rng = random.Random(number)
    while view["result"] is None and game["commands"] < MAX_COMMANDS:
        first = number == 1 and game["commands"] == 0
        req = command_body(view, random.Random(0) if first else rng)  # 서버 시험과 같은 명령 선택 순서
        status, body = send(http, view, req)
        if status != 200 or not body.get("accepted"):
            game["errors"].append(f"command {status} {str(body)[:200]}")
            break
        if first:
            s2, replay = send(http, view, req)
            s3, _ = send(http, view, dict(req, command_id=str(uuid.uuid4())))
            result["duplicate"] = {"replayed": bool(s2 == 200 and replay.get("replayed")),
                                   "same_view": s2 == 200 and replay.get("view") == body["view"], "stale_status": s3}
        for job in body.get("render_jobs") or body["view"].get("pending_render_jobs", []):
            s, render = http.request("GET", f"/games/{view['game_id']}/renders/{job}")
            if s == 200 and (render.get("status"), render.get("final")) == ("failed", True) and render.get("text"):
                game["renders"] += 1
            else:
                game["errors"].append(f"render {s}")
        page = screen_html(http, body["view"])  # 명령마다 화면 HTML도 받아 금지 키를 본다(결과 화면 포함)
        if page[0] != 200:
            game["errors"].append(f"html {page[0]}")
        if body["view"]["day"] != view["day"]:
            check_morning(*page, body["view"], game)
        view = body["view"]
        game["commands"] += 1
        if number == 2 and game["commands"] == RESTART_AFTER:
            server.stop()
            server.start()
            s, again = http.request("GET", f"/games/{view['game_id']}")
            result["restart"] = {"after_commands": RESTART_AFTER, "status": s,
                                 "same_view": s == 200 and again.get("view") == view, "finished_after": False}
            if s == 200:
                view = again["view"]
    game["winner"] = (view.get("result") or {}).get("winner")
    if number == 2 and "restart" in result:
        result["restart"]["finished_after"] = game["winner"] is not None
    s, final = http.request("GET", f"/games/{view['game_id']}")
    game["view_result_matches"] = s == 200 and final["view"]["result"] == view["result"]
    return game


def run(out: Path, record: bool) -> dict:
    """설정 생성 → 하위 프로세스 준비 → 콘텐츠 적재 → 서버 → 3판 → 결과."""
    if not os.environ.get("PALDO_MONGO_URI"):
        raise SystemExit("PALDO_MONGO_URI(레플리카셋, DB 이름 /paldo_web 포함)를 설정하세요")
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", DJANGO_SETTINGS_MODULE="server.tests.int07_run_settings",
               PYTHONPATH=str(ROOT),
               PALDO_INT07_SQLITE=str((out / "accounts.sqlite3").resolve()),
               PALDO_PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
               DJANGO_SECRET_KEY=os.environ.get("DJANGO_SECRET_KEY") or secrets.token_urlsafe(48),
               PALDO_GAME_SERVICE_FACTORY="server.infra.game_factory.build_game_service")
    (out / "accounts.sqlite3").unlink(missing_ok=True)
    started = time.monotonic()
    # 지금 체크아웃의 콘텐츠로 돌았음을 보장하려고 적재 실패는 모두 멈춘다. 콘텐츠 버전은 불변이라 이 버전이 이미 있는 DB에서는
    # 적재가 content_version_exists로 실패하므로, 빈 DB(또는 이 버전이 없는 DB)에서 돌린다.
    loaded = subprocess.run([sys.executable, "-m", "tools.load_content", "content/bundle_v1.json"], cwd=ROOT, env=env,
                            stdin=subprocess.DEVNULL, check=False)
    if loaded.returncode:
        raise SystemExit("콘텐츠 적재 실패: 이 콘텐츠 버전이 없는 빈 DB에서 다시 돌리세요(적재 도구 로그 참고)")
    setup_out = out / "setup.json"
    subprocess.run([sys.executable, "-m", "server.tests.int07_setup", str(setup_out), *map(str, SEEDS)], cwd=ROOT,
                   env=env, stdin=subprocess.DEVNULL, check=True)
    setup = json.loads(setup_out.read_text(encoding="utf-8"))
    server = WebServer(port, env, out / "web.log")
    state = Run()
    result = {"rule_version": setup["rule_version"], "content_version": setup["content_version"],
              "seeds": list(SEEDS), "account_db": "sqlite(실행용 설정)", "game_store": "mongo replica set",
              "server": "daphne HTTP", "duplicate": {}, "restart": {}}
    server.start()
    try:
        http = HttpClient(f"http://127.0.0.1:{port}", tuple(setup["cookie"]), state.record)
        result["games"] = [play_game(http, state, setup["session_id"], n, server, result) for n in (1, 2, 3)]
        s, progress = http.request("GET", "/players/me/progress")
        ids = set((progress.get("progress") or {}).get("game_ids", [])) if s == 200 else set()
        result["progress_has_games"] = all(g.get("game_id") in ids for g in result["games"])
    finally:
        server.stop()
    result.update(responses_checked=state.responses, json_checked=state.json_checked, html_checked=state.html_checked,
                  http_5xx=state.http_5xx, forbidden_hits=state.hits, elapsed_s=round(time.monotonic() - started, 1))
    result.setdefault("duplicate", {})
    for key, default in (("replayed", False), ("same_view", False), ("stale_status", None)):
        result["duplicate"].setdefault(key, default)
    for key, default in (("same_view", False), ("finished_after", False)):
        result["restart"].setdefault(key, default)
    result["verdicts"] = {k: {"result": v[0], "detail": v[1]} for k, v in summarize(result).items()}
    if record:
        from tools.evidence_result import write_result
        from tools.gitrepo import GitRepo
        path = write_result(TASK, "web_run", metrics_of(result), SOURCE)
        detail = dict(task=TASK, commit=GitRepo(ROOT).head(), source=SOURCE, **result)
        path.with_name("web_run_detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n",
                                                         encoding="utf-8", newline="\n")
    return result


# ---------------------------------------------------------------- 표현(CLI)
def main(argv=None) -> int:
    """CLI. 합격선을 자동으로 확인할 수 없는 항목이 실패면 종료 코드 1."""
    ap = argparse.ArgumentParser(prog="python -m tools.perf.int07_web_run")
    ap.add_argument("--out", default="out/int07", help="실행 설정·계정 DB·서버 로그를 둘 폴더(저장소 밖 또는 out/)")
    ap.add_argument("--record", action="store_true",
                    help="결과를 docs/evidence/perf/INT-07/web_run.json(수치)·web_run_detail.json(판별 기록)에 쓴다")
    a = ap.parse_args(argv)
    result = run(Path(a.out), a.record)
    for item, v in result["verdicts"].items():
        print(f"#{item} {v['result']}: {v['detail']}")
    return 1 if any(v["result"] == "실패" for v in result["verdicts"].values()) else 0


if __name__ == "__main__":
    sys.exit(main())
