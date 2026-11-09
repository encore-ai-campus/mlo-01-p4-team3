"""사람 자유 대화 터미널 클라이언트(E-11-12 5번, 11/9 사용자 결정: 사람이 10분 동안 NPC와 자유롭게 대화).

게임과 연결하지 않는다. 판 하나의 행적을 시드로 정하고(`llm.free_answer_eval`의 `make_world`·`target_facts`), 말하는 NPC의
허용 사실 = 자기소개 사실(s1 이름, s2 성향) + 사건 사실(f1~)로 질문마다 `free_answer_load.run_request`(시도 → 재생성 1회 →
폴백, 문장마다 검사)를 부른다. 직전 대화는 최근 `MAX_HISTORY_TURNS`턴(질문·답 각 1턴)까지 넣는다.

    set PALDO_LLM_KEY=<세션 키>
    python -m llm.bench.free_chat --base-url http://<사설망 주소>:8000 --model <서버 모델 이름> \
        --npc jumo --seed 1 --minutes 10 --label chat_s1

입력 `/끝` 또는 EOF(Ctrl+Z·Ctrl+D)로 끝나고, `--minutes`가 지나면 그 턴 뒤에 끝난다. 턴마다
`out/perf/E-11-12/<label>.turns.jsonl`에 한 줄씩 바로 쓰고, 끝나면 `<label>.json`에 요약(통과·폴백·오류 비율, 검사가 막은
사유 기록 수, 누출을 막은 턴 수, 공개 문장 재검사 누출 수)을 쓴다. 키는 환경 변수에서만 읽고, 주소는 결과 파일에 남기지 않는다.

계층: `chat_world`·`summarize_chat`·`published_leaks`(규칙 조립, 단독 테스트) / `ChatSession`(응용: 직전 대화 유지와 요청)
/ `run_terminal`(표현: 입출력 루프, 입출력·시계 주입) / `main`(인자 파싱, 인프라 `OpenAIStream` 연결).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Callable, TextIO

from llm.bench.free_answer_load import KEY_ENV, TIMEOUT_S, OpenAIStream, Record, _record_json, run_request
from llm.free_answer import LEAK, PROMPT_VERSION, FreeQuestion, check_sentence, make_context, split_sentences
from llm.free_answer_eval import BUNDLE, make_world, self_facts, target_facts
from llm.validator import Lexicon, lexicon_from_bundle

MAX_HISTORY_TURNS = 8          # 직전 대화 상한(턴 = 질문 또는 답 1번, 질문·답 4쌍)
END_WORDS = ("/끝", "/end")
PASS_OUTCOMES = ("ok", "partial", "regen_ok", "regen_partial")


# --- 규칙 조립 ---
def chat_world(bundle: dict, npc_id: str, seed: int) -> dict:
    """대화 상대 NPC 한 명의 질문 틀(dict, `FreeQuestion.from_json` 형식에서 `question`·`history`만 빈 것)."""
    chars = {c["npc_id"]: c for c in bundle["characters"]}
    if npc_id not in chars:
        raise ValueError(f"없는 NPC: {npc_id} (가능: {', '.join(sorted(chars))})")
    target = chars[npc_id]
    world = make_world(bundle, random.Random(seed), target)
    facts, forbidden = target_facts(bundle, world, target)
    return {"npc_id": npc_id, "npc_name": target["name_ko"],
            "persona": f"{target['name_ko']}. 말투: {target['speech_style']}",
            "facts": self_facts(bundle, target) + [{"fact_id": f["fact_id"], "sentence": f["sentence"]} for f in facts],
            "forbidden_facts": forbidden, "secret_id": world["secret"]["secret_id"] if world["secret"] else None,
            "question": "", "history": []}


def published_leaks(item: FreeQuestion, lexicon: Lexicon, published: list[str]) -> int:
    """공개한 문장을 허용 사실 전체 인용으로 다시 검사해 누출(`leak`) 사유가 나온 문장 수(검사를 통과한 문장이라 0이어야 한다)."""
    ctx = make_context(item, tuple(f.fact_id for f in item.facts), lexicon)
    return sum(any(r == LEAK for r, _ in check_sentence(s, ctx))
               for text in published for s in split_sentences(text))


def summarize_chat(records: list[Record], leaks: list[int], minutes: float) -> dict:
    """세션 요약: 통과(정상·부분·재생성)·폴백·오류 비율, 검사가 막은 사유별 기록 수(한 문장에 같은 사유가
    여럿일 수 있다), 누출을 막은 턴 수, 공개 문장 재검사 누출 수."""
    n = len(records)
    outcomes = Counter(r.outcome for r in records)
    reasons = Counter(x for r in records for x in r.reasons)
    rate = lambda k: (k / n) if n else None   # noqa: E731
    return {"turns": n, "minutes": round(minutes, 2), "outcomes": dict(sorted(outcomes.items())),
            "pass_rate": rate(sum(outcomes[o] for o in PASS_OUTCOMES)),
            "fallback_rate": rate(outcomes["fallback"]),
            "error_rate": rate(outcomes["error"] + outcomes["timeout"]),
            "blocked_reasons": dict(sorted(reasons.items())),
            "leak_blocked_turns": sum(LEAK in r.reasons for r in records),
            "leak_published": sum(leaks)}


# --- 응용: 대화 세션 ---
class ChatSession:
    """NPC 한 명과의 대화. 질문마다 직전 대화(최근 `MAX_HISTORY_TURNS`턴)를 붙여 요청하고 공개한 답을 대화에 더한다."""

    def __init__(self, client, base: dict, lexicon: Lexicon, clock: Callable[[], float] = time.monotonic,
                 timeout: float = TIMEOUT_S):
        self.client, self.base, self.lexicon, self.clock, self.timeout = client, base, lexicon, clock, timeout
        self.history: list[dict] = []
        self.records: list[Record] = []
        self.leaks: list[int] = []

    def ask(self, question: str, on_publish: Callable[[str], None] | None = None) -> tuple[Record, FreeQuestion]:
        """질문 한 번: 요청 기록과 그때 쓴 입력을 돌려준다. 통과한 문장은 그 즉시 `on_publish`로 넘기고, 공개한 답이 있을
        때만 직전 대화에 더한다."""
        item = FreeQuestion.from_json({**self.base, "question": question,
                                       "history": self.history[-(MAX_HISTORY_TURNS // 2):]})
        rec = run_request(self.client, item, self.lexicon, 0, f"turn_{len(self.records) + 1:03d}",
                          self.clock, self.timeout, on_publish)
        self.records.append(rec)
        self.leaks.append(published_leaks(item, self.lexicon, rec.published))
        if rec.published:
            self.history.append({"question": question, "answer": " ".join(rec.published)})
        return rec, item


# --- 표현: 터미널 루프 ---
def run_terminal(session: ChatSession, input_fn: Callable[[str], str], out: TextIO, log: TextIO,
                 minutes: float, clock: Callable[[], float] = time.monotonic) -> float:
    """입력을 받아 답을 출력하고 턴마다 `log`에 JSON 한 줄을 쓴다. 대화한 시간(분)을 돌려준다."""
    name = session.base["npc_name"]
    t0 = clock()
    print(f"[{name}와 대화. 끝내려면 /끝]", file=out, flush=True)
    while True:
        try:
            q = input_fn("무당> ").strip()
        except EOFError:
            break
        if q in END_WORDS:
            break
        if not q:
            continue
        print(f"{name}>", end="", file=out, flush=True)
        # 검사를 통과한 문장을 받는 즉시 보인다(첫 표시 지연과 화면이 같게)
        rec, _ = session.ask(q, lambda s: print(" " + s, end="", file=out, flush=True))
        if not rec.published:
            print(" (답 없음: " + (rec.error or rec.outcome) + ")", end="", file=out)
        print(file=out, flush=True)
        log.write(json.dumps({"question": q, **_record_json(rec), "leak_published": session.leaks[-1]},
                             ensure_ascii=False) + "\n")
        log.flush()
        if clock() - t0 >= minutes * 60:
            print(f"[{minutes:g}분이 지나 대화를 마칩니다]", file=out, flush=True)
            break
    return (clock() - t0) / 60


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 터미널 자유 대화를 열고, 끝나면 요약 JSON을 쓴다."""
    p = argparse.ArgumentParser(description="E-11-12 사람 자유 대화 터미널 클라이언트")
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--npc", default="jumo", help="대화 상대 npc_id(fixtures/content_min 인물)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--minutes", type=float, default=10.0)
    p.add_argument("--label", required=True)
    p.add_argument("--out-dir", default="out/perf/E-11-12")
    a = p.parse_args(argv)
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    base, lexicon = chat_world(bundle, a.npc, a.seed), lexicon_from_bundle(bundle)
    session = ChatSession(OpenAIStream(a.base_url, a.model, os.environ.get(KEY_ENV)), base, lexicon)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / f"{a.label}.turns.jsonl", "w", encoding="utf-8", newline="\n") as log:
        used = run_terminal(session, input, sys.stdout, log, a.minutes)
    summary = summarize_chat(session.records, session.leaks, used)
    doc = {"label": a.label, "prompt_version": PROMPT_VERSION, "model": a.model, "npc_id": a.npc, "seed": a.seed,
           "secret_id": base["secret_id"], "summary": summary}
    path = out_dir / f"{a.label}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"service": "llm", "component": "free_chat", "msg": "자유 대화 요약", **summary,
                      "path": str(path)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
