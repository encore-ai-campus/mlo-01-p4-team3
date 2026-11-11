"""직접 입력창 서버 설정(`PALDO_FREE_TEXT_INPUT`, 11/11 사용자 결정) 검증: 끄면 화면에 없고 요청도 받지 않는다."""
import json
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class FreeTextFlag(TestCase):
    """녹화 판 하나로 켬·끔 두 상태의 화면과 해석 요청을 대조한다."""

    def setUp(self):
        """독립 계정으로 판을 하나 시작한다."""
        user = User.objects.create_user(username="free-text-flag")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}),
                                    content_type="application/json", HTTP_ACCEPT="application/json")
        self.gid = response.json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    def pages(self):
        """전체 HTML과 htmx 조각 두 응답."""
        return [self.client.get(self.base, HTTP_ACCEPT="text/html"), self.client.get(self.base, HTTP_HX_REQUEST="true")]

    def test_off_hides_input_in_vn_and_classic(self):
        """끄면 VN·클래식 모두 입력창·예시 문구·제출 버튼을 그리지 않고 카드 조작은 남는다."""
        for mode in ("vn", "classic"):
            with self.subTest(mode=mode), override_settings(GAME_UI=mode, FREE_TEXT_INPUT=False):
                for page in self.pages():
                    html = page.content.decode()
                    self.assertEqual(page.status_code, 200)
                    self.assertIn('id="meeting-actions"', html)
                    self.assertNotIn('id="utterance-input"', html)
                    self.assertNotIn('name="text"', html)
                    self.assertNotIn("말로 행동하기", html)
                    self.assertNotIn("/utterances", html)

    def test_on_keeps_input(self):
        """켜면 지금과 같이 입력창이 있다."""
        for mode in ("vn", "classic"):
            with self.subTest(mode=mode), override_settings(GAME_UI=mode, FREE_TEXT_INPUT=True):
                for page in self.pages():
                    self.assertContains(page, 'id="utterance-input"')
                    self.assertContains(page, 'id="speech-cards"')

    @override_settings(FREE_TEXT_INPUT=False)
    def test_off_rejects_parse_and_resolve_without_calling_parser(self):
        """끄면 해석·카드 반응 요청은 403 `free_text_disabled`이고 해석기를 부르지 않는다."""
        form = urlencode({"game_id": self.gid, "state_version": "0", "text": "지켜보겠소"})
        with patch.object(self.service.interactions.parser, "parse") as parse:
            for headers in ({}, {"HTTP_HX_REQUEST": "true"}):
                response = self.client.post(self.base + "/utterances", form,
                                            content_type="application/x-www-form-urlencoded", **headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json(), {"error": "free_text_disabled"})
            body = json.dumps({"game_id": self.gid, "state_version": 0, "text": "지켜보겠소"})
            response = self.client.post(self.base + "/utterances", body, content_type="application/json")
            self.assertEqual(response.status_code, 403)
            parse.assert_not_called()
        response = self.client.post(self.base + "/utterances/a1", urlencode({"game_id": self.gid, "attempt_id": "a1", "reaction": "cancel"}),
                                    content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "free_text_disabled"})

    @override_settings(FREE_TEXT_INPUT=False)
    def test_off_still_requires_login(self):
        """끈 상태에서도 로그인하지 않은 요청은 기존처럼 인증 단계에서 막힌다(설정이 인증보다 먼저 답하지 않는다)."""
        self.client.logout()
        response = self.client.post(self.base + "/utterances", "{}", content_type="application/json")
        self.assertNotEqual(response.content, b'{"error": "free_text_disabled"}')
        self.assertIn(response.status_code, (302, 401, 403))
