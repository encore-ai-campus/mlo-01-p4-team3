"""Alias, evidence and time resolution against public input only."""
import unittest

from engine.domain.model import Claim, ClaimType, EvidenceRef, EvidenceType, Phase, Place, PublicRecord, Slot
from engine.ports import ParseContext
from intent.matcher import find_people, match_evidence, normalize_people, resolve_time


def context():
    """A fixed public context independent of a model prediction."""
    return ParseContext("g", 1, Phase.ROUND1, PublicRecord(),
                        {"hunjang": ["훈장", "김 훈장"], "jumo": ["주모"], "meosum": ["머슴"]})


def claim(record_id, speaker="hunjang", place=Place.SHRINE):
    """Create one visible whereabouts claim."""
    return Claim(record_id, speaker, ClaimType.WHEREABOUTS, speaker, 0, Slot.MIDNIGHT, place)


class MatcherTests(unittest.TestCase):
    """Reject substring aliases and uncertain evidence."""

    def test_longest_alias_and_particles(self):
        matches = find_people("김 훈장님은 주모에게 물었다", context().aliases)
        self.assertEqual([(m.npc_id, m.alias) for m in matches],
                         [("hunjang", "김 훈장"), ("jumo", "주모")])
        self.assertEqual(find_people("주모양과 훈장비", context().aliases), [])

    def test_normalization_preserves_grammar(self):
        self.assertEqual(normalize_people("훈장은 수상하다", context().aliases), "인물은 수상하다")
        self.assertEqual(normalize_people("<P2>는 결백하다", {}), "인물는 결백하다")

    def test_selected_cards_and_unknown_ids(self):
        ctx = context()
        ctx.public.claims = [claim("c1"), claim("c2", "jumo")]
        ctx.selected_cards = [EvidenceRef(EvidenceType.CLAIM, "c1"), EvidenceRef(EvidenceType.CLAIM, "c2")]
        self.assertEqual(match_evidence("무관한 문장", ctx).evidence, ctx.selected_cards)
        ctx.selected_cards = [EvidenceRef(EvidenceType.CLAIM, "private-id")]
        self.assertEqual(match_evidence("", ctx).evidence, [])

    def test_unique_public_pair_and_tied_alternatives(self):
        ctx = context()
        ctx.public.claims = [claim("c1"), claim("c2", "jumo")]
        self.assertEqual(len(match_evidence("서낭당 한밤 진술이 안 맞는다", ctx).evidence), 2)
        ctx.public.claims.append(claim("c3", "meosum"))
        result = match_evidence("서낭당 한밤 진술이 안 맞는다", ctx)
        self.assertEqual(result.evidence, [])
        self.assertEqual(len(result.alternatives), 3)

    def test_duplicate_cards_do_not_make_two_claims(self):
        ctx = context()
        ctx.public.claims = [claim("c1")]
        ctx.selected_cards = [EvidenceRef(EvidenceType.CLAIM, "c1")] * 2
        self.assertEqual(len(match_evidence("", ctx).evidence), 1)

    def test_time_requires_public_reference_for_relative_night(self):
        ctx = context()
        self.assertEqual(resolve_time("어젯밤 한밤", ctx), (None, Slot.MIDNIGHT))
        ctx.public.claims = [claim("c1")]
        self.assertEqual(resolve_time("어젯밤 한밤", ctx), (0, Slot.MIDNIGHT))
        self.assertEqual(resolve_time("2번째 밤 초저녁", ctx), (2, Slot.EVENING))
        self.assertEqual(resolve_time("어디 있었소?", ctx), (None, None))
