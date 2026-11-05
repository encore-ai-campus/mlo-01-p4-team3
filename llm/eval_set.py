"""도메인 규칙: 추궁 평가 세트 생성(E-07-07 완료 조건 2).

콘텐츠 묶음(인물·비밀·장소·템플릿)에서 추궁 요청을 시드로 고정해 만든다. 같은 명령이면 바이트까지 같은
파일이 나온다(`sort_keys`, 들여쓰기 2, `ensure_ascii=False`, 끝 줄바꿈).

    python -m llm.eval_set --seed 1            # → fixtures/llm/interrogation_eval.json (100건)

파일 구조: 맨 위에 `lexicon`(검증기 어휘), `cases`에 사례 100건. 사례는 평가용 값(`case_id`, `kind`,
`allowed_places`)과 `request`(계약 `RenderRequest` 필드 그대로)로 나눈다. 계약 필드는 늘리지 않는다.

사실 문장 종류(`kind`)와 건수:
- `whereabouts` 40: 실제 행적 "N일째 밤 <시간대>엔 <장소>에 있었소." 또는 "그날 …"(엔진 `t_whereabouts` 형식)
- `false_alibi` 20: 비밀 보유자가 비밀 시간대에 대는 거짓 장소(같은 형식, 회피)
- `sighting` 15: "… <장소>에서 <인물>을(를) 봤소."
- `non_sighting` 10: "… <장소>에서 아무도 못 봤소."(부정이 사실에 들어 있는 경우)
- `confession` 15: 비밀의 `confession_line`(고백)

표준 라이브러리와 `llm`만 쓴다. 로그는 README 5절 형식 JSON 한 줄(표준 출력 + `out/logs/llm.jsonl`).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from llm.validator import fact_elements, lexicon_from_bundle

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "fixtures" / "content_min" / "bundle.json"
OUT = ROOT / "fixtures" / "llm" / "interrogation_eval.json"
EVAL_VERSION = "interrogation-eval-1"
GAME_SIZE = 5                 # 한 사례의 주민 수(6인 판 = 무당 + 주민 5). allowed_names = 이 5명
KIND_COUNTS = (("whereabouts", 40), ("false_alibi", 20), ("sighting", 15), ("non_sighting", 10), ("confession", 15))
SLOT_KO = {"evening": "초저녁", "midnight": "한밤"}
REACTIONS_FOR = {
    "whereabouts": ("explain", "evade", "counter"),
    "false_alibi": ("evade", "explain", "counter"),
    "sighting": ("explain", "evade"),
    "non_sighting": ("explain", "evade", "counter"),
    "confession": ("confess",),
}


def object_particle(word: str) -> str:
    """목적격 조사: 받침이 있으면 '을', 없으면 '를'."""
    last = word[-1]
    if "가" <= last <= "힣":
        return "을" if (ord(last) - 0xAC00) % 28 else "를"
    return "을(를)"


def _when(rng: random.Random, slot: str) -> str:
    """시간 머리말: 엔진 형식 'N일째 밤 <시간대>엔' 또는 계약 예시 형식 '그날 <시간대>엔'."""
    if rng.random() < 0.5:
        return f"{rng.randint(1, 3)}일째 밤 {SLOT_KO[slot]}엔"
    return f"그날 {SLOT_KO[slot]}엔"


def _fallback(bundle: dict, npc: dict, reaction: str, fact: str) -> str:
    """엔진과 같은 규칙의 폴백: interrogate 템플릿 중 반응이 같고 성향이 없거나 같은 첫 템플릿, 없으면 사실 문장."""
    for t in bundle["templates"]:
        if t["kind"] == "interrogate" and t.get("reaction") == reaction \
                and t.get("temperament_id") in (None, npc["temperament_id"]):
            return t["variants"][0].format(fact=fact)
    return fact


def _place_ko(bundle: dict, place: str) -> str:
    return next(p["name_ko"] for p in bundle["places"] if p["place"] == place)


def build_cases(bundle: dict, seed: int, n: int = 100) -> list[dict]:
    """콘텐츠 묶음에서 사례 n건(기본 100)을 만든다. 종류별 건수는 `KIND_COUNTS` 비율을 n에 맞춘다."""
    rng = random.Random(seed)
    lexicon = lexicon_from_bundle(bundle)
    chars = sorted(bundle["characters"], key=lambda c: c["npc_id"])
    by_id = {c["npc_id"]: c for c in chars}
    secrets = sorted(bundle["secrets"], key=lambda s: s["secret_id"])
    total = sum(c for _, c in KIND_COUNTS)
    kinds: list[str] = []
    for kind, count in KIND_COUNTS:
        kinds += [kind] * round(count * n / total)
    kinds = (kinds + [KIND_COUNTS[0][0]] * n)[:n]
    cases = []
    for i, kind in enumerate(kinds, start=1):
        if kind in ("false_alibi", "confession"):
            secret = rng.choice(secrets)
            npc = by_id[rng.choice(sorted(secret["eligible_npcs"]))]
        else:
            secret = None
            npc = rng.choice(chars)
        others = rng.sample([c for c in chars if c["npc_id"] != npc["npc_id"]], GAME_SIZE - 1)
        cast = sorted([npc, *others], key=lambda c: c["npc_id"])
        if kind == "whereabouts":
            slot = rng.choice(sorted(npc["routine"]))
            place = rng.choice(sorted(npc["routine"][slot]))
            fact = f"{_when(rng, slot)} {_place_ko(bundle, place)}에 있었소."
        elif kind == "false_alibi":
            fact = f"{_when(rng, secret['slot'])} {_place_ko(bundle, secret['false_place'])}에 있었소."
        elif kind == "sighting":
            slot = rng.choice(sorted(SLOT_KO))
            place_ko = _place_ko(bundle, rng.choice(sorted(p["place"] for p in bundle["places"] if p["public"])))
            seen = rng.choice(others)["name_ko"]
            fact = f"{_when(rng, slot)} {place_ko}에서 {seen}{object_particle(seen)} 봤소."
        elif kind == "non_sighting":
            slot = rng.choice(sorted(SLOT_KO))
            place_ko = _place_ko(bundle, rng.choice(sorted(p["place"] for p in bundle["places"] if p["public"])))
            fact = f"{_when(rng, slot)} {place_ko}에서 아무도 못 봤소."
        else:
            fact = secret["confession_line"]
        reaction = rng.choice(REACTIONS_FOR[kind])
        request = {
            "job_id": f"ev_{i:03d}",
            "game_id": f"g_eval_s{seed}",
            "state_version": i,
            "speech_id": f"sp_ev_{i:03d}",
            "npc_id": npc["npc_id"],
            "persona": f"{npc['name_ko']}. 말투: {npc['speech_style']}",
            "reaction": reaction,
            "fact_sentence": fact,
            "allowed_names": [c["name_ko"] for c in cast],
            "fallback_text": _fallback(bundle, npc, reaction, fact),
            "priority": 0,
            "deadline_ms": 15000,
        }
        cases.append({
            "case_id": f"ev_{i:03d}",
            "kind": kind,
            "secret_id": secret["secret_id"] if secret else None,
            "allowed_places": sorted(fact_elements(fact, lexicon).places),
            "request": request,
        })
    return cases


def build_eval_set(bundle: dict, seed: int, n: int = 100, bundle_path: str = "fixtures/content_min/bundle.json") -> dict:
    """평가 세트 전체(dict). 생성 시각은 넣지 않는다(같은 명령 = 같은 파일)."""
    return {
        "eval_version": EVAL_VERSION,
        "seed": seed,
        "content_version": bundle["content_version"],
        "bundle": bundle_path,
        "lexicon": lexicon_from_bundle(bundle).to_json(),
        "cases": build_cases(bundle, seed, n),
    }


def dumps(data: dict) -> str:
    """고정 직렬화: sort_keys, 들여쓰기 2, ensure_ascii=False, 끝 줄바꿈 1개."""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _log(level: str, msg: str, **extra) -> None:
    """README 5절 구조화 로그 한 줄(표준 출력 + `out/logs/llm.jsonl`). tools/jsonlog를 쓰지 않는 이유:
    이 모듈은 표준 라이브러리와 llm만 import한다(test_layers)."""
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "level": level,
              "service": "llm", "component": "eval_set", "msg": msg, **extra}
    line = json.dumps(record, ensure_ascii=False)
    print(line, flush=True)
    log_dir = Path(os.environ.get("PALDO_LOG_DIR", ROOT / "out" / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "llm.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(json.dumps({**record, "level": "warning", "msg": "로그 파일 쓰기 실패",
                          "error_type": type(exc).__name__}, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 평가 세트를 만들고 파일로 쓴다."""
    p = argparse.ArgumentParser(description="E-07-07 추궁 평가 세트 생성")
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--bundle", default=str(BUNDLE))
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args(argv)
    bundle_path = Path(a.bundle)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    try:
        rel = bundle_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        rel = bundle_path.name
    data = build_eval_set(bundle, a.seed, a.n, rel)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(data))
    counts = {}
    for c in data["cases"]:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    _log("info", "평가 세트 생성", seed=a.seed, n_cases=len(data["cases"]), kinds=counts,
         content_version=data["content_version"], path=str(out.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
