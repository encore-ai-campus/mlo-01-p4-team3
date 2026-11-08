"""투표 균형 측정(A-09-06): 봇별 마을 승률·1일째 무당 감금 비율·평균 날 수·1일째 NPC 침묵 비율.

사용법(저장소 루트):
    python -m tools.perf.vote_balance --games 200 --seed 7 --record A-09-06

- 콘텐츠는 서버와 같은 `content/bundle_v1.json`, 의심 모델은 서버 조립과 같은 `FakeSuspicionModel`, 프리셋 base6.
- 봇: `logic`·`random`은 `engine/sim/bots.py`. `passive`는 "별 행동 없이 투표만 하는 사람" 대역이다
  (말 차례 침묵, 캐묻기 안 함, 투표·밤 점은 무작위). 사람 승률의 검증된 대역은 아니다.
- 판 시드는 `game_seed(seed, i)`(run_engine_games와 같은 식)라 같은 인자면 같은 결과다.

계층: `summarize`(도메인, 판 결과 사전만) / `play`(응용, 엔진 포트) / `main`(표현).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import (
    ENGINE_VERSION, PLAYER_ID, PRESETS, CommandEnvelope, CommandKind, DataSource, GameMeta,
)
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot, RandomBot, legal_commands
from engine.testing.fakes import FakeSuspicionModel
from tools.check_content import load_bundle
from tools.evidence_result import write_result

ROOT = Path(__file__).resolve().parents[2]
MAX_STEPS = 500
SOURCE = "python -m tools.perf.vote_balance --games {games} --seed {seed} --record {task}"


class PassiveBot:
    """말 차례는 침묵, 캐묻기는 하지 않고 넘어가며, 투표·밤 점 대상은 무작위로 고른다."""
    version = "player-passive-v0"

    def choose(self, view, rng: random.Random):
        choices = legal_commands(view)
        if not choices:
            raise ValueError("no legal player command")
        for kind in (CommandKind.SILENCE, CommandKind.ADVANCE):
            group = [c for c in choices if c.kind is kind]
            if group:
                return group[0]
        for kind in (CommandKind.VOTE, CommandKind.DIVINE):
            group = [c for c in choices if c.kind is kind]
            if group:
                return rng.choice(group)
        return rng.choice(choices)


BOTS = {"logic": LogicBot, "random": RandomBot, "passive": PassiveBot}


def game_seed(seed: int, i: int) -> int:
    """판 시드(run_engine_games와 같은 식)."""
    return seed * 100_003 + i


# ---------------------------------------------------------------- 도메인(집계)
def summarize(runs: list[dict]) -> dict:
    """판 결과 사전 목록 → 비율·평균. 판이 없으면 빈 사전."""
    n = len(runs)
    if not n:
        return {}
    npc_d1 = sum(r["npc_speeches_d1"] for r in runs)
    return {
        "games": n,
        "village_win": round(sum(r["winner"] == "village" for r in runs) / n, 3),
        "mudang_locked": round(sum(r["mudang_locked"] for r in runs) / n, 3),
        "mudang_locked_d1": round(sum(r["mudang_locked_day"] == 1 for r in runs) / n, 3),
        "mean_days": round(sum(r["days"] for r in runs) / n, 3),
        "silence_rate_d1": round(sum(r["npc_silence_d1"] for r in runs) / npc_d1, 3) if npc_d1 else None,
        "npc_suspect_npc_d1_per_game": round(sum(r["npc_suspect_npc_d1"] for r in runs) / n, 3),
    }


def flat_metrics(result: dict) -> dict:
    """`run` 결과 → 결과 JSON metrics(`<봇>_<지표>`, 비율·날 수는 단위 없음)."""
    out = {}
    for bot, summary in result["bots"].items():
        for key, value in summary.items():
            if key != "games" and value is not None:
                out[f"{bot}_{key}"] = {"value": value, "unit": ""}
    return out


# ---------------------------------------------------------------- 응용(판 진행)
def play(engine: PaldoEngine, deps: EngineDeps, bot, seed: int, gid: str) -> dict:
    """판 하나를 끝까지 진행하고 집계에 쓸 값만 돌려준다."""
    content = deps.content
    meta = GameMeta(gid, PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION, ENGINE_VERSION,
                    content.content_version, model_versions={"suspicion": FakeSuspicionModel.model_version})
    r = engine.new_game(meta, deps)
    st, tr = r.state, r.truth
    rng = random.Random(seed)
    for _ in range(MAX_STEPS):
        if st.result is not None:
            break
        cmd = bot.choose(engine.player_view(st, content), rng)
        r = engine.apply(st, tr, CommandEnvelope(gid, f"c{st.state_version:05d}", st.state_version, cmd), deps)
        if not r.accepted:
            raise RuntimeError(f"플레이어 명령 거부: {gid} {cmd.kind.value} {r.reject}")
        st, tr = r.state, r.truth
    if st.result is None:
        raise RuntimeError(f"판이 {MAX_STEPS}명령 안에 끝나지 않음: {gid}")
    lock_day = next((lk.day for lk in st.public.locks if lk.locked == PLAYER_ID), None)
    d1 = [s for s in st.public.speeches if s.day == 1 and s.speaker != PLAYER_ID
          and s.kind is not CommandKind.INTERROGATE]
    return {
        "winner": st.result.winner.value, "days": st.result.day,
        "mudang_locked": lock_day is not None, "mudang_locked_day": lock_day,
        "npc_speeches_d1": len(d1),
        "npc_silence_d1": sum(s.kind is CommandKind.SILENCE for s in d1),
        "npc_suspect_npc_d1": sum(s.kind is CommandKind.SUSPECT and s.target not in (None, PLAYER_ID) for s in d1),
    }


def run(games: int, seed: int, bots: list[str]) -> dict:
    """봇마다 `games`판을 돌려 요약한다."""
    content = load_bundle(ROOT / "content/bundle_v1.json")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine(RuleV0Policy())
    out = {"rule_version": params.RULE_PARAMS_VERSION, "content_version": content.content_version,
           "preset": "base6", "games": games, "seed": seed, "bots": {}}
    for name in bots:
        bot = BOTS[name]()
        runs = [play(engine, deps, bot, game_seed(seed, i), f"vb_{name}_{i:05d}") for i in range(games)]
        out["bots"][name] = summarize(runs)
    return out


# ---------------------------------------------------------------- 표현
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="투표 균형 측정(A-09-06)")
    p.add_argument("--games", type=int, default=200)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--bots", default="logic,random,passive")
    p.add_argument("--record", metavar="TASK", help="결과 JSON을 docs/evidence/perf/<TASK>/vote_balance.json에 쓴다")
    a = p.parse_args(argv)
    result = run(a.games, a.seed, [b for b in a.bots.split(",") if b])
    if a.record:
        write_result(a.record, "vote_balance", flat_metrics(result),
                     SOURCE.format(games=a.games, seed=a.seed, task=a.record))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
