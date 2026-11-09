"""B-09-07 9번: 끝난 판마다 설문 이벤트(응답·건너뜀) 정확히 1건, 다음 홈 요청 보완·스냅숏 전 일괄 보완·멱등."""
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase

from server.accounts.models import AccountProfile

from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, issue_test_csrf, make_service


class SurveySkip(TestCase):
    """녹화 판을 끝까지 진행한 뒤 설문 이벤트 수를 센다(CSRF 검사 켬)."""

    def setUp(self):
        """CSRF를 검사하는 참가자와 끝난 판 하나."""
        user = User.objects.create_user(username="survey-skip")
        profile = install_test_profiles(self)(user)
        AccountProfile.objects.create(user=user, player_pseudonym=profile.player_pseudonym)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        self.token = issue_test_csrf(self.client)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.gid = self.start()
        self.url = f"/games/{self.gid}"
        for step in self.service.engine.steps[1:]:
            response = self.client.post(self.url + "/commands", json.dumps(dict(step["request"], game_id=self.gid,
                                        command_id=str(uuid4()))), content_type="application/json",
                                        HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
            self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.service.repository.load(self.gid)[0].result)

    def start(self):
        """녹화 판 하나를 시작한다."""
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session",
                                    "mudang_variant": "f"}), content_type="application/json",
                                    HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
        return response.json()["view"]["game_id"]

    def surveys(self, gid=None):
        """해당 판의 설문 이벤트 본문 목록."""
        return [e.payload for e in self.service.repository.unpublished(gid or self.gid)
                if e.event_type == "survey_submitted"]

    def answer(self):
        """설문 응답."""
        return self.client.post(self.url + "/survey", json.dumps({"game_id": self.gid, "fun": 4, "unfair": 2,
                                "npc_plausible": 3, "comment": ""}), content_type="application/json",
                                HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)

    def batch(self):
        """스냅숏 전 일괄 보완 관리 명령."""
        out = StringIO()
        call_command("backfill_surveys", stdout=out)
        return out.getvalue()

    def test_answer_then_backfills_add_nothing(self):
        """응답 뒤에는 홈 보완·일괄 보완이 추가 이벤트를 남기지 않는다."""
        self.assertEqual(self.answer().status_code, 204)
        self.client.get("/")
        self.assertIn("건너뜀으로 채운 판 0개", self.batch())
        self.assertEqual([p["skipped"] for p in self.surveys()], [False])

    def test_reload_does_not_skip(self):
        """결과 화면을 다시 열어도(새로 고침) 건너뜀이 생기지 않고 설문에 답할 수 있다. 화면 스크립트에 떠남 비콘이 없다."""
        for _ in range(2):
            page = self.client.get(self.url, HTTP_ACCEPT="text/html")
            self.assertContains(page, 'id="survey"')
            self.assertNotContains(page, "data-leave-url")
        self.assertEqual(self.surveys(), [])
        self.assertEqual(self.answer().status_code, 204)
        self.assertEqual([p["skipped"] for p in self.surveys()], [False])
        script = Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "game.js"
        self.assertNotIn("sendBeacon", script.read_text(encoding="utf-8"))
        self.assertEqual(self.client.post(self.url + "/survey/leave").status_code, 404)

    def test_home_backfill_once(self):
        """설문 없이 나간 판은 다음 홈 요청(회차 복귀·새 판 시작 폼)에서 1건 채우고, 다시 와도 1건."""
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.surveys(), [{"skipped": True, "fun": None, "unfair": None, "npc_plausible": None,
                                           "comment": "", "mask_version": "b-mask-1", "reason": "backfill"}])
        self.start()
        self.client.get("/")
        self.assertEqual(len(self.surveys()), 1)

    def test_home_and_batch_overlap_once(self):
        """홈 보완과 일괄 보완이 겹쳐도 1건, 그 뒤 같은 건너뜀은 204·응답은 409."""
        self.client.get("/")
        self.assertIn("건너뜀으로 채운 판 0개", self.batch())
        self.assertEqual([p["reason"] for p in self.surveys()], ["backfill"])
        skip = self.client.post(self.url + "/survey", json.dumps({"game_id": self.gid, "skipped": True, "fun": None,
                                "unfair": None, "npc_plausible": None, "comment": ""}), content_type="application/json",
                                HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(skip.status_code, 204)
        self.assertEqual(self.answer().status_code, 409)
        self.assertEqual(len(self.surveys()), 1)

    def test_batch_fills_participant_who_never_returns(self):
        """다시 오지 않은 참가자의 끝난 판은 일괄 보완이 1건 채우고, 다시 실행해도 1건. 진행 중인 판은 채우지 않는다."""
        other = self.start()
        out = self.batch()
        self.assertIn("참가자 1명, 건너뜀으로 채운 판 1개", out)
        self.assertNotIn(str(AccountProfile.objects.get().player_pseudonym), out)
        self.batch()
        self.assertEqual([p["reason"] for p in self.surveys()], ["backfill"])
        self.assertEqual(self.surveys(other), [])

    def test_backfill_failure_does_not_block_home(self):
        """보완 저장이 안 되면 홈은 그대로 열리고 다음 요청에서 채운다."""
        from server.app.interactions import InteractionUnavailable
        with patch.object(self.service.interactions, "backfill_skipped_surveys", side_effect=InteractionUnavailable):
            self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.surveys(), [])
        self.client.get("/")
        self.assertEqual(len(self.surveys()), 1)
