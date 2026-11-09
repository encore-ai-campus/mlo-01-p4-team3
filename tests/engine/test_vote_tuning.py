"""A-09-06: 1일째 의심 문턱, 규칙 v0 무당 거름 이유 코드, NPC 발언 변형 돌려쓰기.

모델 경로 무당 문턱(`vote.MODEL_MUDANG_VOTE_TRUST`)은 `test_vote_layers.SafetyLayer`가 확인한다.
"""
from __future__ import annotations

import json
import random
import unittest
from unittest import mock

from engine.domain import params, serde
from engine.domain.engine import PaldoEngine, _Tx
from engine.domain.model import (
    ENGINE_VERSION, PLAYER_ID, PRESETS, CommandEnvelope, CommandKind, DataSource, GameMeta, PublicRecord,
)
from engine.domain.npc import RuleV0Policy
from engine.domain.npc import beliefs as bl
from engine.domain.npc import policy_villager as pv
from engine.domain.observation import ReasonCode
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tests.engine.test_npc import contra, snap, speech
from tools.perf.engine_bench import bench_player_command

TEXT_KEYS = {"line_text", "fact_sentence", "fallback"}


def _strip_text(value):
    if isinstance(value, dict):
        return {k: _strip_text(v) for k, v in value.items() if k not in TEXT_KEYS}
    if isinstance(value, list):
        return [_strip_text(v) for v in value]
    return value


def _play(seed: int):
    """base6 한 판을 벤치 플레이어로 끝까지 진행해 (마지막 상태, 전이 이벤트·상태 기록)을 돌려준다."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine(RuleV0Policy())
    meta = GameMeta(f"tune_{seed}", PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    ENGINE_VERSION, content.content_version,
                    model_versions={"suspicion": FakeSuspicionModel.model_version})
    r = engine.new_game(meta, deps)
    st, tr = r.state, r.truth
    log = [json.loads(serde.dumps(r.events))]
    rng = random.Random(seed)
    for _ in range(400):
        if st.result is not None:
            break
        cmd = bench_player_command(engine.player_view(st, content), rng)
        r = engine.apply(st, tr, CommandEnvelope(meta.game_id, f"c{st.state_version:04d}", st.state_version, cmd), deps)
        assert r.accepted, r.reject
        st, tr = r.state, r.truth
        log.append(json.loads(serde.dumps(r.events)))
        log.append(json.loads(serde.dumps(st)))
    assert st.result is not None
    return st, log


class SuspectThreshold(unittest.TestCase):
    def test_day1_threshold_lower(self):
        self.assertEqual(pv.suspect_min(1), pv.SUSPECT_MIN_DAY1)
        self.assertEqual(pv.suspect_min(2), pv.SUSPECT_MIN)
        self.assertLess(pv.SUSPECT_MIN_DAY1, pv.SUSPECT_MIN)


class RuleVoteMudangCode(unittest.TestCase):
    def test_rule_v0_vote_marks_trusted_mudang_skip(self):
        """규칙 v0(모델 실패 대체) 투표도 신뢰받는 무당을 뺄 때 같은 이유 코드를 남긴다."""
        s = snap(legal=[CommandKind.VOTE], candidates=("b",))
        d = pv.VillagerPolicy().vote(s, ["a", "b", PLAYER_ID], random.Random(0))
        self.assertNotEqual(d.value, PLAYER_ID)
        self.assertIn(ReasonCode.SKIP_TRUSTED_MUDANG, d.reason_codes)
        bad = [speech(f"sp{i:02d}", PLAYER_ID, CommandKind.POINT_CONTRADICTION, evidence=[contra(f"cl{i}", f"cx{i}")])
               for i in range(3)]
        low = snap(public=PublicRecord(speeches=bad), legal=[CommandKind.VOTE], candidates=("b",))
        self.assertLess(bl.compute_beliefs(low).trust_in_mudang, pv.MUDANG_VOTE_TRUST)
        d = pv.VillagerPolicy().vote(low, ["a", PLAYER_ID], random.Random(0))
        self.assertNotIn(ReasonCode.SKIP_TRUSTED_MUDANG, d.reason_codes)


class LineVariants(unittest.TestCase):
    def test_same_seed_same_lines(self):
        _, a = _play(3)
        _, b = _play(3)
        self.assertEqual(a, b)

    def test_rotation_does_not_change_decisions(self):
        """문장 고르기는 전용 난수 키라, 첫 변형만 쓸 때와 문장 말고는 모든 이벤트가 같다."""
        original = _Tx.template

        def first_only(self, kind, reaction=None, speaker=None, pick=None):
            return original(self, kind, reaction, speaker, None)

        for seed in (1, 2, 5):
            _, rotated = _play(seed)
            with mock.patch.object(_Tx, "template", first_only):
                _, first = _play(seed)
            self.assertEqual(_strip_text(rotated), _strip_text(first))

    def test_more_than_first_variant_used(self):
        content = FixtureContentSource().load("*")
        firsts = {t.variants[0] for t in content.templates}
        later = {v for t in content.templates for v in t.variants[1:]} - firsts
        lines = set()
        for seed in range(6):
            st, _ = _play(seed)
            lines |= {s.line_text for s in st.public.speeches if s.line_text}
        tails = {v.rsplit("}", 1)[-1] for v in later} - {v.rsplit("}", 1)[-1] for v in firsts} - {""}
        self.assertTrue(any(line.endswith(t) for line in lines for t in tails))


if __name__ == "__main__":
    unittest.main()
