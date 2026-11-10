"""아침 주민 신뢰 단서(11/10 사용자 승인, A-10-01 rule-0.8과 함께): 전날 공개 기록만으로 고르는 해설 1줄."""
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from engine.domain.model import CommandKind, Phase, Place, Slot
from server.api.vn_script import announcement_lines
from server.app import trust_cue as tc
from .test_vn_lines_4th import disappearance, view_with


def vote(day, target, round_="main", voter="jumo"):
    """공개 투표 기록 한 건(가짜)."""
    return SimpleNamespace(day=day, round=round_, voter=voter, target=target)


def speech(day, kind, target, speaker="jumo"):
    """공개 발언 한 건(가짜)."""
    return SimpleNamespace(day=day, kind=kind, target=target, speaker=speaker, phase=Phase.ROUND1)


class Sentences(SimpleTestCase):
    """문장 원문과 0/1/2 이상 구간."""

    def test_approved_sentences(self):
        """승인 문장 3개(11/10 사용자)."""
        self.assertEqual(tc.TRUST_CALM, "주민들은 아직 무당님을 믿는 눈치예요.")
        self.assertEqual(tc.TRUST_WARY, "몇몇 주민이 무당님을 미심쩍게 보고 있어요.")
        self.assertEqual(tc.TRUST_DANGER,
                         "주민들 사이에 무당님을 의심하는 말이 돌아요. 이대로면 무당님이 금줄 광에 갇힐지도 몰라요!")

    def test_levels(self):
        """0 → 안심, 1 → 주의, 2 이상 → 위험. 표와 의심 발언을 더해 센다."""
        self.assertEqual(tc.trust_cue(2, [], []), tc.TRUST_CALM)
        self.assertEqual(tc.trust_cue(2, [vote(1, "mudang")], []), tc.TRUST_WARY)
        self.assertEqual(tc.trust_cue(2, [], [speech(1, CommandKind.SUSPECT, "mudang")]), tc.TRUST_WARY)
        self.assertEqual(tc.trust_cue(2, [vote(1, "mudang")], [speech(1, CommandKind.SUSPECT, "mudang")]),
                         tc.TRUST_DANGER)
        self.assertEqual(tc.trust_cue(3, [vote(2, "mudang", voter=v) for v in ("jumo", "yakcho", "boy")], []),
                         tc.TRUST_DANGER)

    def test_first_morning_has_no_cue(self):
        """1일째 아침은 전날 기록이 없어 단서를 내지 않는다."""
        self.assertIsNone(tc.trust_cue(1, [vote(0, "mudang")], []))


class Counting(SimpleTestCase):
    """세는 기록: 전날·본 투표·무당 대상·의심 발언만."""

    def test_only_previous_day(self):
        """그저께나 오늘 기록은 세지 않는다."""
        votes = [vote(1, "mudang"), vote(3, "mudang")]
        speeches = [speech(1, CommandKind.SUSPECT, "mudang"), speech(3, CommandKind.SUSPECT, "mudang")]
        self.assertEqual(tc.suspicion_count(2, votes, speeches), 0)
        self.assertEqual(tc.trust_cue(3, votes, speeches), tc.TRUST_CALM)

    def test_runoff_votes_excluded(self):
        """결선 투표 표는 세지 않는다(PM 결정: 본 투표만)."""
        self.assertEqual(tc.suspicion_count(1, [vote(1, "mudang", "runoff"), vote(1, "mudang", "runoff", "boy")], []), 0)

    def test_other_targets_and_kinds_excluded(self):
        """다른 주민을 향한 표·의심, 무당을 향한 변호·모순 지적은 세지 않는다."""
        votes = [vote(1, "jumo")]
        speeches = [speech(1, CommandKind.SUSPECT, "jumo"), speech(1, CommandKind.DEFEND, "mudang"),
                    speech(1, CommandKind.POINT_CONTRADICTION, "mudang")]
        self.assertEqual(tc.suspicion_count(1, votes, speeches), 0)


class InAnnouncements(SimpleTestCase):
    """대사창: 그날 까치 발표(전신 컷이 있으면 그 뒤) 다음에 화자 없는 해설 줄."""

    def test_cue_follows_announcement_and_full_cut(self):
        """1일째 아침은 없고, 2일째는 발표·전신 컷 뒤. 화자·그림 없음."""
        view = view_with([disappearance(0, "gwabu", Place.WELL), disappearance(1, "hunjang", Place.HOME, Slot.EVENING)],
                         day=2)
        view.public.votes = [vote(1, "mudang"), vote(1, "mudang", "runoff", "yakcho")]
        ids = [line.line_id for line in announcement_lines(view)]
        self.assertEqual(ids, ["announcement-0", "announcement-0-full", "announcement-1", "announcement-1-full",
                               "trust-cue-2"])
        cue = announcement_lines(view)[-1]
        self.assertEqual((cue.day, cue.phase, cue.speaker, cue.sprite_id, cue.mascot, cue.full, cue.text),
                         (2, "morning", "", "", False, False, tc.TRUST_WARY))

    def test_cue_after_no_disappearance(self):
        """실종 없는 아침에도 발표 줄 뒤에 단다."""
        lines = announcement_lines(view_with([disappearance(0, "gwabu", Place.WELL)], day=2))
        self.assertEqual([line.line_id for line in lines][-2:], ["announcement-none-2", "trust-cue-2"])
        self.assertEqual(lines[-1].text, tc.TRUST_CALM)

    def test_rendered_as_narration(self):
        """HTML은 data-speaker="" 해설 줄(이름표 빈칸)."""
        line = announcement_lines(view_with([], day=2))[-1]
        html = render_to_string("meeting_parts/vn_line.html", {"line": line})
        self.assertIn('data-speaker=""', html)
        self.assertIn('data-line-id="trust-cue-2"', html)
        self.assertNotIn("data-mascot", html)
        self.assertNotIn("data-sprite", html)


class HelpLine(SimpleTestCase):
    """도움말에 신뢰 단서 안내 1줄."""

    def test_help_mentions_trust(self):
        html = render_to_string("ui_help.html", {})
        self.assertIn("주민들이 무당을 의심하면 투표에서 무당이 갇힐 수 있습니다. 아침마다 주민들의 분위기를 알려 드립니다.",
                      html)
