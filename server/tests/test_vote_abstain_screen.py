"""투표 기권(rule-0.9, 11/11 사용자 결정 ②안) 화면: 기권 버튼, 공개 기록의 기권 표시, 폼 → 명령 변환."""
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.http import QueryDict

from engine.domain.model import PLAYER_ID, CommandKind, Phase, PlayerState, PublicRecord, VoteRecord
from engine.ports import PlayerView
from server.api.meeting_view import meeting_context
from server.api.views import _meeting_command_body

NAMES = {"a": "훈장", "b": "과부", "c": "포졸", PLAYER_ID: PLAYER_ID}


def vote_view(kinds, votes=()):
    """투표 단계 가짜 화면 정보(합법 행동 kinds)."""
    return PlayerView("g1", 5, 1, Phase.VOTE, [PLAYER_ID, "a", "b", "c"], [], [], PublicRecord(votes=list(votes)),
                      PlayerState(), list(kinds), PLAYER_ID, [], None, dict(NAMES))


class AbstainScreen(SimpleTestCase):
    def actions(self, view):
        """행동 선택 조각(VN·classic 공통 `meeting_actions.html`)을 그린다."""
        return render_to_string("meeting_actions.html", meeting_context(view))

    def test_abstain_button_only_when_legal(self):
        """엔진이 기권을 합법 행동으로 줄 때(rule-0.9 투표·결선)만 버튼이 나온다. 대상 칸은 없다."""
        html = self.actions(vote_view([CommandKind.VOTE, CommandKind.ABSTAIN]))
        self.assertIn("이번엔 투표하지 않기", html)
        self.assertIn('name="kind" value="abstain"', html)
        self.assertNotIn("이번엔 투표하지 않기", self.actions(vote_view([CommandKind.VOTE])))

    def test_records_show_abstain(self):
        """공개된 투표 기록에서 기권한 표는 '표 안 던짐'으로 보인다."""
        view = vote_view([CommandKind.VOTE], [VoteRecord("v1_0", 1, "main", PLAYER_ID, None),
                                              VoteRecord("v1_1", 1, "main", "a", "b")])
        html = render_to_string("meeting_parts/records.html", meeting_context(view))
        self.assertIn("표 안 던짐", html)
        self.assertIn("과부", html)

    def test_form_to_command(self):
        """기권 폼은 대상 없이 kind=abstain 명령이 된다."""
        form = QueryDict(mutable=True)
        form.update({"game_id": "g1", "command_id": "c1", "state_version": "5", "kind": "abstain"})
        body = _meeting_command_body(form)
        self.assertEqual(body["command"]["kind"], "abstain")
        self.assertNotIn("target", body["command"])


class RuleBaseSetting(SimpleTestCase):
    """새 일반 판 규칙은 `PALDO_RULE_BASE` 설정 하나로 고른다. 비우면 운영 기본 rule-0.8."""

    def versions(self, base):
        from django.test import override_settings
        from server.paldo_web.composition import open_start_versions
        with override_settings(ACTIVE_RULE_BASE=base, ACTIVE_CONTENT_VERSION="content-1.0.1"):
            return open_start_versions()

    def test_default_and_abstain(self):
        from engine.domain import params
        self.assertEqual(self.versions("").rule_version, params.RULE_PARAMS_VERSION)
        self.assertTrue(self.versions("rule-0.9").rule_version.startswith("rule-0.9."))

    def test_unknown_blocks_start(self):
        from server.app.usecases import StartUnavailable
        with self.assertRaises(StartUnavailable):
            self.versions("rule-7.0")
