"""선택형 도전 승률 측정(E-09-08): 기본 6인(base6)과 도전 프리셋 3종을 같은 판 시드로 돌려 비교한다.

사용법(저장소 루트):
    python -m tools.perf.challenge_balance --games 300 --seed 9 --record E-09-08

- 엔진 구성은 투표 균형 측정(`tools/perf/vote_balance.py`)과 같다: 실제 콘텐츠 `content/bundle_v1.json`,
  규칙 정책, 서버 조립과 같은 `FakeSuspicionModel`. 봇은 `logic`·`random`·`passive`(사람 승률의 검증된 대역 아님).
- 검사 실패 = 배정 검증기 위반(`count_violations`, 같은 판 시드) + 판 진행 중 명령 거부·예외·명령 상한 초과.
- 공개 판정(P-08, 11/8): 봇마다 도전 프리셋의 마을 승률 점추정이 base6보다 낮고 검사 실패가 0건이면 "공개".
  세 봇 모두 만족해야 "공개", 하나라도 아니면 "PM 판단"으로 둔다(어느 봇 기준인지 P-08에 적혀 있지 않아 보수적으로).

계층: `wilson`·`summarize`·`verdict`(도메인, 숫자·사전만) / `play`(응용, 엔진 포트) / `main`(표현).
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import (
    CHALLENGE_PRESET_IDS, ENGINE_VERSION, PRESETS, CommandEnvelope, DataSource, GameMeta,
)
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel
from tools.check_content import count_violations, load_bundle
from tools.evidence_result import write_result
from tools.perf.vote_balance import BOTS, game_seed

ROOT = Path(__file__).resolve().parents[2]
MAX_STEPS = 500
BASE = "base6"
SOURCE = "python -m tools.perf.challenge_balance --games {games} --seed {seed} --record {task}"


# ---------------------------------------------------------------- 도메인(집계·판정)
def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """이항 비율의 95% Wilson 구간. n=0이면 (0, 0)."""
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)


def summarize(runs: list[dict]) -> dict:
    """판 결과 사전 목록 → 마을 승률·구간·평균 날 수·무감금 날 비율·검사 실패 수."""
    done = [r for r in runs if r.get("error") is None]
    n = len(done)
    wins = sum(r["winner"] == "village" for r in done)
    lo, hi = wilson(wins, n)
    vote_days = sum(r["vote_days"] for r in done)
    return {
        "games": len(runs),
        "completed": n,
        "village_win": round(wins / n, 3) if n else 0.0,
        "village_win_lo": lo,
        "village_win_hi": hi,
        "mean_days": round(sum(r["days"] for r in done) / n, 3) if n else 0.0,
        "max_days": max((r["days"] for r in done), default=0),
        "no_lock_day_rate": round(sum(r["no_lock_days"] for r in done) / vote_days, 3) if vote_days else 0.0,
        "mean_divinations": round(sum(r["divinations"] for r in done) / n, 3) if n else 0.0,
        "mean_confessions": round(sum(r["confessions"] for r in done) / n, 3) if n else 0.0,
        "run_failures": len(runs) - n,
    }


def verdict(base: dict, challenge: dict, assignment_failures: int) -> dict:
    """P-08 공개 판정. 봇별 '점추정이 기본보다 낮음'과 검사 실패 0건."""
    lower = {bot: challenge[bot]["village_win"] < base[bot]["village_win"] for bot in challenge}
    failures = assignment_failures + sum(s["run_failures"] for s in challenge.values())
    public = failures == 0 and all(lower.values())
    return {"lower_than_base": lower, "check_failures": failures, "decision": "공개" if public else "PM 판단"}


def flat_metrics(result: dict) -> dict:
    """결과 → 결과 JSON metrics(`<프리셋>_<봇>_<지표>`)."""
    out = {}
    for pid, block in result["presets"].items():
        key = pid.replace("-", "_")
        for bot, summary in block["bots"].items():
            for name in ("village_win", "village_win_lo", "village_win_hi", "mean_days", "max_days",
                         "no_lock_day_rate", "mean_divinations", "mean_confessions"):
                out[f"{key}_{bot}_{name}"] = {"value": summary[name], "unit": "day" if "days" in name else ""}
        out[f"{key}_check_failures"] = {"value": block["check_failures"], "unit": "count"}
    return out


# ---------------------------------------------------------------- 응용(판 진행)
def play(engine: PaldoEngine, deps: EngineDeps, preset_id: str, bot, seed: int, gid: str) -> dict:
    """판 하나를 끝까지 둔다. 거부·예외·상한 초과는 `error`로 돌려 검사 실패로 센다."""
    content = deps.content
    meta = GameMeta(gid, PRESETS[preset_id], seed, DataSource.BOT, params.RULE_PARAMS_VERSION, ENGINE_VERSION,
                    content.content_version, model_versions={"suspicion": FakeSuspicionModel.model_version})
    try:
        r = engine.new_game(meta, deps)
        st, tr = r.state, r.truth
        rng = random.Random(seed)
        for _ in range(MAX_STEPS):
            if st.result is not None:
                break
            cmd = bot.choose(engine.player_view(st, content), rng)
            r = engine.apply(st, tr, CommandEnvelope(gid, f"c{st.state_version:05d}", st.state_version, cmd), deps)
            if not r.accepted:
                return {"error": f"reject {cmd.kind.value} {r.reject}"}
            st, tr = r.state, r.truth
        if st.result is None:
            return {"error": "max_steps"}
    except Exception as e:  # noqa: BLE001 - 측정 도구는 예외도 검사 실패로 센다
        return {"error": f"{type(e).__name__}: {e}"}
    return {
        "error": None, "winner": st.result.winner.value, "days": st.result.day,
        "vote_days": len(st.public.locks), "no_lock_days": sum(lk.locked is None for lk in st.public.locks),
        "divinations": sum(len(v) for v in st.player.divinations.values()),
        "confessions": len(st.public.confessions),
    }


def run(games: int, seed: int, bots: list[str], presets: list[str]) -> dict:
    """프리셋·봇마다 `games`판(같은 판 시드)을 돌려 요약하고 도전 프리셋을 판정한다."""
    content = load_bundle(ROOT / "content/bundle_v1.json")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine(RuleV0Policy(content))
    seeds = [game_seed(seed, i) for i in range(games)]
    out = {"rule_version": params.RULE_PARAMS_VERSION, "content_version": content.content_version,
           "games": games, "seed": seed, "presets": {}}
    for pid in presets:
        bad = count_violations(content, pid, seeds)
        block = {"assignment_failures": sum(bad.values()), "bots": {}}
        for name in bots:
            bot = BOTS[name]()
            runs = [play(engine, deps, pid, bot, s, f"cb_{pid}_{name}_{i:05d}") for i, s in enumerate(seeds)]
            block["bots"][name] = summarize(runs)
            block.setdefault("errors", []).extend(r["error"] for r in runs if r["error"])
        block["check_failures"] = block["assignment_failures"] + sum(
            s["run_failures"] for s in block["bots"].values())
        block["errors"] = sorted(set(block.get("errors", [])))[:10]
        out["presets"][pid] = block
    base = out["presets"].get(BASE)
    if base:
        for pid in presets:
            if pid != BASE:
                out["presets"][pid]["verdict"] = verdict(base["bots"], out["presets"][pid]["bots"],
                                                         out["presets"][pid]["assignment_failures"])
    return out


# ---------------------------------------------------------------- 표현
def main(argv=None) -> int:
    """인자를 읽어 측정하고 결과를 출력한다. `--record`면 증빙 결과 JSON도 쓴다."""
    p = argparse.ArgumentParser(description="선택형 도전 승률 측정(E-09-08)")
    p.add_argument("--games", type=int, default=300)
    p.add_argument("--seed", type=int, default=9)
    p.add_argument("--bots", default="logic,random,passive")
    p.add_argument("--record", metavar="TASK", help="결과 JSON을 docs/evidence/perf/<TASK>/challenge_balance.json에 쓴다")
    a = p.parse_args(argv)
    result = run(a.games, a.seed, [b for b in a.bots.split(",") if b], [BASE, *CHALLENGE_PRESET_IDS])
    if a.record:
        write_result(a.record, "challenge_balance", flat_metrics(result),
                     SOURCE.format(games=a.games, seed=a.seed, task=a.record))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
