"""실제 엔진으로 판을 끝까지 돌려 이벤트를 jsonl로 쓴다(D·C 인수 패키지, A-06-03).

    python -m tools.run_engine_games --preset base6 --games 200 --seed 1 --out out/handoff/a06
    python -m tools.run_engine_games --preset base6 --games 200 --seed 1 --out out/handoff/a06 --record A-06-03

출력(`--out`):
- `events.jsonl`: `EventEnvelope` 한 줄씩. 판 순서대로, 판 안에서는 `seq` 순서.
- `manifest.json`: 명령·판 수·이벤트 수·정책·버전(검사 도구 `tools.check_handoff_a06`이 읽는다).

판 i(0부터)의 시드는 `seed * 1000000 + i`, 판 ID는 `eng_<preset>_<seed>_<i:05d>`다. 무당(플레이어) 명령은
판 시드로 고정한 단순 선택(`tools.perf.engine_bench.bench_player_command`)이고, NPC는 `--policy`로 고른 규칙이다.
`--policy min-legal`은 엔진 기본 `BaselinePolicy`(정책 버전 `baseline-0`)다(11/6 PM 결정).
`--policy rule-v0`은 NPC 규칙 v0(`engine.domain.npc.RuleV0Policy`, 정책 버전 `rule-v0`, A-07-01)다. 판 메타·봉투의
`npc_policy_versions`는 판 생성 직후 주민 전원(실종자 포함)을 그 버전으로 채운다.
`occurred_at`은 고정 기준 시각 + (판 순번 분, 이벤트 순번 밀리초)라 같은 명령이면 같은 바이트가 나온다.
처리량(판/초)과 판당 이벤트 수는 로그로 남기고, `--record <태스크 ID>`면 결과 JSON에도 쓴다.

계층: 표현·응용(인자 파싱, 판 조립, 봉투 만들기, 출력). jsonl 쓰기(`JsonlEventSink`)는 `EventSink` 포트의 파일 구현이다.
규칙은 `engine/domain/`에 있다.
"""
from __future__ import annotations

import argparse
import json
import random
import shlex
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from engine.domain import params, serde
from engine.domain.content import ContentBundle
from engine.domain.engine import BaselinePolicy, NpcPolicy, PaldoEngine
from engine.domain.events import DomainEvent, EventEnvelope, topic_of
from engine.domain.npc import RuleV0Policy
from engine.domain.model import ENGINE_VERSION as MODEL_ENGINE_VERSION
from engine.domain.model import CONTRACT_VERSION, PRESETS, CommandEnvelope, DataSource, GameMeta, GameState
from engine.ports import EngineDeps, EventSink
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.evidence_result import write_result
from tools.jsonlog import log
from tools.perf.engine_bench import bench_player_command

COMPONENT = "run_engine_games"
ENGINE_VERSION = MODEL_ENGINE_VERSION              # 봉투 engine_version(단일 출처 engine.domain.model, E-08-14)
EVENTS_FILE = "events.jsonl"
MANIFEST_FILE = "manifest.json"
SEED_STRIDE = 1_000_000                            # 판 시드 = seed * SEED_STRIDE + i
BASE_TIME = datetime(2026, 11, 6, tzinfo=timezone.utc)
MAX_STEPS = 500                                    # 판 하나의 플레이어 명령 상한(끝나지 않는 판 방지)

# 정책 선택지. rule-v0은 NPC 규칙 v0(A-07-01, 정책 버전 rule-v0). 성향·동선은 판과 같은 콘텐츠(fixtures/content_min)에서 찾는다.
POLICIES: dict[str, Callable[[], NpcPolicy]] = {
    "min-legal": BaselinePolicy,
    "rule-v0": lambda: RuleV0Policy(FixtureContentSource().load("*")),
}


class JsonlEventSink:
    """`EventSink` 포트의 파일 구현. 봉투를 한 줄씩 JSON으로 덧붙인다(시뮬레이터·배치 전용 경로)."""

    def __init__(self, path: Path):
        self.path = path
        self.count = 0
        self._f = open(path, "w", encoding="utf-8", newline="\n")

    def append(self, envelopes: list[EventEnvelope]) -> None:
        """봉투 목록을 순서대로 쓴다."""
        for e in envelopes:
            self._f.write(serde.dumps(e) + "\n")
        self.count += len(envelopes)

    def close(self) -> None:
        """파일을 닫는다."""
        self._f.close()


@dataclass
class GameRun:
    """판 하나의 실행 결과 요약."""
    game_id: str
    seed: int
    events: int
    steps: int
    winner: Optional[str]


def game_seed(seed: int, i: int) -> int:
    """판 i의 시드. 실행 시드가 다르면 판 시드가 겹치지 않는다(i < SEED_STRIDE)."""
    return seed * SEED_STRIDE + i


def occurred_at(game_index: int, seq: int) -> str:
    """재생성 때 같은 값이 나오는 고정 시각(ISO 8601 UTC, 밀리초)."""
    t = BASE_TIME + timedelta(minutes=game_index, milliseconds=seq)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class EnvelopeBuilder:
    """판 하나의 `DomainEvent`에 판 메타·ID·순번을 붙인다. `seq`는 판 안에서 0부터 빈틈 없이 매긴다."""

    def __init__(self, meta: GameMeta, game_index: int):
        self.meta = meta
        self.game_index = game_index
        self.seq = 0

    def wrap(self, events: list[DomainEvent], state_version: int, command_id: Optional[str]) -> list[EventEnvelope]:
        """한 전이의 이벤트 목록을 봉투 목록으로 바꾼다."""
        m = self.meta
        out = []
        for e in events:
            out.append(EventEnvelope(
                event_id=f"{m.game_id}:{self.seq:05d}", game_id=m.game_id, seq=self.seq,
                state_version=state_version, event_type=e.event_type.value, topic=topic_of(e.event_type),
                occurred_at=occurred_at(self.game_index, self.seq), day=e.day, phase=e.phase.value,
                source=m.source, contract_version=CONTRACT_VERSION, preset_id=m.preset.preset_id, seed=m.seed,
                rule_version=m.rule_version, engine_version=m.engine_version, content_version=m.content_version,
                npc_policy_versions=dict(m.npc_policy_versions), model_versions=dict(m.model_versions),
                payload=e.payload, command_id=command_id))
            self.seq += 1
        return out


def with_policy_versions(state: GameState, version: str) -> GameState:
    """판 메타의 `npc_policy_versions`를 주민 전원(실종자 포함) {npc: version}으로 채운 상태를 돌려준다.

    엔진의 `decision.policy_version`도 이 값을 쓴다. 입력 상태는 바꾸지 않는다.
    """
    residents = sorted(set(state.npcs) | set(state.missing))
    meta = replace(state.meta, npc_policy_versions={n: version for n in residents})
    return replace(state, meta=meta)


def run_game(engine: PaldoEngine, deps: EngineDeps, preset_id: str, seed: int, i: int, policy_version: str,
             sink: EventSink, *, model_version: str = FakeSuspicionModel.model_version,
             player_bot=None) -> GameRun:
    """판 하나를 끝까지 진행하며 전이마다 봉투를 `sink`에 넘긴다. 명령 거부·상한 초과는 RuntimeError."""
    content: ContentBundle = deps.content
    gseed = game_seed(seed, i)
    meta = GameMeta(f"eng_{preset_id}_{seed}_{i:05d}", PRESETS[preset_id], gseed, DataSource.BOT,
                    params.RULE_PARAMS_VERSION, ENGINE_VERSION, content.content_version,
                    model_versions={"suspicion": model_version})
    if player_bot is not None:
        meta.model_versions["player_bot"] = player_bot.version
    r = engine.new_game(meta, deps)
    st = with_policy_versions(r.state, policy_version)
    tr = r.truth
    builder = EnvelopeBuilder(st.meta, i)
    sink.append(builder.wrap(r.events, st.state_version, None))
    rng = random.Random(gseed)
    steps = 0
    while st.result is None:
        if steps >= MAX_STEPS:
            raise RuntimeError(f"판이 {MAX_STEPS}명령 안에 끝나지 않음: {meta.game_id}")
        view = engine.player_view(st, content)
        cmd = bench_player_command(view, rng) if player_bot is None else player_bot.choose(view, rng)
        cid = f"c{st.state_version:04d}"
        r = engine.apply(st, tr, CommandEnvelope(meta.game_id, cid, st.state_version, cmd), deps)
        if not r.accepted:
            raise RuntimeError(f"플레이어 명령 거부: {meta.game_id} {cmd.kind.value} {r.reject} {r.reject_detail}")
        st, tr = r.state, r.truth
        sink.append(builder.wrap(r.events, st.state_version, cid))
        steps += 1
    return GameRun(meta.game_id, gseed, builder.seq, steps, st.result.winner.value)


def run_games(preset_id: str, games: int, seed: int, policy: str, sink: EventSink) -> list[GameRun]:
    """판 `games`개를 차례로 돌린다. 콘텐츠는 `fixtures/content_min`, 의심 모델은 가짜(규칙 의심도)."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    pol = POLICIES[policy]()
    engine = PaldoEngine(pol)
    version = getattr(pol, "version", policy)
    return [run_game(engine, deps, preset_id, seed, i, version, sink) for i in range(games)]


def command_line(a: argparse.Namespace) -> str:
    """재생성 명령(`--record`·`--quiet` 제외)."""
    s = f"python -m tools.run_engine_games --preset {a.preset} --games {a.games} --seed {a.seed} --out {shlex.quote(a.out)}"
    if a.policy != "min-legal":
        s += f" --policy {a.policy}"
    return s


def summarize(runs: list[GameRun], elapsed_s: float) -> dict:
    """처리량과 판당 이벤트 수 지표(결과 JSON `metrics` 형식)."""
    ev = [r.events for r in runs]
    total = sum(ev)
    return {
        "games": {"value": len(runs), "unit": "판"},
        "events": {"value": total, "unit": "개"},
        "elapsed": {"value": round(elapsed_s, 3), "unit": "s"},
        "games_per_sec": {"value": round(len(runs) / elapsed_s, 3), "unit": "판/초"},
        "events_per_game_mean": {"value": round(total / len(runs), 3), "unit": "개"},
        "events_per_game_min": {"value": min(ev), "unit": "개"},
        "events_per_game_max": {"value": max(ev), "unit": "개"},
    }


def main(argv=None) -> int:
    """명령행 진입점. 판을 돌려 jsonl·manifest를 쓰고, 처리량을 로그(와 결과 JSON)로 남긴다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--preset", default="base6", choices=sorted(PRESETS))
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="out/handoff/a06")
    ap.add_argument("--policy", default="min-legal", choices=sorted(POLICIES))
    ap.add_argument("--record", metavar="TASK_ID", help="결과 JSON을 docs/evidence/perf/<TASK_ID>/engine_games.json에 쓴다")
    a = ap.parse_args(argv)
    if not 0 < a.games < SEED_STRIDE:
        ap.error(f"--games는 1 이상 {SEED_STRIDE} 미만")
    if a.seed < 0:
        ap.error("--seed는 0 이상")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cmd = command_line(a)
    log(COMPONENT, "info", "실행 시작", preset=a.preset, games=a.games, seed=a.seed, policy=a.policy,
        out=str(out), rule_version=params.RULE_PARAMS_VERSION)
    (out / MANIFEST_FILE).unlink(missing_ok=True)    # 실패한 재실행이 옛 manifest와 섞이지 않게(성공 뒤에만 쓴다)
    sink = JsonlEventSink(out / EVENTS_FILE)
    t0 = time.perf_counter()
    try:
        runs = run_games(a.preset, a.games, a.seed, a.policy, sink)
    except Exception as e:
        log(COMPONENT, "error", "실행 실패", error_type=type(e).__name__, error=str(e))
        raise
    finally:
        sink.close()
    elapsed = time.perf_counter() - t0
    metrics = summarize(runs, elapsed)
    winners: dict[str, int] = {}
    for r in runs:
        winners[r.winner] = winners.get(r.winner, 0) + 1
    manifest = {
        "command": cmd, "preset_id": a.preset, "games": len(runs), "seed": a.seed, "policy": a.policy,
        "npc_policy_version": getattr(POLICIES[a.policy](), "version", a.policy),
        "events": sink.count, "rule_version": params.RULE_PARAMS_VERSION, "engine_version": ENGINE_VERSION,
        "contract_version": CONTRACT_VERSION, "source": DataSource.BOT.value,
        "game_ids": [r.game_id for r in runs], "winners": dict(sorted(winners.items())),
    }
    (out / MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(COMPONENT, "info", "실행 완료", duration_ms=round(elapsed * 1000, 3), out=str(out),
        winners=manifest["winners"], **{k: v["value"] for k, v in metrics.items()})
    if a.record:
        path = write_result(a.record, "engine_games", metrics, cmd)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
