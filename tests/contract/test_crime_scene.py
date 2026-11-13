"""피해 장소 공개(11/4 PM 결정 P-02)와 알리바이 명제 전제 계약 테스트."""
import json
import unittest

from engine.domain import serde
from engine.domain.model import (
    DisappearanceRecord, EvidenceSource, Place, PropositionKind, PublicRecord, Sighting, Slot,
    alibi_propositions, crime_scene_of,
)
from engine.ports import PlayerView
from server.api.viewmodels import build_meeting_vm
from tests.contract._util import FX as FIXTURES


class CrimeScene(unittest.TestCase):
    def test_record_requires_place(self):
        with self.assertRaises(Exception):
            serde.from_jsonable(DisappearanceRecord, {"night": 0, "victim": "gwabu"})
        r = serde.from_jsonable(DisappearanceRecord, {"night": 0, "victim": "gwabu", "place": "well"})
        self.assertIs(r.slot, Slot.MIDNIGHT)

    def test_home_means_victims_home(self):
        self.assertEqual(crime_scene_of(DisappearanceRecord(1, "hunjang", Place.HOME)), (Place.HOME, "hunjang"))
        self.assertEqual(crime_scene_of(DisappearanceRecord(1, "hunjang", Place.WELL)), (Place.WELL, None))

    def test_alibi_from_public_scene_and_own_witness(self):
        rec = DisappearanceRecord(0, "gwabu", Place.WELL)
        own = [Sighting(0, Slot.MIDNIGHT, Place.TAVERN, ["yakcho", "gwabu", "me"]),
               Sighting(0, Slot.MIDNIGHT, Place.WELL, ["blacksmith"]),        # 피해 장소에서 본 사람 → 명제 없음
               Sighting(0, Slot.EVENING, Place.SHRINE, ["jumo"])]             # 다른 시간대 → 명제 없음
        props = alibi_propositions(rec, own, 1, observer="me")
        self.assertEqual([(p.subject, p.kind, p.source) for p in props],
                         [("yakcho", PropositionKind.NOT_POSSESSED, EvidenceSource.OWN_WITNESS)])

    def test_no_alibi_from_non_public_place(self):
        # 계약: 공공장소에서의 직접 목격만 알리바이 → 같은 시간대라도 집(HOME)에서 본 사람은 명제 없음
        rec = DisappearanceRecord(0, "gwabu", Place.WELL)
        own = [Sighting(0, Slot.MIDNIGHT, Place.HOME, ["yakcho"])]
        self.assertEqual(alibi_propositions(rec, own, 1, observer="me"), [])

    def test_two_ghosts_only_not_executor(self):
        rec = DisappearanceRecord(1, "hunjang", Place.HOME)
        props = alibi_propositions(rec, [Sighting(1, Slot.MIDNIGHT, Place.TAVERN, ["gisaeng"])], 2, observer="hunter")
        self.assertEqual([p.kind for p in props], [PropositionKind.NOT_EXECUTOR])


class RecordedGameScene(unittest.TestCase):
    def test_every_disappearance_has_scene_and_announcement(self):
        data = json.loads((FIXTURES / "api" / "recorded_game_base6.json").read_text(encoding="utf-8"))
        last = data["steps"][-1]["view"]
        view = serde.from_jsonable(PlayerView, last)
        self.assertTrue(view.public.disappearances)
        for d in view.public.disappearances:
            self.assertIsInstance(d.place, Place)
        vm = build_meeting_vm(view)
        self.assertEqual(len(vm.disappearances), len(view.public.disappearances))
        self.assertIn("짚신", vm.disappearances[0].text)
        announced = [e for s in data["steps"] for e in s.get("events", []) if e.get("event_type") == "morning_announced"]
        self.assertTrue(announced)
        for e in announced:
            self.assertIn("crime_scene", e["payload"])


if __name__ == "__main__":
    unittest.main()
