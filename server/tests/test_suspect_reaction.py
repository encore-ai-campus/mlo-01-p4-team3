"""의심 제기·모순 지적 즉시 반응 줄(11/10 사용자 승인). 엔진과 무관한 대사창 연출이다."""
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from engine.domain.model import (Claim, ClaimType, CommandKind, EvidenceRef, EvidenceType, Phase, Place, Slot,
                                 Speech)
from server.api import vn_script
from server.api.vn_script import REACTION_CONTRADICTED, REACTION_SUSPECTED, reaction_lines, reaction_text
from .test_vn_results import fake_view

TEMPERAMENTS = {"jumo": "keen", "blacksmith": "hothead", "yakcho": "keen", "hunjang": "scholar", "meosum": "timid"}


def talk(speech_id, speaker, kind, target=None, day=1, phase=Phase.ROUND1, evidence=()):
    """회의 말 발언 하나."""
    return Speech(speech_id, day, phase, speaker, kind, target=target, evidence=list(evidence))


def claim(claim_id, speaker, kind=ClaimType.WHEREABOUTS, seen=(), place=Place.TAVERN):
    """공개 진술 하나(0일째 밤 한밤)."""
    return Claim(claim_id, speaker, kind, speaker, 0, Slot.MIDNIGHT, place, seen=list(seen), day=1)


def refs(*claim_ids):
    """진술 근거 목록."""
    return [EvidenceRef(EvidenceType.CLAIM, cid) for cid in claim_ids]


class ApprovedSentences(SimpleTestCase):
    """문장 원문(11/10 사용자 승인)."""

    def test_suspected(self):
        """의심받았을 때: 성향별 2개."""
        self.assertEqual(REACTION_SUSPECTED, {
            None: ("내가 뭘 어쨌다고 그러오!", "날 의심하다니, 억울하오."),
            "scholar": ("근거도 없이 나를 의심하는 것이오?", "의심은 자유이나, 섣부르구려."),
            "keen": ("아이고, 나를 잡겠다고? 헛짚었네.", "허허, 내가 수상해 보이나?"),
            "hothead": ("뭐? 나라고?", "누굴 의심하는 거냐!"),
            "timid": ("제, 제가요? 아, 아닙니다요!", "그, 그런 눈으로 보지 마셔요…"),
        })

    def test_contradicted(self):
        """모순을 지적받았을 때: 성향별 2개."""
        self.assertEqual(REACTION_CONTRADICTED, {
            None: ("내 말이 어디가 어긋났단 말이오?", "말이 다르다니, 무슨 소리요?"),
            "scholar": ("어느 대목이 어긋난다는 것인지 짚어 보시오.", "내 말의 앞뒤를 다시 들어 보시오."),
            "keen": ("말이 다르다니, 어디가 다르단 말인가?", "에이, 내 말을 잘못 들은 게지."),
            "hothead": ("다르긴 뭐가 달라!", "트집 잡지 마라!"),
            "timid": ("제, 제 말이 다르다고요…?", "어, 어디가 다르다는 건지… 모르겠습니다요."),
        })


class ReactionChoice(SimpleTestCase):
    """고르기는 (발언 종류, 발언 ID, 성향)으로 결정적이다."""

    def test_same_speech_same_sentence(self):
        """같은 입력이면 몇 번을 불러도 같은 문장."""
        first = reaction_text(CommandKind.SUSPECT, "sp007", "hothead")
        self.assertEqual({reaction_text(CommandKind.SUSPECT, "sp007", "hothead") for _ in range(5)}, {first})
        self.assertIn(first, REACTION_SUSPECTED["hothead"])

    def test_both_variants_are_used(self):
        """발언 ID가 다르면 두 변형이 모두 나온다."""
        picked = {reaction_text(CommandKind.POINT_CONTRADICTION, f"sp{i:03d}", "scholar") for i in range(40)}
        self.assertEqual(picked, set(REACTION_CONTRADICTED["scholar"]))

    def test_unknown_temperament_falls_back(self):
        """성향이 없거나 표에 없는 성향이면 기본 줄."""
        self.assertIn(reaction_text(CommandKind.SUSPECT, "sp001", None), REACTION_SUSPECTED[None])
        self.assertIn(reaction_text(CommandKind.SUSPECT, "sp001", "stoic"), REACTION_SUSPECTED[None])


class ReactionLines(SimpleTestCase):
    """플레이어의 의심·모순 지적 뒤에만, 지목된 주민이 화자로 선다."""

    def test_player_suspect_and_contradiction_get_target_line(self):
        """화자 이름·인물 ID·그림은 지목된 주민, 날짜·단계는 발언과 같다."""
        speeches = [talk("sp001", "mudang", CommandKind.SUSPECT, "jumo"),
                    talk("sp002", "mudang", CommandKind.POINT_CONTRADICTION, "blacksmith", day=2, phase=Phase.ROUND2)]
        lines = reaction_lines(fake_view(speeches=speeches), TEMPERAMENTS)
        self.assertEqual(set(lines), {"sp001", "sp002"})
        (sus,), (con,) = lines["sp001"], lines["sp002"]
        self.assertEqual((sus.speaker, sus.speaker_id, sus.sprite_id, sus.day, sus.phase),
                         ("주모", "jumo", "sprite:jumo", 1, "round1"))
        self.assertIn(sus.text, REACTION_SUSPECTED["keen"])
        self.assertEqual((con.speaker, con.speaker_id, con.sprite_id, con.day, con.phase),
                         ("대장장이", "blacksmith", "sprite:blacksmith", 2, "round2"))
        self.assertIn(con.text, REACTION_CONTRADICTED["hothead"])
        for line in (sus, con):
            self.assertFalse(line.guide or line.mascot or line.full or line.result)
            self.assertTrue(line.line_id.startswith("reaction-"))

    def test_no_reaction_for_npc_talk_or_other_kinds(self):
        """주민끼리의 의심·모순 지적, 변호·지켜보기, 대상 없는 발언, 무당을 대상으로 한 발언에는 없다."""
        speeches = [talk("sp001", "jumo", CommandKind.SUSPECT, "blacksmith"),
                    talk("sp002", "yakcho", CommandKind.POINT_CONTRADICTION, "jumo"),
                    talk("sp003", "mudang", CommandKind.DEFEND, "jumo"),
                    talk("sp004", "mudang", CommandKind.SILENCE),
                    talk("sp005", "mudang", CommandKind.SUSPECT, None),
                    talk("sp006", "jumo", CommandKind.SUSPECT, "mudang")]
        self.assertEqual(reaction_lines(fake_view(speeches=speeches), TEMPERAMENTS), {})

    def test_without_temperaments_uses_default(self):
        """성향 정보가 없으면 기본 줄."""
        lines = reaction_lines(fake_view(speeches=[talk("sp001", "mudang", CommandKind.SUSPECT, "jumo")]))
        self.assertIn(lines["sp001"][0].text, REACTION_SUSPECTED[None])

    def test_reload_gives_same_lines(self):
        """공개 기록만으로 만들어 다시 그려도 같은 줄."""
        speeches = [talk(f"sp{i:03d}", "mudang", CommandKind.SUSPECT, "jumo") for i in range(1, 6)]
        self.assertEqual(reaction_lines(fake_view(speeches=speeches), TEMPERAMENTS),
                         reaction_lines(fake_view(speeches=speeches), TEMPERAMENTS))

    def test_does_not_touch_public_record(self):
        """반응 줄은 공개 발언 기록을 늘리거나 바꾸지 않는다(엔진 상태와 무관)."""
        speeches = [talk("sp001", "mudang", CommandKind.SUSPECT, "jumo")]
        view = fake_view(speeches=speeches)
        before = [vars(s).copy() for s in view.public.speeches]
        reaction_lines(view, TEMPERAMENTS)
        self.assertEqual([vars(s) for s in view.public.speeches], before)

    def test_rendered_as_vn_line(self):
        """대사창 줄 템플릿으로 그리면 화자·인물 ID가 붙고 안내·마스코트 표시는 없다."""
        line = reaction_lines(fake_view(speeches=[talk("sp001", "mudang", CommandKind.SUSPECT, "jumo")]),
                              TEMPERAMENTS)["sp001"][0]
        html = render_to_string("meeting_parts/vn_line.html", {"line": line})
        self.assertIn('data-speaker="주모"', html)
        self.assertIn('data-speaker-id="jumo"', html)
        self.assertNotIn("data-guide", html)
        self.assertNotIn("data-mascot", html)


class ContradictionTarget(SimpleTestCase):
    """화면의 모순 지적은 대상 칸 없이 진술 두 개만 보낸다: 지목된 주민을 근거에서 고른다."""

    def view(self, speech):
        """진술 4개와 발언 하나가 있는 가짜 화면."""
        view = fake_view(claims=[claim("cl01", "jumo"), claim("cl02", "jumo", place=Place.WELL),
                                 claim("cl03", "yakcho", ClaimType.SIGHTING, seen=["blacksmith"]),
                                 claim("cl04", "blacksmith"), claim("cl05", "yakcho")],
                         speeches=[speech])
        return view

    def target_of(self, evidence):
        """모순 지적 근거에 대한 반응 화자 ID(없으면 None)."""
        lines = reaction_lines(self.view(talk("sp001", "mudang", CommandKind.POINT_CONTRADICTION, evidence=evidence)),
                               TEMPERAMENTS)
        return lines["sp001"][0].speaker_id if lines else None

    def test_same_speaker_pair(self):
        """같은 사람의 두 진술이면 그 사람."""
        self.assertEqual(self.target_of(refs("cl01", "cl02")), "jumo")

    def test_contradiction_record(self):
        """모순 기록 1개(주장 ID 두 개)도 같은 규칙."""
        self.assertEqual(self.target_of([EvidenceRef(EvidenceType.CONTRADICTION, "cl02|cl01")]), "jumo")

    def test_sighting_against_whereabouts(self):
        """남의 목격과 어긋난 행적이면 목격된 당사자."""
        self.assertEqual(self.target_of(refs("cl03", "cl04")), "blacksmith")

    def test_unclear_pair_has_no_reaction(self):
        """서로 관계없는 두 사람 진술, 근거 부족이면 반응 없음."""
        self.assertIsNone(self.target_of(refs("cl01", "cl05")))
        self.assertIsNone(self.target_of(refs("cl01")))
        self.assertIsNone(self.target_of(refs("cl01", "cl99")))

    def test_line_uses_contradiction_sentences(self):
        """모순 지적 반응은 모순 문장 표에서 고른다."""
        lines = reaction_lines(self.view(talk("sp001", "mudang", CommandKind.POINT_CONTRADICTION,
                                              evidence=refs("cl01", "cl02"))), TEMPERAMENTS)
        self.assertIn(lines["sp001"][0].text, REACTION_CONTRADICTED["keen"])


class EngineUntouched(SimpleTestCase):
    """반응 문장은 vn_script 상수에만 있고 엔진 콘텐츠 템플릿에는 없다."""

    def test_not_in_content_templates(self):
        """콘텐츠 묶음 템플릿 문장과 겹치지 않는다(엔진 발언 문장이 아님)."""
        import json
        from pathlib import Path
        bundle = json.loads((Path(__file__).resolve().parents[2] / "content" / "bundle_v1.json").read_text("utf-8"))
        texts = {v for t in bundle["templates"] for v in t["variants"]}
        ours = {v for table in vn_script.REACTIONS.values() for pair in table.values() for v in pair}
        self.assertFalse(texts & ours)
