"""복기 의심 근거 풀이(`engine.domain.review.suspect_basis`·`basis_text`, 11/11 사용자 승인 문구)."""
import json
import unittest
from pathlib import Path

from engine.domain.model import (Claim, ClaimType, CommandKind, DisappearanceRecord, EvidenceRef, EvidenceType, Phase,
                                 Place, Polarity, PrivateKnowledge, PublicRecord, Role, Sighting, Slot, Speech,
                                 TellRecord)
from engine.domain.review import (BASIS_AT_CRIME_SCENE, BASIS_CLAIMED_SEEING_ME_FALSELY, BASIS_CONTRADICTION,
                                  BASIS_OTHERS_SUSPECT, BASIS_OWN_WITNESS_LIE, BASIS_TELL, ReviewTraceItem,
                                  SuspectBasis, basis_text, suspect_basis)

LINES = json.loads((Path(__file__).resolve().parents[2] / "content" / "review_lines_v1.json")
                   .read_text(encoding="utf-8"))["basis_lines"]
NAMES = {"hunjang": "훈장", "meosum": "머슴", "yakcho": "약초꾼"}


def item(evidence, kind="suspect", actor="hunjang", target="meosum"):
    return ReviewTraceItem("d10", 2, "round1", actor, kind, ["rule_suspect"],
                           [EvidenceRef(EvidenceType.CLAIM, cid) for cid in evidence], "rule-v0", "rv_rule_suspect",
                           target_npc=target)


def where(cid, who, place, night=1, slot=Slot.MIDNIGHT, polarity=Polarity.AFFIRM):
    return Claim(cid, who, ClaimType.WHEREABOUTS, who, night, slot, place, polarity)


def saw(cid, who, seen, place, night=1, slot=Slot.MIDNIGHT):
    return Claim(cid, who, ClaimType.SIGHTING, who, night, slot, place, seen=list(seen))


def private(witnessed=(), whereabouts=None):
    return PrivateKnowledge("hunjang", Role.VILLAGER, whereabouts=whereabouts or {}, witnessed=list(witnessed))


VANISH = DisappearanceRecord(1, "yakcho", Place.WELL, Slot.MIDNIGHT)


class SuspectBasisTest(unittest.TestCase):
    def test_at_crime_scene(self):
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL)], disappearances=[VANISH])
        basis = suspect_basis(item(["c1"]), pub, private())
        self.assertEqual(basis, SuspectBasis(BASIS_AT_CRIME_SCENE, "yakcho"))
        self.assertEqual(basis_text(item(["c1"]), basis, LINES, NAMES),
                         "머슴은 약초꾼이 사라진 그때 그 자리에 자기도 있었다고 말했습니다.")

    def test_own_witness_lie(self):
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL)])
        mine = private(witnessed=[Sighting(1, Slot.MIDNIGHT, Place.TAVERN, ["meosum"])])
        basis = suspect_basis(item(["c1"]), pub, mine)
        self.assertEqual(basis, SuspectBasis(BASIS_OWN_WITNESS_LIE))
        self.assertEqual(basis_text(item(["c1"]), basis, LINES, NAMES),
                         "훈장은 그날 머슴을 다른 곳에서 봤기 때문에 그 말을 믿지 않았습니다.")

    def test_claimed_seeing_me_falsely(self):
        pub = PublicRecord(claims=[saw("c2", "meosum", ["hunjang"], Place.SHRINE)])
        mine = private(whereabouts={1: {Slot.MIDNIGHT: Place.TAVERN}})
        basis = suspect_basis(item(["c2"]), pub, mine)
        self.assertEqual(basis, SuspectBasis(BASIS_CLAIMED_SEEING_ME_FALSELY))
        self.assertEqual(basis_text(item(["c2"]), basis, LINES, NAMES),
                         "머슴은 훈장을 봤다고 했지만, 훈장은 그때 거기 없었습니다.")

    def test_none_when_no_rule_matches(self):
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL)],
                           disappearances=[DisappearanceRecord(1, "yakcho", Place.TAVERN, Slot.MIDNIGHT)])
        mine = private(witnessed=[Sighting(1, Slot.MIDNIGHT, Place.WELL, ["meosum"])])   # 본 곳과 말이 같음
        self.assertIsNone(suspect_basis(item(["c1"]), pub, mine))
        self.assertEqual(basis_text(item(["c1"]), None, LINES, NAMES), "")

    def test_both_rules_pick_heavier_weight(self):
        """실종 장소 진술(W 8)과 직접 본 것과 다름(W 35)이 같이 맞으면 무거운 쪽."""
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL)], disappearances=[VANISH])
        mine = private(witnessed=[Sighting(1, Slot.MIDNIGHT, Place.TAVERN, ["meosum"])])
        self.assertEqual(suspect_basis(item(["c1"]), pub, mine).basis, BASIS_OWN_WITNESS_LIE)

    def test_skips_non_suspect_home_deny_and_other_subject(self):
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL), where("c3", "meosum", Place.HOME),
                                   where("c4", "meosum", Place.WELL, polarity=Polarity.DENY),
                                   where("c5", "yakcho", Place.WELL)],
                           disappearances=[VANISH, DisappearanceRecord(1, "yakcho", Place.HOME, Slot.MIDNIGHT)])
        self.assertIsNone(suspect_basis(item(["c1"], kind="vote"), pub, private()))      # 의심 항목만
        self.assertIsNone(suspect_basis(item(["c3"]), pub, private()))                   # 집은 피해 장소 규칙 제외
        self.assertIsNone(suspect_basis(item(["c4"]), pub, private()))                   # 부정 주장
        self.assertIsNone(suspect_basis(item(["c5"]), pub, private()))                   # 근거 주장의 대상이 다름
        self.assertIsNone(suspect_basis(item(["c1"]), None, private()))                  # 공개 기록 없음
        self.assertIsNone(suspect_basis(item(["zz"]), pub, private()))                   # 모르는 근거 ID

    def test_frame_suspicion_is_not_explained(self):
        """씐 자 누명(frame_*)은 믿음 규칙을 쓰지 않으므로 풀이하지 않는다."""
        pub = PublicRecord(claims=[where("c1", "meosum", Place.WELL)], disappearances=[VANISH])
        framed = item(["c1"])
        framed.reason_codes = ["frame_suspected"]
        self.assertIsNone(suspect_basis(framed, pub, private()))


def ref_item(*refs):
    it = item([])
    it.evidence = [EvidenceRef(t, rid) for t, rid in refs]
    return it


class SecondaryBasisTest(unittest.TestCase):
    """4~6(11/11 추가 승인): 1~3이 맞지 않을 때 근거 종류로 고른다."""
    claims = [where("c1", "meosum", Place.WELL), saw("c2", "yakcho", ["jumo"], Place.WELL)]

    def test_contradiction(self):
        it = ref_item((EvidenceType.CONTRADICTION, "c1|c2"))
        basis = suspect_basis(it, PublicRecord(claims=self.claims), private())
        self.assertEqual(basis, SuspectBasis(BASIS_CONTRADICTION))
        self.assertEqual(basis_text(it, basis, LINES, NAMES), "머슴의 말이 다른 사람의 진술과 어긋났습니다.")

    def test_tell_only_when_shown(self):
        it = ref_item((EvidenceType.TELL, "c1"))
        shown = PublicRecord(claims=self.claims, tells=[TellRecord("c1", 2, True)])
        basis = suspect_basis(it, shown, private())
        self.assertEqual(basis, SuspectBasis(BASIS_TELL))
        self.assertEqual(basis_text(it, basis, LINES, NAMES), "머슴은 그 말을 할 때 기색이 수상했습니다.")
        hidden = PublicRecord(claims=self.claims, tells=[TellRecord("c1", 2, False)])
        self.assertIsNone(suspect_basis(it, hidden, private()))

    def test_others_suspect_speech(self):
        sp = Speech("s1", 2, Phase.ROUND1, "yakcho", CommandKind.SUSPECT, target="meosum")
        it = ref_item((EvidenceType.MAJORITY, "s1"))
        basis = suspect_basis(it, PublicRecord(claims=self.claims, speeches=[sp]), private())
        self.assertEqual(basis, SuspectBasis(BASIS_OTHERS_SUSPECT))
        self.assertEqual(basis_text(it, basis, LINES, NAMES), "다른 사람들도 머슴을 수상하게 여기고 있었습니다.")

    def test_secondary_skips_wrong_shapes(self):
        own = Speech("s2", 2, Phase.ROUND1, "hunjang", CommandKind.SUSPECT, target="meosum")      # 자기 발언
        react = Speech("s3", 2, Phase.ROUND1, "meosum", CommandKind.INTERROGATE)                 # 캐묻기 반응
        others = [saw("c3", "yakcho", ["jumo"], Place.TAVERN)]
        pub = PublicRecord(claims=self.claims + others, speeches=[own, react])
        self.assertIsNone(suspect_basis(ref_item((EvidenceType.MAJORITY, "s2")), pub, private()))
        self.assertIsNone(suspect_basis(ref_item((EvidenceType.MAJORITY, "s3")), pub, private()))
        self.assertIsNone(suspect_basis(ref_item((EvidenceType.CONTRADICTION, "c2|c3")), pub, private()))  # 대상 말 아님
        self.assertIsNone(suspect_basis(ref_item((EvidenceType.TELL, "c2")), pub, private()))               # 대상 말 아님

    def test_primary_rules_win_over_secondary(self):
        pub = PublicRecord(claims=self.claims, disappearances=[VANISH], tells=[TellRecord("c1", 2, True)])
        it = ref_item((EvidenceType.TELL, "c1"), (EvidenceType.CLAIM, "c1"))
        self.assertEqual(suspect_basis(it, pub, private()).basis, BASIS_AT_CRIME_SCENE)
        both = ref_item((EvidenceType.TELL, "c1"), (EvidenceType.CONTRADICTION, "c1|c2"))
        self.assertEqual(suspect_basis(both, pub, private()).basis, BASIS_CONTRADICTION)


class RealGamesTest(unittest.TestCase):
    def test_real_games_fill_basis_without_leftover_slots(self):
        """규칙 v0 실제 판 20개: 풀이가 붙은 의심이 있고, 문장에 자리표시자·괄호 조사가 남지 않는다."""
        from tests.engine.test_review import play
        from tests.engine.test_scenarios_engine import CONTENT
        from engine.domain.review import TraceInput, review_trace
        names = {c.npc_id: c.name_ko for c in CONTENT.characters}
        found = 0
        for seed in range(1, 21):
            state, events = play(seed)
            for it in review_trace(TraceInput("g", state.meta.rule_version, seed, events, state.public)).items:
                npc = state.npcs.get(it.actor)
                text = basis_text(it, suspect_basis(it, state.public, npc.private if npc else None), LINES, names)
                if text:
                    found += 1
                    self.assertIn("rule_suspect", it.reason_codes)
                    self.assertNotRegex(text, r"[{}]|\(는\)|\(가\)|\(를\)")
        self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
