"""4차 대사 작업(11/10 사용자 승인): 진술표 카드 문장 틀, 까치 실종 발표, 판 도중 원한 단서 알림."""
import re
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from engine.domain.content import GrudgeClue
from engine.domain.model import (Claim, ClaimType, ClueRecord, CommandKind, ConfessionRecord, Phase, Place,
                                 ReactionIntent, Slot, Speech)
from server.api import tutorial_script, vn_script
from server.api.meeting_view import _clue_vn_lines
from server.api.vn_script import announcement_lines, claim_card_text
from server.app.clue_notice import DUPLICATE, NEW, UNLOCK, ClueNotice, clue_notices
from .test_vn_results import RecordedGameLines, fake_view


def disappearance(night, victim, place, slot=Slot.MIDNIGHT):
    """공개 실종 기록 한 건(가짜)."""
    return SimpleNamespace(night=night, victim=victim, place=place, slot=slot)


def view_with(disappearances, day, speeches=()):
    """실종 기록과 발언을 더한 가짜 PlayerView."""
    view = fake_view(speeches=speeches, day=day)
    view.names.update({"gwabu": "과부", "hunjang": "훈장"})
    view.public.disappearances = list(disappearances)
    return view


class ClaimCards(SimpleTestCase):
    """진술표 카드 문장은 대사창 진술 줄과 같은 승인 문장 틀이다."""

    def test_card_text_uses_statement_frames(self):
        """행적·목격·못 봄 모두 statement_text와 같은 문장."""
        view = fake_view()
        whereabouts = Claim("cl01", "jumo", ClaimType.WHEREABOUTS, "jumo", 0, Slot.EVENING, Place.TAVERN, day=1)
        sighting = Claim("cl02", "yakcho", ClaimType.SIGHTING, "yakcho", 0, Slot.MIDNIGHT, Place.WELL,
                         seen=["jumo", "blacksmith"], day=1)
        nobody = Claim("cl03", "jumo", ClaimType.NON_SIGHTING, "jumo", 0, Slot.MIDNIGHT, Place.SHRINE, day=1)
        self.assertEqual(claim_card_text(view, whereabouts), "0일째 밤 초저녁에는 주막에 있었소.")
        self.assertEqual(claim_card_text(view, sighting), "0일째 밤 한밤에 우물가에서 주모와 대장장이를 보았소.")
        self.assertEqual(claim_card_text(view, nobody), "0일째 밤 한밤에 서낭당에서는 아무도 보지 못했소.")


class KkachiAnnouncement(SimpleTestCase):
    """아침 실종 발표는 까치가 승인 문장으로 말하고, 마스코트 자리에 까치를 세운다."""

    def test_approved_sentences(self):
        """문장 원문(11/10 사용자 승인)."""
        self.assertEqual(vn_script.ANNOUNCE_MISSING,
                         "깍깍! 간밤 {slot}에 {name}이(가) 사라졌어요. {name}의 짚신이 {place}에서 발견됐대요!")
        self.assertEqual(vn_script.ANNOUNCE_NONE, "깍깍! 간밤엔 아무도 사라지지 않았어요.")

    def test_missing_line_picks_particle_and_shows_kkachi(self):
        """받침 없는 이름은 '가', 있는 이름은 '이'. 화자 까치, 까치 그림. 발표 줄 바로 뒤에 피해자 전신 컷 줄."""
        view = view_with([disappearance(0, "gwabu", Place.WELL), disappearance(1, "hunjang", Place.HOME, Slot.EVENING)],
                         day=2)
        first, first_full, second, second_full, _cue = announcement_lines(view)   # 끝은 2일째 신뢰 단서(test_trust_cue)
        self.assertEqual(first.text, "깍깍! 간밤 한밤에 과부가 사라졌어요. 과부의 짚신이 우물가에서 발견됐대요!")
        self.assertEqual(second.text, "깍깍! 간밤 초저녁에 훈장이 사라졌어요. 훈장의 짚신이 훈장의 집에서 발견됐대요!")
        self.assertEqual([(line.day, line.phase) for line in (first, second)], [(1, "morning"), (2, "morning")])
        for line in (first, second):
            self.assertEqual((line.speaker, line.sprite_id, line.mascot, line.full, line.guide),
                             ("까치", "mascot:kkachi", True, False, False))
            self.assertTrue(line.line_id.startswith("announcement-"))
        for line, cut, victim, name in ((first, first_full, "gwabu", "과부"), (second, second_full, "hunjang", "훈장")):
            self.assertEqual(cut.line_id, f"{line.line_id}-full")
            self.assertEqual(cut.text, line.text)   # 새 문장 없이 발표 문장 그대로(전신 컷 동안 대사창은 숨는다)
            self.assertEqual((cut.day, cut.phase, cut.speaker_id, cut.sprite_id, cut.full, cut.mascot, cut.actor_name, cut.beat),
                             (line.day, "morning", victim, f"sprite:{victim}", True, False, name, True))
            self.assertFalse(line.beat)

    def test_morning_without_disappearance(self):
        """실종 기록이 없는 아침은 '아무도 사라지지 않았어요'."""
        lines = announcement_lines(view_with([disappearance(0, "gwabu", Place.WELL)], day=2))
        self.assertEqual(len(lines), 4)   # 1일째 발표·전신 컷, 2일째 실종 없음(전신 컷 없음)·신뢰 단서
        self.assertEqual(lines[2].text, "깍깍! 간밤엔 아무도 사라지지 않았어요.")
        self.assertEqual((lines[2].day, lines[2].full, lines[2].sprite_id), (2, False, "mascot:kkachi"))

    def test_rendered_source_marks_mascot(self):
        """대사 줄 HTML: 발표 줄은 data-mascot·화자 까치·전신 컷 아님, 다음 줄은 피해자 전신 컷."""
        line, cut = announcement_lines(view_with([disappearance(0, "gwabu", Place.WELL)], day=1))
        cut_html = render_to_string("meeting_parts/vn_line.html", {"line": cut})
        self.assertIn('data-full="true"', cut_html)
        self.assertIn('data-actor-name="과부"', cut_html)
        self.assertIn('data-beat="true"', cut_html)   # 한 박자 컷: 닫으면 다음 줄, 밤 화면에서는 뺀다
        self.assertIn("sprite/gwabu", cut_html)
        self.assertNotIn("data-mascot", cut_html)
        html = render_to_string("meeting_parts/vn_line.html", {"line": line})
        self.assertIn('data-speaker="까치"', html)
        self.assertIn('data-mascot="true"', html)
        self.assertNotIn("data-beat", html)
        self.assertIn("mascot/kkachi", html)
        self.assertNotIn("data-full", html)
        self.assertNotIn("data-guide", html)


class KkachiInRecordedGame(RecordedGameLines):
    """녹화 판 1일째 아침에 까치 발표 줄이 나온다."""

    test_first_morning_statements_stand_with_speakers = None
    test_vote_lock_and_divination_appear_as_dialogue = None
    test_every_public_record_reaches_the_dialogue = None

    def test_first_morning_announcement_is_kkachi(self):
        """발표 줄(까치 마스코트) 바로 뒤 피해자 전신 컷. 아침 기본 까치 그림은 없고, 기록 창의 실종 기록 문구는 남는다."""
        day, phase, lines = self.lines()
        current = self.current(day, phase, lines)
        ids = [line["data-line-id"] for line in current]
        at = next(i for i, x in enumerate(ids) if x.startswith("announcement-"))
        self.assertEqual((current[at]["data-speaker"], current[at].get("data-mascot")), ("까치", "true"))
        self.assertEqual(ids[at + 1], f"{ids[at]}-full")
        self.assertEqual(current[at + 1].get("data-full"), "true")
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertNotIn('class="vn-mascot"', html)   # 까치는 발표 줄이 나올 때만 JS가 세운다
        self.assertIn("1일째 아침:", html)
        cards_match = re.search(r'<ul\b[^>]*\bclass="claim-cards"[^>]*>(.*?)</ul>', html, re.S)
        self.assertIsNotNone(cards_match, "공개 진술 목록이 있어야 한다")
        cards = cards_match.group(1)
        self.assertNotRegex(cards, r"· (있었다|없었다)</p>")
        self.assertRegex(cards, r"<p>\d+일째 밤 (초저녁|한밤)에")


CLUES = [GrudgeClue("clue_famine", "신원 단서 문장", "secret:grain", "identity"),
         GrudgeClue("clue_valley", "다른 신원 단서", "secret:taboo_herb", "identity"),
         GrudgeClue("clue_debt", "죽음 단서 문장", "secret:gambling", "death"),
         GrudgeClue("clue_gate", "대상 단서 문장", "secret:drinking", "target")]


def secret(secret_id, clue_id):
    """단서 ID만 쓰는 가짜 비밀."""
    return SimpleNamespace(secret_id=secret_id, grudge_clue_id=clue_id)


SECRETS = [secret("grain", "clue_famine"), secret("taboo_herb", "clue_valley"), secret("gambling", "clue_debt"),
           secret("drinking", "clue_gate"), secret("plain", None)]


class ClueNoticeRule(SimpleTestCase):
    """고백마다 새 단서·이미 가진 단서·한풀이 열림을 고른다."""

    def test_new_duplicate_and_unlock(self):
        """다른 판에서 얻은 신원 단서는 이미 가진 것, 이 판의 죽음 단서는 새것, 대상 단서로 세 부분이 모이면 열림."""
        confessions = [ConfessionRecord(1, "hunjang", "grain"), ConfessionRecord(1, "jumo", "gambling"),
                       ConfessionRecord(2, "yakcho", "drinking"), ConfessionRecord(2, "gwabu", "plain")]
        progress = {"clue_famine": ClueRecord("clue_famine", "g-old", 1),
                    "clue_debt": ClueRecord("clue_debt", "g1", 1), "clue_gate": ClueRecord("clue_gate", "g1", 2)}
        notices = clue_notices("g1", confessions, SECRETS, CLUES, progress)
        self.assertEqual([(n.npc_id, n.kind) for n in notices],
                         [("hunjang", DUPLICATE), ("jumo", NEW), ("yakcho", UNLOCK)])
        self.assertEqual(notices[1].clue_text, "죽음 단서 문장")

    def test_progress_not_yet_saved_counts_as_new(self):
        """명령 직후 진척 반영이 아직이면 새 단서로 본다."""
        notices = clue_notices("g1", [ConfessionRecord(1, "jumo", "gambling")], SECRETS, CLUES, {})
        self.assertEqual([n.kind for n in notices], [NEW])

    def test_clues_from_later_game_are_not_owned_yet(self):
        """이 판 뒤에 시작한 판에서 얻은 단서는 이 판 고백 시점에 없던 것: 나중 판 대상 단서로 열림이 앞당겨지지 않는다."""
        progress = {"clue_famine": ClueRecord("clue_famine", "g-old", 1),
                    "clue_debt": ClueRecord("clue_debt", "g1", 1), "clue_gate": ClueRecord("clue_gate", "g-new", 1)}
        notices = clue_notices("g1", [ConfessionRecord(1, "jumo", "gambling")], SECRETS, CLUES, progress,
                               ["g-old", "g1", "g-new"])
        self.assertEqual([n.kind for n in notices], [NEW])
        later = clue_notices("g-new", [ConfessionRecord(1, "yakcho", "drinking")], SECRETS, CLUES, progress,
                             ["g-old", "g1", "g-new"])
        self.assertEqual([n.kind for n in later], [UNLOCK])

    def test_already_completed_hypothesis_does_not_reopen(self):
        """세 부분이 다른 판에서 이미 모였으면 이 판의 새 신원 단서는 열림이 아니라 새 단서."""
        old = {c: ClueRecord(c, "g-old", 1) for c in ("clue_famine", "clue_debt", "clue_gate")}
        progress = {**old, "clue_valley": ClueRecord("clue_valley", "g1", 1)}
        notices = clue_notices("g1", [ConfessionRecord(1, "jumo", "taboo_herb")], SECRETS, CLUES, progress)
        self.assertEqual([n.kind for n in notices], [NEW])


class ClueLines(SimpleTestCase):
    """고백 발언 바로 뒤 단서 알림 줄. 안내 줄이 아니라 스위치를 꺼도 나온다."""

    def speech(self, speech_id, speaker, reaction=ReactionIntent.CONFESS):
        """캐묻기 답변 발언(기본은 고백)."""
        return Speech(speech_id, 1, Phase.INTERROGATION, speaker, CommandKind.INTERROGATE, reaction=reaction)

    def test_three_kinds(self):
        """새 단서: 해설+족제비 1줄, 이미 가진 단서: 족제비 1줄, 열림: 해설+족제비 2줄."""
        speeches = [self.speech("sp1", "jumo"), self.speech("sp2", "hunjang"), self.speech("sp3", "yakcho"),
                    self.speech("sp4", "gwabu", ReactionIntent.EXPLAIN)]
        view = view_with([], day=1, speeches=speeches)
        notices = [ClueNotice("jumo", 1, "clue_debt", "죽음 단서 문장", NEW),
                   ClueNotice("hunjang", 1, "clue_famine", "신원 단서 문장", DUPLICATE),
                   ClueNotice("yakcho", 1, "clue_gate", "대상 단서 문장", UNLOCK),
                   ClueNotice("gwabu", 1, "clue_valley", "없는 고백", NEW)]
        lines = _clue_vn_lines(view, notices)
        self.assertEqual(set(lines), {"sp1", "sp2", "sp3"})   # 고백 발언이 아니면 붙이지 않는다
        self.assertEqual([(l.speaker, l.text) for l in lines["sp1"]],
                         [("", "죽음 단서 문장"), ("족제비", tutorial_script.CLUE_NEW[0])])
        self.assertEqual([l.text for l in lines["sp2"]], list(tutorial_script.CLUE_DUPLICATE))
        self.assertEqual([l.text for l in lines["sp3"]], ["대상 단서 문장", *tutorial_script.CLUE_UNLOCK])
        for line in [l for group in lines.values() for l in group]:
            self.assertFalse(line.guide)
            self.assertEqual((line.day, line.phase), (1, "interrogation"))
            if line.speaker == "족제비":
                self.assertEqual((line.sprite_id, line.mascot), ("mascot:jokjebi", True))
        html = render_to_string("meeting_parts/vn_line.html", {"line": lines["sp1"][0]})
        self.assertIn('data-speaker=""', html)
        self.assertNotIn("data-guide", html)

    def test_approved_sentences(self):
        """문장 원문(11/10 사용자 승인)."""
        self.assertEqual(tutorial_script.CLUE_NEW, (
            "앗, 방금 이야기에 창귀의 원한 단서가 숨어 있었어요! 잊어버려도 괜찮아요, '원한 단서' 탭에 모아 둘게요!",))
        self.assertEqual(tutorial_script.CLUE_DUPLICATE, ("어, 이 이야기는 전에 들은 단서랑 똑같아요. 벌써 모아 둔 거예요!",))
        self.assertEqual(tutorial_script.CLUE_UNLOCK, (
            "우와, 창귀가 누구였는지, 어떻게 죽었는지, 누구를 원망하는지 다 알아냈어요!",
            "이제 쫓아내지 않고 한을 풀어 줄 수 있어요. 다음 판을 시작할 때 한풀이를 골라 봐요!"))
