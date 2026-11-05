"""엔진 연산 1회 시간 측정(`--op new_game|apply --n N`). 결과 JSON은 `write_result`로 쓴다.

    python -m tools.perf.engine_bench --op new_game --n 1000
    python -m tools.perf.engine_bench --op apply --n 1000

`apply`는 시드 0부터 판을 차례로 끝까지 진행하며 `apply` 호출 N회를 잰다. 플레이어 명령은 판 시드로 고정한
단순 선택(캐묻기 1회·의심·무작위 투표·점)이고, NPC는 엔진 기본 규칙이다. 판 생성 시간은 빼고 잰다.

계층: 표현·응용(인자 파싱, 판 조립, 시간 측정, 출력). 규칙은 `engine/domain/`에 있다.
시드는 0부터 N-1까지 고정이라 같은 명령으로 다시 돌릴 수 있다. 콘텐츠는 `fixtures/content_min`.
"""
from __future__ import annotations

import argparse
import math
import random
import time

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import (
    PLAYER_ID, PRESETS, Command, CommandEnvelope, CommandKind, DataSource, GameMeta, InterrogationKind, Phase, Slot,
)
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.evidence_result import write_result
from tools.jsonlog import log

COMPONENT = "engine_bench"
TASK_OF_OP = {"new_game": "A-05-01", "apply": "A-05-02"}


def percentile(values: list[float], q: float) -> float:
    """최근접 순위 방식 백분위수(q는 0~100). 값이 없으면 ValueError."""
    if not values:
        raise ValueError("값이 없음")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def bench_new_game(n: int, preset_id: str) -> list[float]:
    """시드 0..n-1로 판을 n번 만들고 1회 시간(ms) 목록을 돌려준다. 콘텐츠 로드는 측정에서 뺀다."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine()
    preset = PRESETS[preset_id]
    out = []
    for seed in range(n):
        meta = GameMeta(f"bench_{preset_id}_{seed:05d}", preset, seed, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "bench", content.content_version)
        t0 = time.perf_counter()
        engine.new_game(meta, deps)
        out.append((time.perf_counter() - t0) * 1000)
    return out


def bench_player_command(view, rng: random.Random) -> Command:
    """측정용 플레이어 명령(응용 계층의 고정 선택. 규칙 판단은 엔진이 한다)."""
    npcs = sorted(n for n in view.active if n != PLAYER_ID)
    legal = view.legal_kinds
    if view.phase is Phase.INTERROGATION and CommandKind.INTERROGATE in legal and view.player.interrogations_left == 2:
        return Command(CommandKind.INTERROGATE, PLAYER_ID, addressee=rng.choice(npcs),
                       interrogation_kind=InterrogationKind.WHEREABOUTS, night=0, slot=Slot.MIDNIGHT)
    if CommandKind.SUSPECT in legal:
        return Command(CommandKind.SUSPECT, PLAYER_ID, target=rng.choice(npcs))
    if CommandKind.VOTE in legal:
        return Command(CommandKind.VOTE, PLAYER_ID, target=rng.choice([c for c in sorted(view.runoff_candidates) if c != PLAYER_ID] or npcs))
    if CommandKind.DIVINE in legal:
        return Command(CommandKind.DIVINE, PLAYER_ID, target=rng.choice(npcs))
    return Command(CommandKind.ADVANCE, PLAYER_ID)


def bench_apply(n: int, preset_id: str) -> tuple[list[float], int]:
    """`apply` 호출 n회의 1회 시간(ms) 목록과 사용한 판 수. 시드 0부터 판을 끝까지 진행한다."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine()
    preset = PRESETS[preset_id]
    out: list[float] = []
    seed = 0
    while len(out) < n:
        meta = GameMeta(f"bench_{preset_id}_{seed:05d}", preset, seed, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "bench", content.content_version)
        r = engine.new_game(meta, deps)
        st, tr, rng = r.state, r.truth, random.Random(seed)
        while st.result is None and len(out) < n:
            cmd = bench_player_command(engine.player_view(st, content), rng)
            env = CommandEnvelope(meta.game_id, f"c{st.state_version:04d}", st.state_version, cmd)
            t0 = time.perf_counter()
            r = engine.apply(st, tr, env, deps)
            out.append((time.perf_counter() - t0) * 1000)
            if not r.accepted:
                raise RuntimeError(f"측정 명령 거부: {seed} {cmd.kind.value} {r.reject} {r.reject_detail}")
            st, tr = r.state, r.truth
        seed += 1
    return out, seed


def main(argv=None) -> int:
    """명령행 진입점. 측정값을 로그로 남기고 결과 JSON을 쓴다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--op", choices=sorted(TASK_OF_OP), required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--preset", default="base6", choices=sorted(PRESETS))
    ap.add_argument("--no-write", action="store_true", help="결과 JSON을 쓰지 않는다(시험 실행)")
    a = ap.parse_args(argv)
    if a.n <= 0:
        ap.error("--n은 1 이상")
    log(COMPONENT, "info", "측정 시작", op=a.op, n=a.n, preset=a.preset,
        rule_version=params.RULE_PARAMS_VERSION)
    if a.op == "apply":
        times, games = bench_apply(a.n, a.preset)
        count = {"calls": {"value": len(times), "unit": "회"}, "games": {"value": games, "unit": "판"}}
    else:
        times = bench_new_game(a.n, a.preset)
        count = {"games": {"value": len(times), "unit": "판"}}
    metrics = {
        f"{a.op}_p50": {"value": round(percentile(times, 50), 3), "unit": "ms"},
        f"{a.op}_p95": {"value": round(percentile(times, 95), 3), "unit": "ms"},
        f"{a.op}_max": {"value": round(max(times), 3), "unit": "ms"},
        **count,
    }
    log(COMPONENT, "info", "측정 완료", op=a.op, n=a.n, preset=a.preset,
        duration_ms=round(sum(times), 3), **{k: v["value"] for k, v in metrics.items()})
    if not a.no_write:
        source = f"python -m tools.perf.engine_bench --op {a.op} --n {a.n}"
        if a.preset != "base6":
            source += f" --preset {a.preset}"
        path = write_result(TASK_OF_OP[a.op], f"engine_{a.op}", metrics, source)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
