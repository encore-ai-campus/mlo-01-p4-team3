"""A-07-01 `tools.perf.npc_rule_check` 테스트: 이벤트 검사(합법·마을 추론 위반 집계)와 결정 시간 측정."""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from engine.domain import params, serde
from engine.domain.engine import PaldoEngine
from engine.domain.model import (
    PLAYER_ID, PRESETS, Beliefs, CommandEnvelope, CommandKind, DataSource, EvidenceSource, GameMeta,
    ObservationSnapshot, Phase, PrivateKnowledge, Proposition, PropositionKind, PublicRecord, Role, Slot, Place,
)
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.perf import npc_rule_check as tool
from tools.perf.engine_bench import bench_player_command

CONTENT = FixtureContentSource().load("*")


def _snap(role=Role.VILLAGER, legal=(CommandKind.VOTE,), props=()) -> ObservationSnapshot:
    return ObservationSnapshot("s1", "g", "a", 1, Phase.VOTE, 1, PublicRecord(), PrivateKnowledge("a", role),
                               Beliefs(propositions=list(props)), list(legal), ["b", "c"], 1, "base6")


def _env(game, seq, et, payload) -> dict:
    return {"game_id": game, "seq": seq, "event_type": et, "payload": payload}


class LegalProblem(unittest.TestCase):
    def test_cases(self):
        self.assertIsNone(tool.legal_problem(_snap(), "vote", "b", "a"))
        self.assertIn("자기", tool.legal_problem(_snap(), "vote", "a", "a"))
        self.assertIn("합법", tool.legal_problem(_snap(), "suspect", "b", "a"))
        self.assertIn("밤", tool.legal_problem(_snap(legal=(CommandKind.NIGHT_PLAN,)), "night_plan", {}, "a"))
        self.assertIn("고백", tool.legal_problem(_snap(Role.POSSESSED), "react", "confess", "a"))


class CheckEvents(unittest.TestCase):
    def write(self, rows) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / "events.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return p

    def test_counts_inference_violation(self):
        cleared = Proposition(PropositionKind.NOT_POSSESSED, "b", EvidenceSource.OWN_WITNESS, 0, Slot.MIDNIGHT, Place.WELL)
        s = serde.to_jsonable(_snap(props=[cleared]))
        rows = [_env("g", 0, "observation", {"snapshot_id": "s1", "snapshot": s}),
                _env("g", 1, "decision", {"snapshot_id": "s1", "actor": "a", "kind": "vote", "target": "b",
                                          "reason_codes": ["max_suspicion"]}),
                _env("g", 2, "observation", {"snapshot_id": "s2", "snapshot": s}),
                _env("g", 3, "decision", {"snapshot_id": "s2", "actor": "a", "kind": "vote", "target": "c",
                                          "reason_codes": ["max_suspicion"]})]
        r = tool.check_events(self.write(rows))
        self.assertEqual((r["decisions"], r["villager_votes"], r["inference_violations"], r["legal_violations"]),
                         (2, 2, 1, 0))

    def test_real_games_have_no_violations(self):
        """규칙 v0 판의 이벤트에서 합법·마을 추론 위반이 0건이다(봉투 형식을 흉내 낸 jsonl)."""
        engine = PaldoEngine(RuleV0Policy(CONTENT))
        deps = EngineDeps(CONTENT, FakeSuspicionModel())
        rows = []
        for seed in range(5):
            meta = GameMeta(f"g{seed}", PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION, "t",
                            CONTENT.content_version)
            r = engine.new_game(meta, deps)
            st, tr, rng, n = r.state, r.truth, random.Random(seed), 0
            while st.result is None:
                cmd = bench_player_command(engine.player_view(st, CONTENT), rng)
                r = engine.apply(st, tr, CommandEnvelope(meta.game_id, "c", st.state_version, cmd), deps)
                st, tr = r.state, r.truth
                for e in r.events:
                    rows.append(_env(meta.game_id, n, e.event_type.value, serde.to_jsonable(e.payload)))
                    n += 1
        res = tool.check_events(self.write(rows))
        self.assertEqual((res["games"], res["legal_violations"], res["inference_violations"]), (5, 0, 0))
        self.assertGreater(res["villager_votes"], 0)


class SuspicionFallbacks(unittest.TestCase):
    def test_counts_fallback_decisions(self):
        """policy_version이 `-fallback`인 결정(의심 모델 폴백, A-07-02)과 안전 층 대체 표를 센다."""
        s = serde.to_jsonable(_snap())
        rows = [_env("g", 0, "observation", {"snapshot_id": "s1", "snapshot": s}),
                _env("g", 1, "decision", {"snapshot_id": "s1", "actor": "a", "kind": "vote", "target": "b",
                                          "reason_codes": ["max_suspicion", "model_fallback_unavailable"],
                                          "policy_version": "rule-v0-fallback"}),
                _env("g", 2, "observation", {"snapshot_id": "s2", "snapshot": s}),
                _env("g", 3, "decision", {"snapshot_id": "s2", "actor": "a", "kind": "vote", "target": "c",
                                          "reason_codes": ["model_estimate", "fallback_max_suspicion"],
                                          "policy_version": "rule-v0"})]
        res = tool.check_events(CheckEvents.write(self, rows))
        self.assertEqual((res["suspicion_fallbacks"], res["fallback_votes"]), (1, 1))


class RunnerRuleV0(unittest.TestCase):
    def test_run_engine_games_rule_v0(self):
        """`tools.run_engine_games --policy rule-v0`로 판을 돌리면 정책 버전 rule-v0, 이벤트 검사 위반 0건."""
        from tools import run_engine_games
        out = Path(tempfile.mkdtemp())
        self.assertEqual(run_engine_games.main(["--preset", "base6", "--games", "3", "--seed", "2", "--out", str(out),
                                                "--policy", "rule-v0"]), 0)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["npc_policy_version"], "rule-v0")
        res = tool.check_events(out / "events.jsonl")
        self.assertEqual((res["games"], res["legal_violations"], res["inference_violations"]), (3, 0, 0))
        first = json.loads((out / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(first["npc_policy_versions"].values()), {"rule-v0"})


class Bench(unittest.TestCase):
    def test_bench_samples(self):
        s = tool.bench("short5", 2, 0)
        self.assertTrue(s)
        self.assertTrue(all(x >= 0 for x in s))


if __name__ == "__main__":
    unittest.main()
