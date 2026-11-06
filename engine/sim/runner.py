"""GameEngine 포트로만 플레이어 봇 판을 실행한다. 진실 값은 엔진에 되넘기기만 한다."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from engine.domain.model import CommandEnvelope, CommandKind, DataSource, GameMeta
from engine.domain.serde import dumps
from engine.ports import EngineDeps, GameEngine
from engine.sim.bots import legal_commands


class SimulationError(RuntimeError):
    """명령 거부·실행 상한·잘못된 출처로 판 검증에 실패했다."""


@dataclass(frozen=True)
class GameRun:
    """시간을 제외한 판 결과. trace 해시는 명령·전이 이벤트의 재현성을 비교한다."""
    game_id: str
    policy: str
    seed: int
    winner: str
    days: int
    steps: int
    illegal_commands: int
    meaningful_choices: int
    vote_days: int
    no_lock_days: int
    confessions: int
    confession_opportunities: int
    trace_sha256: str


def run_game(engine: GameEngine, deps: EngineDeps, meta: GameMeta, bot, max_steps: int = 500) -> GameRun:
    """판을 종료까지 진행한다. 봇은 PlayerView만 받으며 거부된 명령을 재시도해 숨기지 않는다.

    의미 있는 선택 = advance 외의 서로 다른 후보 명령이 2개 이상인 지점에서
    플레이어가 실제 수락된 advance 외 명령을 낸 횟수다. 사람의 재미·판단 품질 지표는 아니다.
    """
    if meta.source is not DataSource.BOT or max_steps < 1:
        raise SimulationError("source=bot과 양의 명령 상한이 필요합니다")
    transition = engine.new_game(meta, deps)
    if not transition.accepted or transition.state is None or transition.truth is None:
        raise SimulationError("판 생성 실패")
    state, truth = transition.state, transition.truth
    trace = hashlib.sha256(dumps(transition.events).encode())
    rng = random.Random(meta.seed)
    steps = meaningful = 0
    while True:
        view = engine.player_view(state, deps.content)
        if view.result is not None:
            if not view.result.completed:
                raise SimulationError("중단 판은 완료 판으로 세지 않습니다")
            return GameRun(meta.game_id, bot.version, meta.seed, view.result.winner.value,
                view.result.day, steps, 0, meaningful, len(view.public.locks),
                sum(lock.locked is None for lock in view.public.locks),
                len({c.npc_id for c in view.public.confessions}), meta.preset.n_secret, trace.hexdigest())
        if steps >= max_steps:
            raise SimulationError(f"명령 상한 초과: {meta.game_id}, {max_steps}")
        choices = legal_commands(view)
        command = bot.choose(view, rng)
        env = CommandEnvelope(meta.game_id, f"c{view.state_version:05d}", view.state_version, command)
        transition = engine.apply(state, truth, env, deps)
        if not transition.accepted:
            raise SimulationError(f"합법 위반: {meta.game_id}, {command.kind.value}, {transition.reject}")
        actionable = {dumps(c) for c in choices if c.kind is not CommandKind.ADVANCE}
        meaningful += int(command.kind is not CommandKind.ADVANCE and len(actionable) >= 2)
        trace.update(dumps(command).encode())
        trace.update(dumps(transition.events).encode())
        state, truth = transition.state, transition.truth
        steps += 1
