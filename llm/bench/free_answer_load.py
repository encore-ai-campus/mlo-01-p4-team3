"""인프라: 자유 질문 답변 부하 클라이언트(E-08-13 5번, E-11-12 1~4번 측정).

OpenAI 호환 `/v1/chat/completions`를 스트리밍(`stream: true`, SSE)으로 부르고, 받은 조각을 `llm.free_answer`의
`AnswerStream`·`SentenceGate`로 문장마다 검사한다. 닫힌 루프 N세션(세션마다 요청 → 답 → 생각 시간 → 다음 요청).

지연 정의(E-11-12 3번): 시작 = 요청 송신. 첫 표시 = 근거 줄과 첫 문장을 받아 그 문장이 검사를 통과한 시각(재생성·폴백 포함).
완료 = 마지막으로 공개한 문장이 통과한 시각. 첫 문장 실패면 1회 재생성 뒤 폴백, 둘째 문장 실패면 버리고 부분 답.
실패·시간 초과·스트리밍 중 끊김은 완료 시각을 무한대로, 그때까지 첫 표시가 없었으면 첫 표시도 무한대로 넣어 p50·p95를
계산한다. 모델 첫 토큰 시각(검사 전)은 참고로 남긴다.

    python -m llm.bench.free_answer_load --base-url http://<사설망 주소>:8000 --model <서버 모델 이름> \
        --sessions 30 --think 5 15 --warmup 120 --duration 600 --seed 1 --label s2_normal \
        --server-version <버전> --image <이미지 태그> --revision <모델 리비전> --options "<실행 옵션>"

세션 키는 환경 변수 `PALDO_LLM_KEY`에서 읽어 `Authorization` 헤더에만 넣고 출력·결과 파일에 쓰지 않는다.
주소는 명령 인자로만 받고 결과 JSON에 남기지 않는다(저장소 비밀 검사).

계층: 위쪽 순수 함수(백분위·요약·SSE 줄 해석, 단독 테스트) / `OpenAIStream`(HTTP 인프라, 표준 라이브러리 urllib) /
`run_request`·`run_closed_loop`(응용: 시도·재생성·폴백 흐름과 세션 루프, 시계·잠자기 주입) / `main`(인자 파싱).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from llm.free_answer import PROMPT_VERSION, AnswerStream, FreeQuestion, SentenceGate, build_messages, fallback
from llm.validator import Lexicon

INF = math.inf
TIMEOUT_S = 30.0          # 요청 한 건(재생성 포함) 시간 제한(E-11-12 3번)
MAX_TOKENS = 160
KEY_ENV = "PALDO_LLM_KEY"


# --- 순수 함수 ---
def percentile(values: list[float], q: float) -> float:
    """최근접 순위 백분위(무한대 포함). 빈 목록이면 NaN."""
    if not values:
        return math.nan
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return s[k]


def parse_sse_line(line: str) -> str | None:
    """SSE 한 줄에서 내용 조각을 꺼낸다: 조각 문자열, 끝 표시(`[DONE]`)면 "", 내용이 없는 줄이면 None."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return ""
    obj = json.loads(data)
    choices = obj.get("choices") or [{}]
    delta = choices[0].get("delta") or {}
    return delta.get("content") or None


@dataclass
class Record:
    """요청 한 건의 기록. 시각은 요청 송신 기준 초(실패면 무한대)."""
    session: int
    item_id: str
    start: float
    first_token: float = INF
    first_display: float = INF
    complete: float = INF
    outcome: str = "error"          # ok | partial | regen_ok | regen_partial | fallback | error | timeout
    attempts: int = 0
    reasons: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    error: str | None = None


def summarize(records: list[Record], wall_s: float) -> dict:
    """p50·p95(무한대 포함)·비율·처리량. JSON에 무한대는 null로 쓴다."""
    n = len(records)

    def pct(key: str) -> dict:
        vals = [getattr(r, key) for r in records]
        return {f"p{q}": _num(percentile(vals, q)) for q in (50, 95)}
    count = lambda *o: sum(r.outcome in o for r in records)   # noqa: E731
    return {
        "n": n,
        "first_display_s": pct("first_display"),
        "complete_s": pct("complete"),
        "first_token_s": pct("first_token"),
        "error_rate": count("error", "timeout") / n if n else None,
        "fallback_rate": count("fallback") / n if n else None,
        "partial_rate": count("partial", "regen_partial") / n if n else None,
        "regen_rate": count("regen_ok", "regen_partial", "fallback") / n if n else None,
        "throughput_rps": n / wall_s if wall_s > 0 else None,
    }


def _num(x: float) -> float | None:
    return None if (isinstance(x, float) and (math.isinf(x) or math.isnan(x))) else round(x, 4)


# --- 인프라: OpenAI 호환 스트리밍 ---
class StreamError(Exception):
    """스트리밍 실패(HTTP 오류, 끊김, 형식 오류)."""


class OpenAIStream:
    """`/v1/chat/completions` 스트리밍 호출. `stream(messages)`는 내용 조각을 차례로 내놓는다."""

    def __init__(self, base_url: str, model: str, key: str | None = None, timeout: float = TIMEOUT_S,
                 extra: dict | None = None):
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.model, self.key, self.timeout, self.extra = model, key, timeout, extra or {}

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """조각 반복자. 끝 표시 없이 연결이 끊기면 `StreamError`."""
        body = {"model": self.model, "messages": messages, "stream": True, "max_tokens": MAX_TOKENS,
                "temperature": 0.3, **self.extra}
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except Exception as exc:   # noqa: BLE001 — 연결·HTTP 오류는 모두 실패로 센다
            if hasattr(exc, "close"):
                exc.close()   # HTTPError는 응답 본문을 들고 있다
            raise StreamError(type(exc).__name__) from None
        done = False
        try:
            with resp:
                for raw in resp:
                    try:
                        piece = parse_sse_line(raw.decode("utf-8"))
                    except ValueError:
                        raise StreamError("bad_sse") from None
                    if piece == "":
                        done = True
                        break
                    if piece:
                        yield piece
        except StreamError:
            raise
        except Exception as exc:   # noqa: BLE001
            raise StreamError(type(exc).__name__) from None
        if not done:
            raise StreamError("disconnected")


# --- 응용: 요청 한 건 ---
def run_request(client, item: FreeQuestion, lexicon: Lexicon, session: int = 0, item_id: str = "",
                clock: Callable[[], float] = time.monotonic, timeout: float = TIMEOUT_S) -> Record:
    """시도 → (첫 문장 실패면) 재생성 1회 → 폴백. 통과 문장만 공개한다."""
    t0 = clock()
    rec = Record(session=session, item_id=item_id, start=t0)
    messages = build_messages(item)
    for attempt in (1, 2):
        rec.attempts = attempt
        gate, parser = SentenceGate(item, lexicon), AnswerStream()
        try:
            result = _one_attempt(client, messages, gate, parser, rec, t0, clock, timeout)
        except StreamError as exc:
            rec.outcome, rec.error, rec.complete = "error", str(exc), INF
            return rec
        except TimeoutError:
            rec.outcome, rec.error, rec.complete = "timeout", "timeout", INF
            return rec
        rec.reasons += gate.reasons
        if result != "fail":
            rec.published = list(gate.published)
            rec.outcome = ("regen_" if attempt == 2 else "") + result
            return rec
    ids, text = fallback(item, lexicon)
    now = clock() - t0
    rec.published, rec.outcome, rec.complete = [text], "fallback", now
    rec.first_display = min(rec.first_display, now)
    return rec


def _one_attempt(client, messages, gate: SentenceGate, parser: AnswerStream, rec: Record, t0: float,
                 clock, timeout: float) -> str:
    for piece in client.stream(messages):
        now = clock() - t0
        if now > timeout:
            raise TimeoutError
        rec.first_token = min(rec.first_token, now)
        for ev in parser.feed(piece):
            act = gate.on_event(ev)
            _mark(rec, act, clock() - t0)
        if gate.failed or gate.partial:
            return gate.finish() if gate.partial else "fail"
    for ev in parser.close():
        _mark(rec, gate.on_event(ev), clock() - t0)
    if clock() - t0 > timeout:
        raise TimeoutError
    return gate.finish()


def _mark(rec: Record, act: str | None, now: float) -> None:
    if act == "publish":
        rec.first_display = min(rec.first_display, now)
        rec.complete = now


# --- 응용: 닫힌 루프 ---
@dataclass
class LoadConfig:
    """닫힌 루프 설정."""
    sessions: int
    think_lo: float
    think_hi: float
    warmup_s: float
    duration_s: float
    seed: int
    timeout: float = TIMEOUT_S


def run_closed_loop(client, items: list[tuple[str, FreeQuestion]], lexicon: Lexicon, cfg: LoadConfig,
                    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                    sample_every: float = 1.0) -> tuple[list[Record], list[dict], float]:
    """(측정 구간 기록, 처리 중 요청 수 표본, 측정 구간 길이). 예열 구간에 시작한 요청은 버린다."""
    t_start = clock()
    t_measure, t_end = t_start + cfg.warmup_s, t_start + cfg.warmup_s + cfg.duration_s
    lock = threading.Lock()
    in_flight = [0]
    records: list[Record] = []
    samples: list[dict] = []
    stop = threading.Event()

    def session(i: int) -> None:
        rng = random.Random(cfg.seed * 1000 + i)
        sleep(rng.uniform(0, cfg.think_hi))   # 시작을 흩뜨린다
        while clock() < t_end:
            item_id, item = rng.choice(items)
            with lock:
                in_flight[0] += 1
            try:
                rec = run_request(client, item, lexicon, i, item_id, clock, cfg.timeout)
            finally:
                with lock:
                    in_flight[0] -= 1
            if t_measure <= rec.start < t_end:
                with lock:
                    records.append(rec)
            sleep(rng.uniform(cfg.think_lo, cfg.think_hi))

    def sampler() -> None:
        while not stop.is_set():
            with lock:
                samples.append({"t": round(clock() - t_start, 3), "in_flight": in_flight[0]})
            stop.wait(sample_every)

    threads = [threading.Thread(target=session, args=(i,), daemon=True) for i in range(cfg.sessions)]
    s = threading.Thread(target=sampler, daemon=True)
    s.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    s.join()
    return records, samples, cfg.duration_s


def _record_json(r: Record) -> dict:
    """결과 JSON용 기록: 시각은 요청 송신 기준 초(무한대는 null), 시계 원점(`start`)은 뺀다."""
    d = asdict(r)
    d.pop("start")
    d.update({k: _num(getattr(r, k)) for k in ("first_token", "first_display", "complete")})
    return d


def load_items(path: Path) -> tuple[list[tuple[str, FreeQuestion]], Lexicon, dict]:
    """평가 세트 JSON → ((item_id, 질문) 목록, 어휘, 원본 머리 정보)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    items = [(it["item_id"], FreeQuestion.from_json(it)) for it in data["items"]]
    head = {k: data.get(k) for k in ("eval_version", "status", "seed", "content_version")}
    return items, Lexicon.from_json(data["lexicon"]), head


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 닫힌 루프 부하를 걸고 결과 JSON을 쓴다."""
    p = argparse.ArgumentParser(description="E-08-13 자유 질문 답변 부하 클라이언트")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--eval", default="fixtures/llm/free_answer_eval.json")
    p.add_argument("--sessions", type=int, default=1)
    p.add_argument("--think", type=float, nargs=2, default=(5.0, 15.0), metavar=("LO", "HI"))
    p.add_argument("--warmup", type=float, default=0.0)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--label", required=True)
    p.add_argument("--out-dir", default="out/perf/E-11-12")
    for k in ("server-version", "image", "revision", "options", "gpu"):
        p.add_argument(f"--{k}", default="미기록")
    a = p.parse_args(argv)
    items, lexicon, head = load_items(Path(a.eval))
    client = OpenAIStream(a.base_url, a.model, os.environ.get(KEY_ENV))
    cfg = LoadConfig(a.sessions, a.think[0], a.think[1], a.warmup, a.duration, a.seed)
    records, samples, wall = run_closed_loop(client, items, lexicon, cfg)
    out = {"label": a.label, "prompt_version": PROMPT_VERSION, "eval": head, "config": asdict(cfg),
           "versions": {"model": a.model, "server_version": a.server_version, "image": a.image,
                        "revision": a.revision, "options": a.options, "gpu": a.gpu},
           "summary": summarize(records, wall), "in_flight": samples,
           "records": [_record_json(r) for r in records]}
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{a.label}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"service": "llm", "component": "free_answer_load", "msg": "부하 측정 완료", "label": a.label,
                      **out["summary"], "path": str(path)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
