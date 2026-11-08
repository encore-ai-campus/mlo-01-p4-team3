"""도메인 규칙: 자유 질문 답변 평가 세트 생성(E-08-13 4번). 상태: 사용자 확인 완료(11/8, 새 문장 12개 전부 승인).

`content_min` 콘텐츠 묶음으로 판 하나의 행적(인물별 시간대 장소, 비밀)을 시드로 정하고, 말하는 NPC 시점의 허용 사실
(엔진 투영과 같은 형식: 행적, 목격, 못 봄)과 비공개 사실(비밀의 실제 장소·고백·그 장소의 목격)을 만든다. 비밀을 가진
NPC의 비밀 시간대 행적은 거짓 장소(엔진이 대는 알리바이)가 허용 사실이다.

    python -m llm.free_answer_eval --seed 1     # → fixtures/llm/free_answer_eval.json(같은 시드 = 같은 바이트)

항목 구성: 질문 유형 3종(`whereabouts` 행적 확인, `contradiction` 모순 추궁, `suspicion` 의심 이유) × 대화 길이 3종
(0·2·4턴) × `PER_CELL`건 + 반례 `false_premise`(거짓 전제) 6건·`wrong_history`(오류 섞인 이전 답) 6건.
질문 문장은 E-06-02 의도 예시(`content/intent/examples_v1.jsonl`, 사람 작성 질문형)에서 고른다(`question_source`에 예시 ID).
새로 쓴 문장(거짓 전제 질문, 오류 섞인 이전 답)은 `new_sentences`에 모아 두고 사용자 확인을 받는다(11/8 12개 전부 승인). 생성 결과가 승인본 지문(`APPROVED_NEW_SENTENCES_SHA256`)과 다르면
`status`는 확인 전으로 남고, 새 문장을 다시 확인받은 뒤 지문을 바꾼다.

항목 필드: `item_id`, `qtype`, `kind`(normal|false_premise|wrong_history), `turns`, `npc_id`, `npc_name`, `persona`,
`facts`([{fact_id, sentence}]), `history`([{question, answer}]), `question`, `question_source`, `relevant_fact_ids`
(허용 복수 정답), `answerable`, `forbidden_facts`, `secret_id`. 표준 라이브러리와 `llm`만 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

from llm.eval_set import SLOT_KO, dumps
from llm.validator import lexicon_from_bundle

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "fixtures" / "content_min" / "bundle.json"
EXAMPLES = ROOT / "content" / "intent" / "examples_v1.jsonl"
OUT = ROOT / "fixtures" / "llm" / "free_answer_eval.json"
EVAL_VERSION = "free-answer-eval-1"
# 사용자가 확인한 new_sentences(시드 1, 12개)의 지문. 생성 결과가 이와 같을 때만 확인 완료로 표시한다.
APPROVED_NEW_SENTENCES_SHA256 = "a1d969e8dbc8c1cb840532306416f095725284bac793334a1d9119b42c75a5ab"
STATUS = "사용자 확인 완료(11/8, new_sentences 12개 전부 승인)"
STATUS_PENDING = "사용자 확인 전(new_sentences가 11/8 승인본과 다름: 새 문장 확인 뒤 확정)"
GAME_SIZE = 5
MAX_FACTS = 5
PER_CELL = 7                         # 3 유형 × 3 길이 × 7 = 63건
TURNS = (0, 2, 4)
QTYPES = {"whereabouts": "interrogate_whereabouts", "contradiction": "interrogate_contradiction",
          "suspicion": "interrogate_suspicion_reason"}
SECRET_RATE = 0.35
# 새로 쓴 거짓 전제 질문 틀(사용자 확인 대상). {p1}=말하는 NPC, {p2}=다른 인물, {place}=그 시간대에 없던 장소
FALSE_PREMISE_TEMPLATES = (
    "{p1}, {slot}에 {place}에 있었다는 걸 다 아오. 거기서 뭘 했소?",
    "{p1}, {p2}{i_ga} 창귀라고 자네 입으로 말하지 않았소?",
    "{p1}, {slot}에 {p2}{wa_gwa} {place}에 같이 있었다던데 맞소?",
)
# 외모·신분으로 부르는 예시("거기 수염 난 양반", "그 젊은 아낙")는 말하는 NPC와 안 맞을 수 있어 뺀다
DESCRIPTOR_RE = re.compile(r"양반|아낙|나무꾼|어르신")
WRONG_HISTORY_QUESTIONS = ("ex1_159", "ex1_171", "ex1_139", "ex1_140", "ex1_150", "ex1_174")


def _has_batchim(word: str) -> bool:
    last = word[-1]
    return "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0


def josa(word: str, pair: str) -> str:
    """받침에 맞는 조사(pair는 "은/는", "이/가", "을/를", "과/와")."""
    with_b, without_b = pair.split("/")
    return with_b if _has_batchim(word) else without_b


_JOSA_FIX = (("는", "은/는"), ("은", "은/는"), ("가", "이/가"), ("이", "이/가"), ("를", "을/를"), ("을", "을/를"),
             ("와", "과/와"), ("과", "과/와"))


def fill(template: str, p1: str, p2: str) -> str:
    """의도 예시의 `<P1>`·`<P2>`를 인물 이름으로 바꾸고 바로 뒤 조사를 받침에 맞춘다."""
    out = template
    for tag, name in (("<P1>", p1), ("<P2>", p2)):
        if not name:
            continue
        for j, pair in _JOSA_FIX:
            out = out.replace(tag + j, name + josa(name, pair))
        out = out.replace(tag, name)
    return out


def load_questions(path: Path) -> dict[str, list[dict]]:
    """의도 예시에서 질문 유형별 사람 작성 질문형 예시(정렬된 목록)."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    out: dict[str, list[dict]] = {}
    for qtype, label in QTYPES.items():
        out[qtype] = sorted((r for r in rows if r["label"] == label and r["label_source"] == "authored"
                             and r["form"] == "question" and not DESCRIPTOR_RE.search(r["text"])),
                            key=lambda r: r["example_id"])
    out["_by_id"] = {r["example_id"]: r for r in rows}   # type: ignore[assignment]
    return out


def _place_ko(bundle: dict, place: str) -> str:
    return next(p["name_ko"] for p in bundle["places"] if p["place"] == place)


def _public(bundle: dict, place: str) -> bool:
    return next(p["public"] for p in bundle["places"] if p["place"] == place)


def make_world(bundle: dict, rng: random.Random, target: dict) -> dict:
    """판 하나의 행적: 출연 인물, 날, 인물별 시간대 실제 장소, 말하는 NPC의 비밀(있으면)."""
    chars = sorted(bundle["characters"], key=lambda c: c["npc_id"])
    others = rng.sample([c for c in chars if c["npc_id"] != target["npc_id"]], GAME_SIZE - 1)
    day = rng.randint(1, 3)
    where = {}
    for c in [target, *others]:
        for slot in sorted(SLOT_KO):
            places = sorted(c["routine"][slot])
            where[(c["npc_id"], slot)] = rng.choices(places, weights=[c["routine"][slot][p] for p in places])[0]
    eligible = [s for s in sorted(bundle["secrets"], key=lambda s: s["secret_id"]) if target["npc_id"] in s["eligible_npcs"]]
    secret = rng.choice(eligible) if eligible and rng.random() < SECRET_RATE else None
    if secret:
        where[(target["npc_id"], secret["slot"])] = secret["actual_place"]
    return {"others": others, "day": day, "where": where, "secret": secret}


def target_facts(bundle: dict, world: dict, target: dict) -> tuple[list[dict], list[str]]:
    """말하는 NPC의 (허용 사실, 비공개 사실). 허용 사실 ID는 f1부터, 최대 `MAX_FACTS`개."""
    allowed, forbidden = [], []
    secret = world["secret"]
    sightings = []
    for slot in sorted(SLOT_KO, key=lambda s: s != "evening"):   # 초저녁, 한밤 순서
        when = f"{world['day']}일째 밤 {SLOT_KO[slot]}엔"
        real = world["where"][(target["npc_id"], slot)]
        hidden = bool(secret and secret["slot"] == slot)
        claimed = secret["false_place"] if hidden else real
        allowed.append({"slot": slot, "text": f"{when} {_place_ko(bundle, claimed)}에 있었소."})
        if hidden:
            forbidden.append(f"{when} {_place_ko(bundle, real)}에 있었소.")
        if not _public(bundle, real):
            continue
        seen = [o["name_ko"] for o in world["others"] if world["where"][(o["npc_id"], slot)] == real]
        lines = [f"{when} {_place_ko(bundle, real)}에서 {n}{josa(n, '을/를')} 봤소." for n in seen] or \
                [f"{when} {_place_ko(bundle, real)}에서 아무도 못 봤소."]
        if hidden:
            forbidden += lines
        else:
            sightings += [{"slot": slot, "text": t, "seen": n} for t, n in zip(lines, seen or [None])]
    if secret:
        forbidden.append(secret["confession_line"])
    facts = allowed + sightings[:MAX_FACTS - len(allowed)]
    return [{"fact_id": f"f{i}", "sentence": f["text"], "slot": f["slot"], "seen": f.get("seen")}
            for i, f in enumerate(facts, start=1)], forbidden


def _relevant(qtype: str, example: dict, facts: list[dict], p2: str) -> list[str]:
    whereabouts = [f["fact_id"] for f in facts if "에 있었소." in f["sentence"]]
    if qtype == "whereabouts":
        slot = example["slots"].get("slot")
        ids = [f["fact_id"] for f in facts if f["slot"] == slot] if slot else list(whereabouts)
        if "<P2>" in example["text"]:
            ids += [f["fact_id"] for f in facts if f["seen"] == p2 and f["fact_id"] not in ids]
        return ids
    if qtype == "contradiction":
        return whereabouts
    return [f["fact_id"] for f in facts if f["seen"] == p2]


def _public_facts(facts: list[dict]) -> list[dict]:
    return [{"fact_id": f["fact_id"], "sentence": f["sentence"]} for f in facts]


def _history_question(rng: random.Random, questions: dict, slot: str, name: str) -> str:
    """이전 턴 질문: 행적 확인 예시 중 답 사실의 시간대와 맞거나 시간대가 없는 것(`<P2>` 없는 것)."""
    pool = [q for q in questions["whereabouts"] if "<P2>" not in q["text"] and q["slots"].get("slot") in (slot, None)]
    return fill(rng.choice(pool)["text"], name, "")


def _history(rng: random.Random, questions: dict, facts: list[dict], name: str, turns: int) -> list[dict]:
    """직전 대화: 턴 = 질문 또는 답 한 번이라 2턴 = 질문·답 1쌍. 답은 그 NPC의 허용 사실 문장 그대로."""
    out = []
    for _ in range(turns // 2):
        f = rng.choice(facts)
        out.append({"question": _history_question(rng, questions, f["slot"], name), "answer": f["sentence"]})
    return out


def build_items(bundle: dict, questions: dict, seed: int) -> tuple[list[dict], list[dict]]:
    """(항목 목록, 새로 쓴 문장 목록)."""
    rng = random.Random(seed)
    chars = sorted(bundle["characters"], key=lambda c: c["npc_id"])
    items, new_sentences = [], []

    def base(kind: str, qtype: str, turns: int) -> tuple[dict, dict, list[dict], list[str]]:
        target = rng.choice(chars)
        world = make_world(bundle, rng, target)
        facts, forbidden = target_facts(bundle, world, target)
        item = {"item_id": f"fa_{len(items) + 1:03d}", "qtype": qtype, "kind": kind, "turns": turns,
                "npc_id": target["npc_id"], "npc_name": target["name_ko"],
                "persona": f"{target['name_ko']}. 말투: {target['speech_style']}",
                "facts": _public_facts(facts), "forbidden_facts": forbidden,
                "secret_id": world["secret"]["secret_id"] if world["secret"] else None}
        return item, world, facts, forbidden

    for qtype in QTYPES:
        for turns in TURNS:
            for k in range(PER_CELL):
                # 의심 이유는 절반(짝수 번째)을 목격 사실이 있는 판으로 뽑아 답할 수 있는 항목을 만든다(최대 20회)
                for _ in range(20 if qtype == "suspicion" and k % 2 == 0 else 1):
                    item, world, facts, _ = base("normal", qtype, turns)
                    seen = [f["seen"] for f in facts if f["seen"]]
                    if seen:
                        break
                want_seen = qtype == "suspicion" and k % 2 == 0
                use_seen = bool(seen) and (want_seen or rng.random() < 0.6)
                p2 = rng.choice(seen) if use_seen else rng.choice(world["others"])["name_ko"]
                ex = rng.choice(questions[qtype])
                rel = _relevant(qtype, ex, facts, p2)
                item.update(history=_history(rng, questions, facts, item["npc_name"], turns),
                            question=fill(ex["text"], item["npc_name"], p2),
                            question_source=f"E-06-02:{ex['example_id']}", relevant_fact_ids=rel,
                            answerable=bool(rel))
                items.append(item)
    for i in range(6):
        item, world, facts, _ = base("false_premise", "whereabouts" if i % 3 != 1 else "suspicion", 0)
        slot = rng.choice(sorted(SLOT_KO))
        real = {world["where"][(item["npc_id"], s)] for s in SLOT_KO}
        claimed = {f["sentence"] for f in facts}
        absent = [p for p in sorted(p["place"] for p in bundle["places"] if p["public"]) if p not in real
                  and not any(_place_ko(bundle, p) in c for c in claimed)]
        place = _place_ko(bundle, rng.choice(absent or ["gate"]))
        p2 = rng.choice(world["others"])["name_ko"]
        q = FALSE_PREMISE_TEMPLATES[i % 3].format(p1=item["npc_name"], p2=p2, place=place, slot=SLOT_KO[slot],
                                                  i_ga=josa(p2, "이/가"), wa_gwa=josa(p2, "과/와"))
        rel = [] if i % 3 == 1 else [f["fact_id"] for f in facts if f["slot"] == slot]
        item.update(history=[], question=q, question_source="new", relevant_fact_ids=rel, answerable=bool(rel))
        items.append(item)
        new_sentences.append({"item_id": item["item_id"], "field": "question", "text": q})
    by_id = questions["_by_id"]
    for i in range(6):
        item, world, facts, _ = base("wrong_history", "contradiction", 2)
        f = rng.choice([x for x in facts if "에 있었소." in x["sentence"]])
        wrong_place = rng.choice([p["name_ko"] for p in bundle["places"] if p["name_ko"] not in f["sentence"]])
        cur = next(p["name_ko"] for p in bundle["places"] if f" {p['name_ko']}에 있었소." in f["sentence"])
        wrong = f["sentence"].replace(f" {cur}에 있었소.", f" {wrong_place}에 있었소.")
        ex = by_id[WRONG_HISTORY_QUESTIONS[i]]
        history = [{"question": _history_question(rng, questions, f["slot"], item["npc_name"]), "answer": wrong}]
        p2 = rng.choice(world["others"])["name_ko"]
        item.update(history=history, question=fill(ex["text"], item["npc_name"], p2),
                    question_source=f"E-06-02:{ex['example_id']}", relevant_fact_ids=[f["fact_id"]], answerable=True)
        items.append(item)
        new_sentences.append({"item_id": item["item_id"], "field": "history[0].answer", "text": wrong})
    return items, new_sentences


def sentences_fingerprint(new_sentences: list[dict]) -> str:
    """new_sentences의 sha256(키 정렬·공백 없는 JSON). 승인본과 대조하는 데 쓴다."""
    text = json.dumps(new_sentences, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def status_for(new_sentences: list[dict]) -> str:
    """승인본과 같은 문장이면 확인 완료, 다르면(다른 시드·콘텐츠·틀 변경) 확인 전."""
    return STATUS if sentences_fingerprint(new_sentences) == APPROVED_NEW_SENTENCES_SHA256 else STATUS_PENDING


def build_eval(bundle: dict, questions: dict, seed: int) -> dict:
    """평가 세트 전체(dict). 생성 시각은 넣지 않는다."""
    items, new_sentences = build_items(bundle, questions, seed)
    return {"eval_version": EVAL_VERSION, "status": status_for(new_sentences), "seed": seed, "content_version": bundle["content_version"],
            "bundle": "fixtures/content_min/bundle.json", "intent_examples": "content/intent/examples_v1.jsonl",
            "lexicon": lexicon_from_bundle(bundle).to_json(), "items": items, "new_sentences": new_sentences}


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 평가 세트를 파일로 쓰고 요약 한 줄을 출력한다."""
    p = argparse.ArgumentParser(description="E-08-13 자유 질문 답변 평가 세트 생성")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
    data = build_eval(bundle, load_questions(EXAMPLES), a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(data))
    kinds: dict[str, int] = {}
    for it in data["items"]:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    print(json.dumps({"service": "llm", "component": "free_answer_eval", "msg": "평가 세트 생성", "seed": a.seed,
                      "n_items": len(data["items"]), "kinds": kinds, "new_sentences": len(data["new_sentences"]),
                      "path": out.name}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
