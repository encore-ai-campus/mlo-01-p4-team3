"""A-08-03 결정 파라미터 파일(`decision_params.json`)과 적용 경로 테스트."""
from __future__ import annotations

import json
import random
import unittest
from collections import Counter

from engine.domain import params
from engine.domain.content import Character
from engine.domain.engine import _routine_place
from engine.domain.model import Place, Slot
from engine.domain.npc import vote as vt


def _params(**kw) -> params.DecisionParams:
    base = dict(version="t", conformity_scale=0.2, vote_noise=0.02, q_max=0.5, witness_prob=0.7, tell_prob=0.5,
                routine_randomness=0.0)
    base.update(kw)
    return params.DecisionParams(**base)


class DecisionParamsFile(unittest.TestCase):
    def setUp(self):
        self._saved = params.current()

    def tearDown(self):
        params.apply(self._saved)

    def test_file_loads_and_versions(self):
        raw = json.loads(params.DECISION_PARAMS_FILE.read_text(encoding="utf-8"))
        p = params.load()
        self.assertEqual(p.version, raw["version"])
        self.assertEqual(params.DECISION_PARAMS_VERSION, p.version)
        self.assertEqual(params.RULE_PARAMS_VERSION, f"{params.RULE_BASE_VERSION}.{p.version}")
        # 회차 등록 검증(server/app/test_sessions.py)과 같은 문자 집합이어야 운영자가 이 버전으로 회차를 등록할 수 있다
        self.assertRegex(params.RULE_PARAMS_VERSION, r"^[a-zA-Z0-9_.-]{1,80}$")
        self.assertEqual((params.WITNESS_PROB, params.TELL_PROB, params.Q_MAX),
                         (p.witness_prob, p.tell_prob, p.q_max))

    def test_rejects_version_with_unsafe_chars(self):
        raw = json.loads(params.DECISION_PARAMS_FILE.read_text(encoding="utf-8"))
        for bad in ("decision+1", "decision 1", ""):
            with self.assertRaises(ValueError):
                params.DecisionParams.from_dict({**raw, "version": bad})

    def test_rejects_out_of_range_and_missing(self):
        with self.assertRaises(ValueError):
            _params(q_max=1.5)
        with self.assertRaises(ValueError):
            _params(vote_noise=-0.1)
        with self.assertRaises(KeyError):
            params.DecisionParams.from_dict({"version": "x"})

    def test_apply_is_read_at_call_time(self):
        params.apply(_params(version="t1", q_max=0.8, vote_noise=0.0, conformity_scale=0.0))
        self.assertEqual(params.RULE_PARAMS_VERSION, f"{params.RULE_BASE_VERSION}.t1")
        self.assertAlmostEqual(vt.mudang_q(0), 0.8)
        scores = vt.decision_scores({"b": 0.3}, {"b": 1.0}, 1.0, random.Random(0))
        self.assertEqual(scores, {"b": 0.3})           # 잡음 0·동조 0
        params.apply(_params(conformity_scale=0.4, vote_noise=0.0))
        self.assertAlmostEqual(vt.decision_scores({"b": 0.3}, {"b": 1.0}, 1.0, random.Random(0))["b"], 0.7)


class RoutineRandomness(unittest.TestCase):
    def setUp(self):
        self._saved = params.current()
        self.ch = Character.__new__(Character)
        object.__setattr__(self.ch, "routine", {Slot.MIDNIGHT: {Place.HOME: 9.0, Place.TAVERN: 1.0}})

    def tearDown(self):
        params.apply(self._saved)

    def _draw(self, n=4000):
        rng = random.Random(1)
        return Counter(_routine_place(rng, self.ch, Slot.MIDNIGHT, home=False) for _ in range(n))   # 콘텐츠 가중치 그대로(rule-0.8 한밤 집 ×0.25는 test_rule08)

    def test_zero_keeps_weights_and_one_is_uniform(self):
        params.apply(_params(routine_randomness=0.0))
        c0 = self._draw()
        self.assertAlmostEqual(c0[Place.TAVERN] / 4000, 0.1, delta=0.02)
        params.apply(_params(routine_randomness=1.0))
        c1 = self._draw()
        self.assertAlmostEqual(c1[Place.TAVERN] / 4000, 0.5, delta=0.03)
        self.assertEqual(set(c1), {Place.HOME, Place.TAVERN})   # 후보 장소 밖으로는 가지 않는다


if __name__ == "__main__":
    unittest.main()
