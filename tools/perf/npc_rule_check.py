"""NPC 규칙 v0 검사·측정(A-07-01 조건 5·6). 결과 JSON은 `write_result`로 쓴다.

    python -m tools.perf.npc_rule_check --events out/a0701/events.jsonl --record A-07-01
    python -m tools.perf.npc_rule_check --bench --preset base6 --games 200 --seed 2 --record A-07-01

- `--events`: `tools.run_engine_games`가 쓴 `events.jsonl`을 읽어, 결정(`decision`)마다 같은 snapshot_id의 관측
  스냅샷과 맞춰 센다. 합법 행동 위반(관측의 합법 종류 밖, 자기 투표, 자기 대상 의심·감싸기, 씐 자 아닌 고백 판정은
  엔진이 막음)과 마을 추론 위반(마을 주민 표가 그 스냅샷의 확정 명제 `not_possessed`와 충돌, `npc.beliefs.vote_conflicts`).
  안전 층 대체 표(`fallback_votes`)와 의심 모델 폴백 결정(`suspicion_fallbacks`, policy_version `-fallback`, A-07-02)도
  센다. 결과 이름 `npc_checks`.
- `--bench`: 엔진으로 판을 돌리며 NPC 1명 결정 시간(정책의 믿음 갱신 + 행동 선택, 관측 투영 제외)을 잰다.
  의심 모델 투표(`vote_with_model`, A-07-02)는 모델 호출 시간을 포함한다. 플레이어 명령은 `engine_bench.bench_player_command`, 판 시드는 `seed * 1000000 + i`. 결과 이름 `npc_decision_time`.

계층: 표현·응용(인자 파싱, 파일 읽기, 집계, 출력). 판정 규칙은 `engine/domain/npc/`에 있다.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Optional

from engine.domain import params, serde
from engine.domain.engine import PaldoEngine
from engine.domain.model import (
    PLAYER_ID, PRESETS, CommandEnvelope, DataSource, GameMeta, ObservationSnapshot, Role,
)
from engine.domain.npc import RuleV0Policy
from engine.domain.npc.beliefs import vote_conflicts
from engine.domain.npc.vote import is_fallback_version
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.evidence_result import write_result
from tools.jsonlog import log
from tools.perf.engine_bench import bench_player_command, percentile

COMPONENT = "npc_rule_check"
TASK = "A-07-01"
SEED_STRIDE = 1_000_000
TALK_KINDS = {"suspect", "defend", "point_contradiction", "explain", "silence"}


# ---------------------------------------------------------------- 이벤트 검사(조건 5)
def legal_problem(snap: ObservationSnapshot, kind: str, target, actor: str) -> Optional[str]:
    """결정 1건의 합법 행동 위반 설명(없으면 None). 관측 스냅샷의 합법 종류·후보 기준."""
    legal = {k.value for k in snap.legal_kinds}
    if kind in TALK_KINDS or kind in ("vote", "night_plan"):
        if kind not in legal:
            return f"합법 종류 밖: {kind}"
    if kind == "vote" and target == actor:
        return "자기 투표"
    if kind in ("suspect", "defend") and target == actor:
        return f"자기 대상 {kind}"
    if kind == "night_plan" and snap.private.role is not Role.POSSESSED:
        return "씐 자 아닌 밤 명령"
    if kind == "react" and target == "confess" and snap.private.role is Role.POSSESSED:
        return "씐 자 고백"
    return None


def check_events(path: Path) -> dict:
    """events.jsonl을 읽어 결정 수·합법 위반·마을 추론 위반을 센다. 위반 예시는 앞 5건만 싣는다."""
    snaps: dict[str, ObservationSnapshot] = {}
    games: set[str] = set()
    out = {"decisions": 0, "villager_votes": 0, "legal_violations": 0, "inference_violations": 0,
           "fallback_votes": 0, "suspicion_fallbacks": 0, "confessions": 0, "examples": []}
    with path.open(encoding="utf-8") as f:
        for line in f:
            env = json.loads(line)
            et, p = env["event_type"], env["payload"]
            games.add(env["game_id"])
            if et == "observation":
                snaps[p["snapshot_id"]] = serde.from_jsonable(ObservationSnapshot, p["snapshot"])
            elif et == "confession":
                out["confessions"] += 1
            elif et == "decision":
                out["decisions"] += 1
                if is_fallback_version(str(p.get("policy_version", ""))):
                    out["suspicion_fallbacks"] += 1
                snap = snaps.pop(p["snapshot_id"])
                bad = legal_problem(snap, p["kind"], p["target"], p["actor"])
                if bad:
                    out["legal_violations"] += 1
                    _example(out, env, bad)
                if p["kind"] == "vote" and snap.private.role is Role.VILLAGER and p["actor"] != PLAYER_ID:
                    out["villager_votes"] += 1
                    if {"fallback_all_legal", "fallback_max_suspicion"} & set(p["reason_codes"]):
                        out["fallback_votes"] += 1
                    if vote_conflicts(snap.beliefs, p["target"]):
                        out["inference_violations"] += 1
                        _example(out, env, f"확정 명제와 충돌하는 표: {p['actor']} -> {p['target']}")
    out["games"] = len(games)
    return out


def _example(out: dict, env: dict, why: str) -> None:
    if len(out["examples"]) < 5:
        out["examples"].append({"game_id": env["game_id"], "seq": env["seq"], "why": why})


# ---------------------------------------------------------------- 결정 시간(조건 6)
class TimedPolicy:
    """`RuleV0Policy`를 감싸 NPC 결정 1회(믿음 갱신 + 행동 선택) 시간을 ms로 모은다."""

    def __init__(self, inner: RuleV0Policy):
        self.inner = inner
        self.version = inner.version
        self.samples: list[float] = []
        self._pending = 0.0

    def update_beliefs(self, obs):
        """믿음 갱신 시간을 다음 행동 선택 시간에 더한다."""
        t0 = time.perf_counter()
        out = self.inner.update_beliefs(obs)
        self._pending = time.perf_counter() - t0
        return out

    def _timed(self, fn, *a):
        t0 = time.perf_counter()
        out = fn(*a)
        self.samples.append((self._pending + time.perf_counter() - t0) * 1000)
        self._pending = 0.0
        return out

    def talk(self, obs, rng):
        """회의 말 행동(시간 측정)."""
        return self._timed(self.inner.talk, obs, rng)

    def vote(self, obs, candidates, rng):
        """투표(시간 측정)."""
        return self._timed(self.inner.vote, obs, candidates, rng)

    def vote_with_model(self, obs, candidates, rng, model, clock=None):
        """의심 모델 투표 3층(A-07-02, 시간 측정. 모델 호출 시간 포함)."""
        return self._timed(self.inner.vote_with_model, obs, candidates, rng, model, clock)

    def night_plan(self, obs, executors, victims, rng):
        """밤 명령(시간 측정)."""
        return self._timed(self.inner.night_plan, obs, executors, victims, rng)

    def react(self, obs, cmd, rng):
        """캐묻기 답(시간 측정)."""
        return self._timed(self.inner.react, obs, cmd, rng)


def bench(preset_id: str, games: int, seed: int) -> list[float]:
    """판 `games`개를 돌려 NPC 결정 시간 표본(ms)을 돌려준다. 콘텐츠는 `fixtures/content_min`."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    pol = TimedPolicy(RuleV0Policy(content))
    engine = PaldoEngine(pol)
    for i in range(games):
        gseed = seed * SEED_STRIDE + i
        meta = GameMeta(f"npcbench_{preset_id}_{seed}_{i:05d}", PRESETS[preset_id], gseed, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "bench", content.content_version)
        r = engine.new_game(meta, deps)
        st, tr, rng = r.state, r.truth, random.Random(gseed)
        while st.result is None:
            cmd = bench_player_command(engine.player_view(st, content), rng)
            r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version:04d}", st.state_version, cmd), deps)
            if not r.accepted:
                raise RuntimeError(f"측정 명령 거부: {meta.game_id} {cmd.kind.value} {r.reject}")
            st, tr = r.state, r.truth
    return pol.samples


# ---------------------------------------------------------------- 진입점
def main(argv=None) -> int:
    """명령행 진입점."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--events", help="tools.run_engine_games의 events.jsonl")
    mode.add_argument("--bench", action="store_true", help="NPC 결정 시간 측정")
    ap.add_argument("--preset", default="base6", choices=sorted(PRESETS))
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed", type=int, default=2)
    ap.add_argument("--record", metavar="TASK_ID", help="결과 JSON을 docs/evidence/perf/<TASK_ID>/에 쓴다")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    if a.events:
        cmd = f"python -m tools.perf.npc_rule_check --events {a.events}"
        log(COMPONENT, "info", "검사 시작", events=a.events)
        r = check_events(Path(a.events))
        metrics = {k: {"value": r[k], "unit": u} for k, u in (
            ("games", "판"), ("decisions", "건"), ("villager_votes", "건"), ("legal_violations", "건"),
            ("inference_violations", "건"), ("fallback_votes", "건"), ("suspicion_fallbacks", "건"),
            ("confessions", "건"))}
        name = "npc_checks"
        log(COMPONENT, "info", "검사 완료", duration_ms=round((time.perf_counter() - t0) * 1000, 3),
            examples=r["examples"], **{k: v["value"] for k, v in metrics.items()})
    else:
        if a.games < 1:
            ap.error("--games는 1 이상")
        cmd = f"python -m tools.perf.npc_rule_check --bench --preset {a.preset} --games {a.games} --seed {a.seed}"
        log(COMPONENT, "info", "측정 시작", preset=a.preset, games=a.games, seed=a.seed)
        s = bench(a.preset, a.games, a.seed)
        metrics = {
            "decisions": {"value": len(s), "unit": "건"},
            "decision_p50": {"value": round(percentile(s, 50), 3), "unit": "ms"},
            "decision_p95": {"value": round(percentile(s, 95), 3), "unit": "ms"},
            "decision_max": {"value": round(max(s), 3), "unit": "ms"},
        }
        name = f"npc_decision_time_{a.preset}"
        log(COMPONENT, "info", "측정 완료", duration_ms=round((time.perf_counter() - t0) * 1000, 3),
            **{k: v["value"] for k, v in metrics.items()})
    if a.record:
        path = write_result(a.record, name, metrics, cmd)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    print(json.dumps({k: v["value"] for k, v in metrics.items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
