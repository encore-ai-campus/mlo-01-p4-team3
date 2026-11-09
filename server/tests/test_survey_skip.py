"""B-09-07 9번: 끝난 판마다 설문 이벤트(응답·건너뜀) 정확히 1건, 비콘·다음 요청 보완·멱등."""
import json
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase

from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, issue_test_csrf, make_service


class SurveySkip(TestCase):
    """녹화 판을 끝까지 진행한 뒤 설문 이벤트 수를 센다(CSRF 검사 켬)."""

    def setUp(self):
        """CSRF를 검사하는 참가자와 끝난 판 하나."""
        user = User.objects.create_user(username="survey-skip")
        install_test_profiles(self)(user)
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

    def beacon(self, gid=None):
        """브라우저 sendBeacon과 같은 폼 본문(헤더 없이 본문 CSRF 토큰)."""
        gid = gid or self.gid
        return self.client.post(f"/games/{gid}/survey/leave", urlencode({"csrfmiddlewaretoken": self.token,
                                "game_id": gid}), content_type="application/x-www-form-urlencoded")

    def answer(self):
        """설문 응답."""
        return self.client.post(self.url + "/survey", json.dumps({"game_id": self.gid, "fun": 4, "unfair": 2,
                                "npc_plausible": 3, "comment": ""}), content_type="application/json",
                                HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)

    def test_answer_then_leave_adds_nothing(self):
        """응답 뒤에는 비콘·홈 보완이 추가 이벤트를 남기지 않는다."""
        self.assertEqual(self.answer().status_code, 204)
        self.assertEqual(self.beacon().status_code, 204)
        self.client.get("/")
        self.assertEqual([p["skipped"] for p in self.surveys()], [False])

    def test_beacon_records_one_skip(self):
        """비콘은 건너뜀 1건(reason=beacon), 다시 보내도 1건."""
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        self.assertContains(page, f'data-leave-url="/games/{self.gid}/survey/leave"')
        self.assertEqual(self.beacon().status_code, 204)
        self.assertEqual(self.beacon().status_code, 204)
        self.assertEqual(self.surveys(), [{"skipped": True, "fun": None, "unfair": None, "npc_plausible": None,
                                           "comment": "", "mask_version": "b-mask-1", "reason": "beacon"}])
        self.assertNotContains(self.client.get(self.url, HTTP_ACCEPT="text/html"), "data-leave-url")

    def test_failed_beacon_is_backfilled_on_next_request(self):
        """비콘이 안 가도 다음 요청(홈: 회차 복귀·새 판 시작 폼)에서 1건 채우고, 다시 와도 1건."""
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual([p["reason"] for p in self.surveys()], ["backfill"])
        self.start()
        self.client.get("/")
        self.assertEqual(len(self.surveys()), 1)

    def test_beacon_and_backfill_overlap_once(self):
        """비콘과 다음 요청 보완이 겹쳐도 1건, 그 뒤 같은 건너뜀은 204·응답은 409."""
        self.beacon()
        self.client.get("/")
        self.start()
        self.assertEqual([p["reason"] for p in self.surveys()], ["beacon"])
        skip = self.client.post(self.url + "/survey", json.dumps({"game_id": self.gid, "skipped": True, "fun": None,
                                "unfair": None, "npc_plausible": None, "comment": ""}), content_type="application/json",
                                HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(skip.status_code, 204)
        self.assertEqual(self.answer().status_code, 409)
        self.assertEqual(len(self.surveys()), 1)

    def test_unfinished_game_and_bad_beacon(self):
        """진행 중인 판은 채우지 않고, 다른 판 ID·모르는 필드·CSRF 없는 비콘은 거절한다."""
        other = self.start()
        self.assertEqual(self.beacon(other).status_code, 204)
        self.client.get("/")
        self.assertEqual(self.surveys(other), [])
        bad = self.client.post(self.url + "/survey/leave", urlencode({"csrfmiddlewaretoken": self.token,
                               "game_id": other}), content_type="application/x-www-form-urlencoded")
        self.assertEqual(bad.status_code, 400)
        extra = self.client.post(self.url + "/survey/leave", urlencode({"csrfmiddlewaretoken": self.token,
                                 "game_id": self.gid, "fun": "5"}), content_type="application/x-www-form-urlencoded")
        self.assertEqual(extra.status_code, 400)
        no_csrf = self.client.post(self.url + "/survey/leave", urlencode({"game_id": self.gid}),
                                   content_type="application/x-www-form-urlencoded")
        self.assertEqual(no_csrf.status_code, 403)

    def test_backfill_failure_does_not_block_home(self):
        """보완 저장이 안 되면 홈은 그대로 열리고 다음 요청에서 채운다."""
        from server.app.interactions import InteractionUnavailable
        with patch.object(self.service.interactions, "backfill_skipped_surveys", side_effect=InteractionUnavailable):
            self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.surveys(), [])
        self.client.get("/")
        self.assertEqual(len(self.surveys()), 1)
