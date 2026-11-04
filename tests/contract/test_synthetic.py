"""합성 데이터 생성기 출력이 계약을 지키는지."""
from __future__ import annotations

import unittest

from contracts.datasets import TrainingRow
from engine.domain import serde
from engine.domain.events import EventEnvelope
from tools.gen_synthetic import generate


class Synthetic(unittest.TestCase):
    def test_valid_and_deterministic(self):
        e1, r1 = generate(6, 3, ["base6", "duo9", "duo11"])
        e2, r2 = generate(6, 3, ["base6", "duo9", "duo11"])
        self.assertEqual(serde.to_jsonable(e1), serde.to_jsonable(e2))
        self.assertEqual(serde.to_jsonable(r1), serde.to_jsonable(r2))
        for e in e1:
            serde.from_jsonable(EventEnvelope, serde.to_jsonable(e))
            self.assertEqual(e.source.value, "synthetic")
        for r in r1:
            serde.from_jsonable(TrainingRow, serde.to_jsonable(r))
            self.assertEqual(r.source, "synthetic")
            self.assertIn(r.label, (0, 1))
            self.assertNotIn(r.observer, ("mudang", r.candidate))

    def test_every_game_ends_once(self):
        events, _ = generate(10, 11, ["base6"])
        ended = [e.game_id for e in events if e.event_type == "game_ended"]
        self.assertEqual(len(ended), len(set(ended)))
        self.assertEqual(len(ended), 10)
        # 진실은 종료 이벤트 뒤에만
        for gid in set(ended):
            types = [e.event_type for e in events if e.game_id == gid]
            self.assertEqual(types[-2:], ["game_ended", "truth_revealed"])


if __name__ == "__main__":
    unittest.main()
