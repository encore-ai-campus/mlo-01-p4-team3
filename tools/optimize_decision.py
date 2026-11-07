"""결정 층 최적화(A-08-03): 플레이어 대역 봇 vs NPC 규칙 v0 판을 돌려 목표 마을 승률 구간에 맞는 파라미터를 찾는다.

    python -m tools.optimize_decision --seed 11 --workers 8 --out out/a0803 --write-params --record A-08-03
    python -m tools.optimize_decision --target 0.65 0.75 --params-out out/difficulty/easy.json --version decision-easy-1   # 난이도별(예)

탐색 대상(`engine/domain/params.py` `DecisionParams`): 동조 크기, 투표 잡음, q_max, 목격 확률, 기색 확률, 동선 무작위성.
피해 장소 공개는 확정 규칙이라 켠 상태로만 돈다(끄는 비교 실행은 하지 않는다).

절차(시드 고정, 같은 명령이면 같은 결과):
1. 선별: 격자(`SPACE`)에서 `--samples`개를 시드로 뽑고 규칙 v0 기준값을 더해 후보마다 `--screen-games`판.
2. 확인: 목표 구간 중앙에 가까운 상위 `--top`개를 다른 시드로 `--confirm-games`판.
3. 선택: 확인 승률이 목표 구간 안인 후보 중 중앙과의 거리가 가장 작은 것(같으면 기준값과 가까운 것).
4. 최종: 선택한 값으로 또 다른 시드에서 `--final-games`판(합격 판정, 95% 구간, 마을 추론 위반 수).
최종 승률이 구간 밖이거나 추론 위반이 있으면 `상태: 실패`로 보고 `decision_params.json`은 그대로 둔다(A-08-03 조건 5·6).

목표 구간(`--target LOW HIGH`)은 마을(플레이어 대역 봇) 승률이다. 기본 0.40~0.60(README 6절 합격선)이고, 난이도별
값(예: 쉬움 0.70 근처, 어려움 0.30 근처)도 같은 명령에 구간만 바꿔 찾을 수 있다. 의심 모델은 바꾸지 않는다.

판 i의 시드는 `단계 시드 * 1000000 + i`, 단계 시드는 선별 `seed*10+1`, 확인 `seed*10+2`, 최종 `seed*10+3`.
병렬 실행은 판 묶음을 프로세스에 나눠 돌리고(프로세스마다 `params.apply`), 결과는 판 순서와 무관하게 합산한다.

계층: 표현·응용(인자 파싱, 판 실행 조립, 병렬 실행, 파일 쓰기). 탐색·선택·구간 계산은 아래 순수 함수이고,
게임 규칙은 `engine/domain/`에 있다.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import shlex
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import PLAYER_ID, PRESETS, DataSource, GameMeta, Role
from engine.domain.npc import RuleV0Policy
from engine.domain.npc.beliefs import vote_conflicts
from engine.ports import EngineDeps
from engine.sim.bots import BOT_TYPES
from engine.sim.runner import run_game
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.evidence_result import write_result
from tools.jsonlog import log

COMPONENT = "optimize_decision"
SEED_STRIDE = 1_000_000
DEFAULT_TARGET = (0.40, 0.60)
PARAM_NAMES = ("conformity_scale", "vote_noise", "q_max", "witness_prob", "tell_prob", "routine_randomness")

# 탐색 격자(증빙 "탐색 대상과 범위" 표의 원본). 규칙 v0 기준값을 모두 포함한다.
SPACE: dict[str, tuple[float, ...]] = {
    "conformity_scale": (0.1, 0.2, 0.3, 0.4),   # 0은 동조 항을 끄므로 넣지 않는다(성향 conformity·이유 코드 vote_conform 유지)
    "vote_noise": (0.02, 0.05, 0.1, 0.2, 0.3),
    "q_max": (0.3, 0.5, 0.6, 0.7, 0.8, 0.9),
    "witness_prob": (0.3, 0.4, 0.5, 0.6, 0.7),
    "tell_prob": (0.2, 0.3, 0.4, 0.5),
    "routine_randomness": (0.0, 0.25, 0.5),
}


# ---------------------------------------------------------------- 순수 함수(탐색·선택·구간)
def sample_grid(space: dict[str, tuple[float, ...]], n: int, rng: random.Random) -> list[dict[str, float]]:
    """격자에서 서로 다른 점 n개를 시드로 뽑는다(격자 크기보다 크면 격자 전부). 순서는 뽑은 순서."""
    total = math.prod(len(v) for v in space.values())
    n = min(n, total)
    seen: set[tuple] = set()
    out: list[dict[str, float]] = []
    while len(out) < n:
        point = {k: rng.choice(v) for k, v in space.items()}
        key = tuple(point[k] for k in space)
        if key not in seen:
            seen.add(key)
            out.append(point)
    return out


def wilson(wins: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """윌슨 95% 구간. n=0이면 (0, 1)."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def distance_to_base(point: dict[str, float], base: dict[str, float], space: dict[str, tuple[float, ...]]) -> float:
    """기준값과의 거리(각 축을 격자 폭으로 나눈 절댓값 합). 동점 후보 중 덜 바꾼 쪽을 고르는 데 쓴다."""
    out = 0.0
    for k, v in space.items():
        width = (max(v) - min(v)) or 1.0
        out += abs(point[k] - base[k]) / width
    return out


def rank_key(win_rate: float, target: tuple[float, float], point: dict[str, float], base: dict[str, float],
             space: dict[str, tuple[float, ...]]) -> tuple[float, float]:
    """정렬 키: (목표 중앙과의 거리, 기준값과의 거리). 작을수록 좋다."""
    mid = (target[0] + target[1]) / 2
    return abs(win_rate - mid), distance_to_base(point, base, space)


def in_target(win_rate: float, target: tuple[float, float]) -> bool:
    """승률이 목표 구간(양끝 포함) 안인가."""
    return target[0] <= win_rate <= target[1]


def choose(confirmed: list[dict], target: tuple[float, float], base: dict[str, float],
           space: dict[str, tuple[float, ...]]) -> Optional[dict]:
    """확인 단계 결과 중 목표 구간 안이며 정렬 키가 가장 작은 후보. 없으면 None."""
    ok = [c for c in confirmed if in_target(c["win_rate"], target)]
    if not ok:
        return None
    return min(ok, key=lambda c: rank_key(c["win_rate"], target, c["point"], base, space))


# ---------------------------------------------------------------- 판 실행(응용)
class CheckedPolicy:
    """`RuleV0Policy`를 감싸 마을 주민 표의 추론 위반(확정 명제와 충돌, `vote_conflicts`)과 안전 층 대체 표를 센다."""

    def __init__(self, inner: RuleV0Policy):
        self.inner = inner
        self.version = inner.version
        self.villager_votes = 0
        self.violations = 0
        self.fallback_votes = 0

    def _check(self, obs, decision) -> None:
        if obs.private.role is not Role.VILLAGER or obs.observer == PLAYER_ID:
            return
        self.villager_votes += 1
        if vote_conflicts(obs.beliefs, decision.value):
            self.violations += 1
        if {"fallback_all_legal", "fallback_max_suspicion"} & {getattr(r, "value", r) for r in decision.reason_codes}:
            self.fallback_votes += 1

    def update_beliefs(self, obs):
        """믿음 갱신(그대로 넘김)."""
        return self.inner.update_beliefs(obs)

    def talk(self, obs, rng):
        """회의 말 행동(그대로 넘김)."""
        return self.inner.talk(obs, rng)

    def vote(self, obs, candidates, rng):
        """규칙 v0 투표(검사 후 넘김)."""
        d = self.inner.vote(obs, candidates, rng)
        self._check(obs, d)
        return d

    def vote_with_model(self, obs, candidates, rng, model, clock=None):
        """투표 3층(검사 후 넘김)."""
        out = self.inner.vote_with_model(obs, candidates, rng, model, clock)
        self._check(obs, out.decision)
        return out

    def night_plan(self, obs, executors, victims, rng):
        """밤 명령(그대로 넘김)."""
        return self.inner.night_plan(obs, executors, victims, rng)

    def react(self, obs, cmd, rng):
        """캐묻기 답(그대로 넘김)."""
        return self.inner.react(obs, cmd, rng)


@dataclass
class Tally:
    """판 묶음 결과 합계."""
    games: int = 0
    village_wins: int = 0
    days: int = 0
    villager_votes: int = 0
    violations: int = 0
    fallback_votes: int = 0

    def add(self, other: "Tally") -> None:
        """다른 합계를 더한다."""
        for k in self.__dataclass_fields__:
            setattr(self, k, getattr(self, k) + getattr(other, k))


def run_chunk(job: tuple[dict, str, str, int, int, int]) -> Tally:
    """판 묶음 하나(프로세스 작업 단위): 파라미터를 적용하고 판 [start, start+count)를 돌린다."""
    point, preset_id, bot_name, stage_seed, start, count = job
    params.apply(to_params(point, "search"))
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    policy = CheckedPolicy(RuleV0Policy(content))
    engine = PaldoEngine(policy)
    bot_type = BOT_TYPES[bot_name]
    t = Tally()
    for i in range(start, start + count):
        meta = GameMeta(f"opt_{preset_id}_{stage_seed}_{i:05d}", PRESETS[preset_id], stage_seed * SEED_STRIDE + i,
                        DataSource.BOT, params.RULE_PARAMS_VERSION, "paldo-engine", content.content_version,
                        model_versions={"suspicion": FakeSuspicionModel.model_version, "player_bot": bot_type.version})
        r = run_game(engine, deps, meta, bot_type())
        t.games += 1
        t.village_wins += r.winner == "village"
        t.days += r.days
    t.villager_votes, t.violations, t.fallback_votes = policy.villager_votes, policy.violations, policy.fallback_votes
    return t


def to_params(point: dict[str, float], version: str) -> params.DecisionParams:
    """탐색 점 → `DecisionParams`."""
    return params.DecisionParams(version=version, **{k: point[k] for k in PARAM_NAMES})


def chunks(games: int, workers: int) -> list[tuple[int, int]]:
    """판 [0, games)를 workers개 이하 묶음 (start, count)로 나눈다."""
    size = max(1, math.ceil(games / max(1, workers)))
    return [(s, min(size, games - s)) for s in range(0, games, size)]


def evaluate(points: list[dict[str, float]], preset_id: str, bot_name: str, stage_seed: int, games: int,
             pool: Optional[ProcessPoolExecutor], workers: int) -> list[dict]:
    """후보마다 같은 시드 판 games개를 돌려 승률·구간·위반 수를 낸다(후보 사이 공통 난수 비교)."""
    jobs, owner = [], []
    for idx, pt in enumerate(points):
        for s, c in chunks(games, workers if pool else 1):
            jobs.append((pt, preset_id, bot_name, stage_seed, s, c))
            owner.append(idx)
    results = pool.map(run_chunk, jobs) if pool else map(run_chunk, jobs)
    tallies = [Tally() for _ in points]
    for idx, t in zip(owner, results):
        tallies[idx].add(t)
    out = []
    for pt, t in zip(points, tallies):
        lo, hi = wilson(t.village_wins, t.games)
        out.append({"point": pt, "games": t.games, "village_wins": t.village_wins,
                    "win_rate": t.village_wins / t.games, "ci95": [round(lo, 4), round(hi, 4)],
                    "days_mean": t.days / t.games, "villager_votes": t.villager_votes,
                    "violations": t.violations, "fallback_votes": t.fallback_votes})
    return out


def base_point() -> dict[str, float]:
    """지금 `decision_params.json`(조정 전) 값."""
    d = params.DEFAULT.to_dict()
    return {k: d[k] for k in PARAM_NAMES}


def command_line(a: argparse.Namespace) -> str:
    """재현 명령."""
    s = (f"python -m tools.optimize_decision --target {a.target[0]} {a.target[1]} --preset {a.preset} --bot {a.bot} "
         f"--samples {a.samples} --top {a.top} --screen-games {a.screen_games} --confirm-games {a.confirm_games} "
         f"--final-games {a.final_games} --seed {a.seed} --workers {a.workers} --out {shlex.quote(a.out)}")
    if a.write_params:
        s += " --write-params"
    if a.params_out:
        s += f" --params-out {shlex.quote(a.params_out)}"
    if a.version:
        s += f" --version {a.version}"
    return s


def main(argv: Optional[Iterable[str]] = None) -> int:
    """명령행 진입점. 선별 → 확인 → 선택 → 최종 판정, 결과를 `--out`(와 결과 JSON·파라미터 파일)에 쓴다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", nargs=2, type=float, default=list(DEFAULT_TARGET), metavar=("LOW", "HIGH"),
                    help="목표 마을(대역 봇) 승률 구간. 기본 0.40 0.60")
    ap.add_argument("--preset", default="base6", choices=sorted(PRESETS))
    ap.add_argument("--bot", default="logic", choices=sorted(BOT_TYPES), help="플레이어 대역 봇(기본 논리 우선)")
    ap.add_argument("--samples", type=int, default=40)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--screen-games", type=int, default=200)
    ap.add_argument("--confirm-games", type=int, default=1000)
    ap.add_argument("--final-games", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--workers", type=int, default=1, help="병렬 프로세스 수(1이면 직렬)")
    ap.add_argument("--out", default="out/a0803")
    ap.add_argument("--version", help="채택 시 새 DECISION_PARAMS_VERSION(기본: 기존 번호 + 1)")
    ap.add_argument("--write-params", action="store_true", help="합격하면 engine/domain/decision_params.json을 덮어쓴다")
    ap.add_argument("--params-out", help="합격하면 이 경로에 파라미터 json을 쓴다(난이도별 파일 등, 기본 파일은 그대로). --version 필수")
    ap.add_argument("--record", metavar="TASK_ID", help="결과 JSON을 docs/evidence/perf/<TASK_ID>/optimize_decision.json에")
    a = ap.parse_args(argv)
    lo, hi = a.target
    if not 0.0 <= lo < hi <= 1.0:
        ap.error("--target은 0 ≤ LOW < HIGH ≤ 1")
    for name in ("samples", "top", "screen_games", "confirm_games", "final_games", "workers"):
        if getattr(a, name) < 1:
            ap.error(f"--{name.replace('_', '-')}는 1 이상")
    if a.params_out and not a.version:
        ap.error("--params-out에는 --version이 필요하다(파일마다 다른 버전이어야 판 메타로 값을 구분한다)")
    if not 0 <= a.seed < 100_000:
        ap.error("--seed는 0 이상 100000 미만")
    target = (lo, hi)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = command_line(a)
    base = base_point()
    log(COMPONENT, "info", "탐색 시작", target=list(target), preset=a.preset, bot=a.bot, workers=a.workers,
        base_version=params.DEFAULT.version, rule_base=params.RULE_BASE_VERSION)
    t0 = time.perf_counter()
    pool = ProcessPoolExecutor(a.workers) if a.workers > 1 else None
    try:
        points = sample_grid(SPACE, a.samples, random.Random(a.seed))
        if base not in points:
            points.insert(0, base)
        screen = evaluate(points, a.preset, a.bot, a.seed * 10 + 1, a.screen_games, pool, a.workers)
        t_screen = time.perf_counter() - t0
        log(COMPONENT, "info", "선별 완료", candidates=len(screen), duration_ms=round(t_screen * 1000))
        top = sorted(screen, key=lambda c: rank_key(c["win_rate"], target, c["point"], base, SPACE))[:a.top]
        confirmed = evaluate([c["point"] for c in top], a.preset, a.bot, a.seed * 10 + 2, a.confirm_games,
                             pool, a.workers)
        t_confirm = time.perf_counter() - t0
        log(COMPONENT, "info", "확인 완료", candidates=len(confirmed), duration_ms=round(t_confirm * 1000))
        chosen = choose(confirmed, target, base, SPACE)
        final_point = chosen["point"] if chosen else base
        final = evaluate([final_point], a.preset, a.bot, a.seed * 10 + 3, a.final_games, pool, a.workers)[0]
    finally:
        if pool:
            pool.shutdown()
    elapsed = time.perf_counter() - t0
    passed = chosen is not None and in_target(final["win_rate"], target) and final["violations"] == 0
    old = params.DEFAULT.version
    new_version = a.version or (f"decision-{int(old.rsplit('-', 1)[1]) + 1}" if old.rsplit("-", 1)[-1].isdigit()
                                else f"{old}-1")
    summary = {
        "command": cmd, "target": list(target), "preset": a.preset, "bot": BOT_TYPES[a.bot].version,
        "npc_policy": RuleV0Policy.version, "suspicion_model": FakeSuspicionModel.model_version,
        "rule_base_version": params.RULE_BASE_VERSION, "base_version": old, "space": {k: list(v) for k, v in SPACE.items()},
        "seeds": {"screen": a.seed * 10 + 1, "confirm": a.seed * 10 + 2, "final": a.seed * 10 + 3},
        "elapsed_s": round(elapsed, 1), "screen_s": round(t_screen, 1), "confirm_s": round(t_confirm - t_screen, 1),
        "final_s": round(elapsed - t_confirm, 1), "workers": a.workers,
        "status": "통과" if passed else "실패", "chosen": final_point if chosen else None,
        "new_version": new_version if passed else None, "final": final,
        "confirm": confirmed, "screen": screen,
    }
    (out / "search.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                                     newline="\n")
    if passed:
        doc = {"version": new_version,
               "note": f"A-08-03 탐색 결과(목표 마을 승률 {lo:.2f}~{hi:.2f}, {a.preset}, {BOT_TYPES[a.bot].version}). "
                       f"재현: {cmd}"}
        doc.update(final_point)
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        if a.write_params:
            params.DECISION_PARAMS_FILE.write_text(text, encoding="utf-8", newline="\n")
        if a.params_out:
            Path(a.params_out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.params_out).write_text(text, encoding="utf-8", newline="\n")
    log(COMPONENT, "info" if passed else "warning", "탐색 완료", status=summary["status"],
        win_rate=round(final["win_rate"], 4), ci95=final["ci95"], violations=final["violations"],
        duration_ms=round(elapsed * 1000), chosen=final_point)
    if a.record:
        metrics = {
            "final_games": {"value": final["games"], "unit": "판"},
            "final_village_win_rate": {"value": round(100 * final["win_rate"], 2), "unit": "%"},
            "final_ci95_low": {"value": round(100 * final["ci95"][0], 2), "unit": "%"},
            "final_ci95_high": {"value": round(100 * final["ci95"][1], 2), "unit": "%"},
            "final_inference_violations": {"value": final["violations"], "unit": "건"},
            "final_villager_votes": {"value": final["villager_votes"], "unit": "표"},
            "final_fallback_votes": {"value": final["fallback_votes"], "unit": "표"},
            "final_days_mean": {"value": round(final["days_mean"], 3), "unit": "낮"},
            "screen_candidates": {"value": len(screen), "unit": "개"},
            "confirm_candidates": {"value": len(confirmed), "unit": "개"},
            "search_elapsed": {"value": round(elapsed, 1), "unit": "s"},
            "workers": {"value": a.workers, "unit": "개"},
        }
        path = write_result(a.record, "optimize_decision", metrics, cmd)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
