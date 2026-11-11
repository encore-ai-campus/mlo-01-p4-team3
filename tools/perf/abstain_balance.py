"""투표 기권 규칙(rule-0.9) 균형 측정(11/11 사용자 결정 ②안): 기권 문턱 후보별 봇 마을 승률.

사용법(저장소 루트):
    python -m tools.perf.abstain_balance --games 1000 --seed 41 --margins none 0.02 0.05 0.1 --bots alibi random
    python -m tools.perf.abstain_balance --games 500 --seed 41 --presets short5 base7 --margins none 0.05 --out X.json

- 구성은 11/10 rule-0.8 측정(A-10-01 1번)과 같다: 콘텐츠 `content/bundle_v1.json`, 정책 `RuleV0Policy(content)`(서비스와
  같은 구성), 의심 모델 `FakeSuspicionModel`(모델 투표 경로), 판 시드 `seed × 100003 + 50000 + i`.
- `none`은 기권 없는 rule-0.8(운영 기본), 숫자는 rule-0.9 판에서 마을 주민 NPC 기권 문턱(`params.ABSTAIN_MARGIN` 자리).
- 봇: `alibi`는 알리바이 봇(LogicBot + 공개 목격으로 결백이 확정된 사람을 투표·의심 대상에서 뒤로 미룸, 창귀 1명 판만),
  `random`·`logic`은 `engine/sim/bots.py`. 봇(무당)은 기권하지 않는다(`legal_commands`가 기권을 만들지 않음).
- 지표: 마을 승률과 Wilson 95% 구간, NPC 기권 비율(NPC 표 중 기권), 무감금 날 비율, 무당 감금 종료 비율, 평균 날 수.

계층: `summarize`·`wilson`(도메인, 판 결과 사전만) / `play`·`run`(응용, 엔진 포트) / `main`(표현).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from engine.domain import params
from engine.domain.engine import AssignmentError, PaldoEngine
from engine.domain.model import ENGINE_VERSION, PLAYER_ID, PRESETS, CommandEnvelope, DataSource, GameMeta
from engine.domain.npc import RuleV0Policy
from engine.domain.observation import witness_evidence
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot, RandomBot
from engine.testing.fakes import FakeSuspicionModel
from tools.check_content import load_bundle
from tools.evidence_result import write_result

ROOT = Path(__file__).resolve().parents[2]
MAX_STEPS = 500


class AlibiBot(LogicBot):
    """LogicBot + 공개 목격으로 결백이 확정된 사람(창귀 1명 판)의 점수를 -100으로(대상 우선순위에서 뺌).

    점수 설정이라 남은 대상이 모두 결백 확정자면 그중에서 고른다. 11/10 rule-0.8 측정의 알리바이 봇과 같은 규칙이다.
    """
    version = "player-logic-alibi-meas"

    def _scores(self, view):
        scores = super()._scores(view)
        for p, _cid in witness_evidence(view.public, PLAYER_ID, 1):   # 측정 프리셋은 모두 창귀 1명
            if p.subject in scores:
                scores[p.subject] = -100
        return scores


BOTS = {"alibi": AlibiBot, "random": RandomBot, "logic": LogicBot}


def game_seed(seed: int, i: int) -> int:
    """판 시드(11/10 rule-0.8 측정과 같은 식)."""
    return seed * 100_003 + 50_000 + i


# ---------------------------------------------------------------- 도메인(집계)
def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """이항 비율 Wilson 95% 구간."""
    if n == 0:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 3), round(c + h, 3)]


def summarize(runs: list[dict]) -> dict:
    """판 결과 사전 목록 → 비율·평균. 배정 실패 판은 따로 센다."""
    ok = [r for r in runs if not r.get("fail")]
    n = len(ok)
    if not n:
        return {"games": 0, "assign_fail": len(runs)}
    wins = sum(r["winner"] == "village" for r in ok)
    npc_ballots = sum(r["npc_ballots"] for r in ok)
    days = sum(r["vote_days"] for r in ok)
    return {
        "games": n, "assign_fail": len(runs) - n,
        "village_win": round(wins / n, 3), "ci95": wilson(wins, n),
        "npc_abstain_rate": round(sum(r["npc_abstains"] for r in ok) / npc_ballots, 3) if npc_ballots else 0.0,
        "no_lock_day_rate": round(sum(r["no_lock_days"] for r in ok) / days, 3) if days else 0.0,
        "mudang_locked_end": round(sum(r["end"] == "mudang_locked" for r in ok) / n, 3),
        "mean_days": round(sum(r["days"] for r in ok) / n, 3),
    }


# ---------------------------------------------------------------- 응용(판 진행)
def play(args: tuple) -> dict:
    """판 하나를 끝까지 진행하고 집계에 쓸 값만 돌려준다. (preset_id, bot, margin, seed)."""
    preset_id, bot_name, margin, seed = args
    content = _content()
    rule_version = params.rule_params_version(params.ABSTAIN_RULE_BASE if margin is not None else "")
    model = FakeSuspicionModel()
    deps = EngineDeps(content, model)
    engine = PaldoEngine(RuleV0Policy(content, abstain_margin=margin))
    gid = f"ab_{preset_id}_{seed}"
    meta = GameMeta(gid, PRESETS[preset_id], seed, DataSource.BOT, rule_version, ENGINE_VERSION,
                    content.content_version, model_versions={"suspicion": model.model_version})
    try:
        r = engine.new_game(meta, deps)
    except AssignmentError:
        return {"fail": True, "seed": seed}
    st, tr = r.state, r.truth
    bot, rng = BOTS[bot_name](), random.Random(seed)
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
    npc_votes = [v for v in st.public.votes if v.voter != PLAYER_ID]
    return {"seed": seed, "winner": st.result.winner.value, "days": st.result.day, "end": st.result.reason.value,
            "npc_ballots": len(npc_votes), "npc_abstains": sum(v.target is None for v in npc_votes),
            "vote_days": len(st.public.locks), "no_lock_days": sum(lk.locked is None for lk in st.public.locks)}


_CONTENT = None


def _content():
    global _CONTENT
    if _CONTENT is None:
        _CONTENT = load_bundle(ROOT / "content/bundle_v1.json")
    return _CONTENT


def run(games: int, seed: int, presets: list[str], margins: list, bots: list[str], workers: int) -> dict:
    """프리셋 × 문턱 × 봇마다 games판을 돌려 요약한다(같은 시드 판끼리 비교)."""
    content = _content()
    out = {"content_version": content.content_version, "decision_params": params.current().to_dict(),
           "rule_versions": {"none": params.rule_params_version(),
                             "abstain": params.rule_params_version(params.ABSTAIN_RULE_BASE)},
           "games": games, "seed": seed, "policy": "RuleV0Policy(content) + FakeSuspicionModel", "runs": {}}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for preset_id in presets:
            for margin in margins:
                for bot in bots:
                    jobs = [(preset_id, bot, margin, game_seed(seed, i)) for i in range(games)]
                    key = f"{preset_id}|{'none' if margin is None else margin}|{bot}"
                    out["runs"][key] = summarize(list(pool.map(play, jobs, chunksize=8)))
                    print(key, json.dumps(out["runs"][key], ensure_ascii=False), round(time.time() - t0), flush=True)
    out["elapsed_s"] = round(time.time() - t0)
    return out


def flat_metrics(result: dict) -> dict:
    """`run` 결과 → 결과 JSON metrics(`<프리셋>_<문턱>_<봇>_<지표>`, 문턱 0.05 → m005, 없음 → none)."""
    out = {}
    for key, summary in result["runs"].items():
        preset_id, margin, bot = key.split("|")
        tag = "none" if margin == "none" else "m" + f"{float(margin):.2f}".replace(".", "").lstrip("0").rjust(3, "0")
        for name, value in summary.items():
            if name == "ci95":
                out[f"{preset_id}_{tag}_{bot}_ci95_low"] = {"value": value[0], "unit": ""}
                out[f"{preset_id}_{tag}_{bot}_ci95_high"] = {"value": value[1], "unit": ""}
            elif isinstance(value, (int, float)):
                out[f"{preset_id}_{tag}_{bot}_{name}"] = {"value": value, "unit": "판" if name in ("games", "assign_fail")
                                                          else ("일" if name == "mean_days" else "")}
    return out


# ---------------------------------------------------------------- 표현
def _margin(x: str):
    return None if x == "none" else float(x)


def main(argv=None) -> int:
    """인자를 읽어 측정하고 결과를 출력한다. `--out`이면 JSON으로도 쓴다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=41)
    ap.add_argument("--presets", nargs="*", default=["base6"])
    ap.add_argument("--margins", nargs="*", default=["none", "0.05"])
    ap.add_argument("--bots", nargs="*", default=["alibi", "random"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out")
    ap.add_argument("--record", nargs=2, metavar=("TASK", "NAME"), help="증빙 결과 JSON(docs/evidence/perf/TASK/NAME.json)")
    a = ap.parse_args(argv)
    res = run(a.games, a.seed, a.presets, [_margin(m) for m in a.margins], a.bots, a.workers)
    res["args"] = vars(a)
    if a.record:
        source = ("python -m tools.perf.abstain_balance --games {} --seed {} --presets {} --margins {} --bots {}"
                  .format(a.games, a.seed, " ".join(a.presets), " ".join(a.margins), " ".join(a.bots)))
        print(write_result(a.record[0], a.record[1], flat_metrics(res), source))
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
