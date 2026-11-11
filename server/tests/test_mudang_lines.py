"""무당(플레이어) 회의 발언 문장(11/11 사용자 승인)과 발언 문장의 괄호 조사 표시. 저장된 공개 기록은 바꾸지 않는다."""
import copy
import re
from dataclasses import replace

from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from engine.domain.model import Claim, ClaimType, CommandKind, DivinationResult, Phase, Speech
from server.api.interaction_view import line_context
from server.api.meeting_view import meeting_context
from server.api.ui_viewmodels import build_ui_vm
from server.api.vn_script import MUDANG_DIVINATION_EARLIER, MUDANG_LINES, mudang_line_text, speech_display_text
from .support import make_service
from .test_vn_results import fake_view


def talk(speech_id, speaker, kind, target=None, claim_ids=(), line_text=None):
    """1일째 첫 회의 발언 하나."""
    return Speech(speech_id, 1, Phase.ROUND1, speaker, kind, target=target, claim_ids=list(claim_ids),
                  line_text=line_text)


def reveal(claim_id, subject, result):
    """무당의 점괘 공개 주장(공개 값)."""
    return Claim(claim_id, "mudang", ClaimType.DIVINATION_REVEAL, subject, 0, divination_result=result, day=1)


class ApprovedSentences(SimpleTestCase):
    """문장 원문과 종류별 채움."""

    def test_templates_are_the_approved_sentences(self):
        """11/11 사용자 승인 문장 그대로."""
        self.assertEqual(MUDANG_LINES, {
            CommandKind.SUSPECT: "{target}, 아무래도 수상하오.",
            CommandKind.DEFEND: "{target}은(는) 의심할 사람이 아니오.",
            CommandKind.POINT_CONTRADICTION: "앞서 나온 말과 지금 말이 서로 어긋나오.",
            CommandKind.SILENCE: "……좀 더 지켜보겠소.",
            CommandKind.REVEAL_DIVINATION: "간밤 {target}을(를) 점쳤더니 {result}이 나왔소.",
        })

    def test_each_kind_fills_target_and_particle(self):
        """대상 이름을 넣고 괄호 조사는 받침(대장장이·주모 → 는, 약초꾼 → 은)으로 하나만 남긴다."""
        view = fake_view()
        cases = [
            (talk("sp1", "mudang", CommandKind.SUSPECT, "jumo"), "주모, 아무래도 수상하오."),
            (talk("sp2", "mudang", CommandKind.DEFEND, "blacksmith"), "대장장이는 의심할 사람이 아니오."),
            (talk("sp3", "mudang", CommandKind.DEFEND, "yakcho"), "약초꾼은 의심할 사람이 아니오."),
            (talk("sp4", "mudang", CommandKind.POINT_CONTRADICTION), "앞서 나온 말과 지금 말이 서로 어긋나오."),
            (talk("sp5", "mudang", CommandKind.SILENCE), "……좀 더 지켜보겠소."),
        ]
        for speech, expected in cases:
            self.assertEqual(mudang_line_text(view, speech), expected)

    def test_divination_reveal_reads_public_value(self):
        """점괘 공개는 공개한 값(거짓 공개여도)을 쓴다. 실제 점괘(player.divinations)는 읽지 않는다."""
        claims = [reveal("cl9", "yakcho", DivinationResult.MURKY), reveal("cl8", "jumo", DivinationResult.CLEAR)]
        view = fake_view(claims=claims, divinations={0: {"yakcho": DivinationResult.CLEAR}})
        murky = talk("sp1", "mudang", CommandKind.REVEAL_DIVINATION, "yakcho", claim_ids=["cl9"])
        clear = talk("sp2", "mudang", CommandKind.REVEAL_DIVINATION, "jumo", claim_ids=["cl8"])
        self.assertEqual(mudang_line_text(view, murky), "간밤 약초꾼을 점쳤더니 탁함이 나왔소.")
        self.assertEqual(mudang_line_text(view, clear), "간밤 주모를 점쳤더니 맑음이 나왔소.")

    def test_missing_target_or_unknown_kind_is_empty(self):
        """채울 대상이 없거나 문장이 없는 종류(캐묻기 등)는 빈 문자열."""
        view = fake_view()
        self.assertEqual(mudang_line_text(view, talk("sp1", "mudang", CommandKind.SUSPECT)), "")
        self.assertEqual(mudang_line_text(view, talk("sp2", "mudang", CommandKind.INTERROGATE, "jumo")), "")
        self.assertEqual(mudang_line_text(view, talk("sp3", "mudang", CommandKind.REVEAL_DIVINATION, "jumo")), "")

    def test_deterministic(self):
        """같은 기록이면 몇 번을 불러도 같은 문장."""
        view = fake_view()
        speech = talk("sp1", "mudang", CommandKind.DEFEND, "jumo")
        self.assertEqual({mudang_line_text(view, speech) for _ in range(5)}, {"주모는 의심할 사람이 아니오."})


class DisplayText(SimpleTestCase):
    """발언 표시 문장: 무당 빈 문장은 승인 문장, 그 밖은 괄호 조사만 고른다."""

    def test_npc_line_particles(self):
        """받침 있음·없음 모두 괄호 없이 하나만."""
        view = fake_view()
        npc = talk("sp1", "jumo", CommandKind.SUSPECT, "hunjang")
        self.assertEqual(speech_display_text(view, npc, "훈장이(가) 수상하오."), "훈장이 수상하오.")
        self.assertEqual(speech_display_text(view, npc, "주모이(가) 뭔가 숨기고 있어."), "주모가 뭔가 숨기고 있어.")
        self.assertEqual(speech_display_text(view, npc, "대장장이을(를) 의심할 까닭은 없다고 보오."),
                         "대장장이를 의심할 까닭은 없다고 보오.")

    def test_player_stored_text_kept(self):
        """무당 발언에 저장된 문장이 있으면 그 문장(조사만 고름)을 쓴다."""
        view = fake_view()
        speech = talk("sp1", "mudang", CommandKind.SUSPECT, "jumo")
        self.assertEqual(speech_display_text(view, speech, "약초꾼이(가) 수상하오."), "약초꾼이 수상하오.")
        self.assertEqual(speech_display_text(view, speech, ""), "주모, 아무래도 수상하오.")

    def test_render_fragment_particles(self):
        """캐묻기 답 폴링 조각도 보일 때만 조사를 고른다."""
        output = type("Out", (), {"speech_id": "sp1", "job_id": "job_001", "text": "주모이(가) 왔소.", "final": True})()
        self.assertEqual(line_context("g1", output)["line"]["text"], "주모가 왔소.")


@override_settings(GAME_UI="vn")
class RecordedGame(SimpleTestCase):
    """녹화 판 화면 정보에 무당 발언과 괄호 조사 발언을 끼워 그린다. 저장된 공개 기록은 그대로다."""

    def view(self):
        """1일째 화면 정보에 무당 의심 발언(문장 없음)과 괄호 조사가 있는 주민 발언을 더한다."""
        base = make_service().engine.views[1]
        npc = next(n for n in base.names if n != "mudang")
        speeches = list(base.public.speeches) + [
            talk("sp-mudang", "mudang", CommandKind.SUSPECT, npc),
            talk("sp-npc", npc, CommandKind.SUSPECT, npc, line_text=f"{base.names[npc]}이(가) 수상하오."),
        ]
        return replace(base, public=replace(base.public, speeches=speeches)), base.names[npc]

    def test_player_and_npc_lines_in_record(self):
        """발언 기록(대사창 원본)에 무당 승인 문장과 조사를 고른 주민 문장이 보이고, 자리 문구는 없다."""
        view, name = self.view()
        before = copy.deepcopy(view.public.speeches)
        html = render_to_string("meeting_parts/records.html", meeting_context(view))
        text = re.sub(r"<[^>]+>", " ", html)
        self.assertIn(f"{name}, 아무래도 수상하오.", text)
        self.assertNotIn("이(가) 수상하오", text)
        self.assertEqual(view.public.speeches, before)

    def test_ui_vm_same_on_rebuild(self):
        """다시 그려도 같은 문장(새로 고침·기록 보기)."""
        view, _ = self.view()
        first = [s.text for s in build_ui_vm(view).speeches]
        self.assertEqual([s.text for s in build_ui_vm(view).speeches], first)


class DivinationNight(SimpleTestCase):
    """점괘 공개 문장의 밤 표기(11/11 사용자 승인): 직전 밤이면 '간밤', 그 전이면 'N일째 밤'."""

    @staticmethod
    def said_on(day, claim):
        """claim 점괘를 day 낮에 공개하는 보기와 발언을 만든다."""
        view = fake_view(claims=[claim])
        speech = Speech("sp9", day, Phase.ROUND1, "mudang", CommandKind.REVEAL_DIVINATION,
                        target=claim.subject, claim_ids=[claim.claim_id])
        return view, speech

    def test_earlier_template_is_approved_sentence(self):
        """승인 원문."""
        self.assertEqual(MUDANG_DIVINATION_EARLIER, "{night}일째 밤 {target}을(를) 점쳤더니 {result}이 나왔소.")

    def test_previous_night_is_ganbam(self):
        """3일째에 2일째 밤 점괘를 공개하면 '간밤'."""
        claim = Claim("cl1", "mudang", ClaimType.DIVINATION_REVEAL, "jumo", 2,
                      divination_result=DivinationResult.MURKY, day=3)
        self.assertEqual(mudang_line_text(*self.said_on(3, claim)), "간밤 주모를 점쳤더니 탁함이 나왔소.")

    def test_earlier_night_uses_number(self):
        """3일째에 1일째 밤 점괘를 공개하면 '1일째 밤', 공개한 값을 쓴다."""
        claim = Claim("cl2", "mudang", ClaimType.DIVINATION_REVEAL, "yakcho", 1,
                      divination_result=DivinationResult.CLEAR, day=3)
        self.assertEqual(mudang_line_text(*self.said_on(3, claim)), "1일째 밤 약초꾼을 점쳤더니 맑음이 나왔소.")

    def test_same_record_same_sentence(self):
        """같은 기록이면 늘 같은 문장."""
        claim = Claim("cl3", "mudang", ClaimType.DIVINATION_REVEAL, "yakcho", 1,
                      divination_result=DivinationResult.MURKY, day=4)
        view, speech = self.said_on(4, claim)
        self.assertEqual(mudang_line_text(view, speech), mudang_line_text(copy.deepcopy(view), copy.deepcopy(speech)))
