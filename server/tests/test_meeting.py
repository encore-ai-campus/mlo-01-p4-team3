"""회의 화면의 공개 범위와 htmx 명령 흐름을 검사한다."""
from html.parser import HTMLParser
import json
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase

from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class _MeetingForms(HTMLParser):
    """렌더된 폼과 입력 속성을 읽는다."""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        """폼과 폼 안 입력의 이름·값을 기록한다."""
        if tag == "form":
            self.current = (dict(attrs), [])
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current[1].append(dict(attrs))

    def handle_endtag(self, tag):
        """닫힌 폼의 범위를 기록에서 끝낸다."""
        if tag == "form":
            self.current = None


class Meeting(TestCase):
    """Django 라우트와 실제 HTML에서 회의 화면 계약을 검증한다."""

    def setUp(self):
        """계정 프로필과 게임 포트를 시험 전용으로 조립한다."""
        user = User.objects.create_user(username="meeting-player")
        install_test_profiles(self)(user)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.recording = service.engine.steps
        csrf_request = RequestFactory().get("/")
        self.csrf = {"HTTP_X_CSRFTOKEN": get_token(csrf_request)}
        self.client.cookies["csrftoken"] = csrf_request.META["CSRF_COOKIE"]
        response = self.client.post(
            "/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "m"},
            content_type="application/json", HTTP_HX_REQUEST="true", **self.csrf,
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")
        self.assertTemplateUsed(response, "meeting.html")
        self.game_id = response.context["vm"].game_id
        self.game_url = f"/games/{self.game_id}"
        self.command_url = self.game_url + "/commands"

    def _form_body(self, recorded, command_id=None):
        command = recorded["command"]
        body = {field: command[field] for field in ("kind", "target", "addressee", "night", "slot",
                                                     "interrogation_kind") if command.get(field) is not None}
        body.update(game_id=self.game_id, command_id=command_id or str(uuid4()),
                    state_version=recorded["expected_state_version"],
                    evidence=[f"{ref['type']}:{ref['record_id']}" for ref in command["evidence"]])
        if command["divination"]:
            item = command["divination"]
            body["divination"] = f"{item['night']}:{item['target']}:{item['result']}"
        return body

    def _post_form(self, body, *, csrf=True):
        # htmx 기본 폼은 URL 인코딩을 사용한다.
        return self.client.post(self.command_url, urlencode(body, doseq=True),
                                content_type="application/x-www-form-urlencoded",
                                HTTP_HX_REQUEST="true", **(self.csrf if csrf else {}))

    def test_recording_thirteen_public_html_states_and_forms(self):
        """녹화 13단계의 전체 화면과 htmx 조각에서 금지 정보와 버전 필드를 확인한다."""
        self.assertEqual(len(self.recording), 13)
        initial = self.client.get(self.game_url, HTTP_HX_REQUEST="true")
        self.assertContains(initial, "/static/assets/portrait/blacksmith.png")
        for index, step in enumerate(self.recording):
            with self.subTest(state_version=index):
                if index:
                    fragment = self._post_form(self._form_body(step["request"]))
                else:
                    fragment = initial
                self.assertNotContains(fragment, "<html")
                self.assertContains(fragment, 'id="meeting"')
                self.assertTemplateUsed(fragment, "meeting.html")
                whole = self.client.get(self.game_url, HTTP_ACCEPT="text/html")
                self.assertContains(whole, '<html lang="ko">')
                self.assertTemplateUsed(whole, "game.html")
                for response in (fragment, whole):
                    self.assertContains(response, f'data-state-version="{index}"')
                    html = response.content.decode()
                    for forbidden in ("claim_truths", "npc_states", "beliefs", "private", "night_plans",
                                      "secret_facts", "player_pseudonym", "hidden_visits"):
                        self.assertNotIn(forbidden, html)
                    self.assertNotIn("view", response.context)
                    forms = _MeetingForms(html).forms
                    if index < len(self.recording) - 1:
                        self.assertTrue(forms)
                    for attrs, inputs in forms:
                        self.assertEqual(attrs["hx-target"], "#meeting")
                        self.assertEqual(attrs["hx-post"], self.command_url)
                        versions = [entry for entry in inputs if entry.get("name") == "state_version"]
                        self.assertEqual(versions, [{"type": "hidden", "name": "state_version", "value": str(index)}])
        self.assertContains(whole, "판이 종료됐습니다.")

    def test_replay_and_conflict_replace_with_current_html(self):
        """CSRF·재전송·거절·409에서 HTML과 기존 JSON 계약을 함께 확인한다."""
        first = self._form_body(self.recording[1]["request"])
        self.assertEqual(self._post_form(first, csrf=False).status_code, 403)
        self.assertContains(self._post_form(first), 'data-state-version="1"')
        second = self._form_body(self.recording[2]["request"])
        self.assertContains(self._post_form(second), 'data-state-version="2"')
        self.assertContains(self._post_form(first), 'data-state-version="2"')
        conflict = self._post_form(dict(first, command_id=str(uuid4())))
        self.assertContains(conflict, 'data-state-version="2"', status_code=409)
        self.assertContains(conflict, 'role="alert"', status_code=409)
        rejected = self._post_form(dict(first, command_id=str(uuid4()), state_version=2, kind="explain"))
        self.assertContains(rejected, 'data-state-version="2"', status_code=422)
        self.assertEqual(self._post_form(dict(first, private="unexpected")).status_code, 400)
        payload = dict(self.recording[1]["request"], game_id=self.game_id, command_id=first["command_id"])
        replay = self.client.post(self.command_url, json.dumps(payload), content_type="application/json", **self.csrf)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["view"]["state_version"], 1)
