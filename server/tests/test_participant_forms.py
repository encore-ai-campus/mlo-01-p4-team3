"""B-07-02 화면 입력부터 수첩·확신도·설문 아웃박스까지 검증한다."""
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase
from engine.domain.engine import PaldoEngine
from engine.domain.model import PLAYER_ID, Phase
from tests.engine.test_scenarios_engine import ScriptedPolicy, drive_to, game_base6

from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, issue_test_csrf, make_service


class ParticipantFormsTests(TestCase):
    """실제 Django 경로, 녹화 엔진, 메모리 아웃박스의 계약 검증."""

    def setUp(self):
        """CSRF를 검사하는 참가자와 독립된 판을 만든다."""
        self.register = install_test_profiles(self)
        self.user = User.objects.create_user(username="participant-forms")
        self.register(self.user)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.token = issue_test_csrf(self.client)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session",
                                   "mudang_variant": "f"}), content_type="application/json",
                                   HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.gid = response.json()["view"]["game_id"]
        self.url = f"/games/{self.gid}"

    def form(self, method, suffix, body, client=None, csrf=True):
        """htmx가 보내는 URL 인코딩 폼과 CSRF 헤더를 사용한다."""
        return (client or self.client).generic(method, self.url + suffix, urlencode(body),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true",
            **({"HTTP_X_CSRFTOKEN": self.token} if csrf else {}))

    def survey_json(self, body):
        """같은 설문 API에 JSON 응답 또는 건너뛰기를 보낸다."""
        return self.client.post(self.url + "/survey", json.dumps({"game_id": self.gid, **body}),
                                content_type="application/json", HTTP_ACCEPT="application/json",
                                HTTP_X_CSRFTOKEN=self.token)

    def view(self):
        """플레이어 공개 상태를 반환한다."""
        return self.client.get(self.url, HTTP_ACCEPT="application/json").json()["view"]

    def play_until(self, phase=None):
        """녹화된 명령을 투표 단계 또는 종료까지 실제 API로 보낸다."""
        for step in self.service.engine.steps[self.view()["state_version"] + 1:]:
            if phase and self.view()["phase"] == phase:
                break
            data = dict(step["request"], game_id=self.gid, command_id=str(uuid4()))
            response = self.client.post(self.url + "/commands", json.dumps(data), content_type="application/json",
                                       HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
            self.assertEqual(response.status_code, 200)

    def test_notebook_and_pre_vote_confidence_forms_restore_values(self):
        """주민별 0~100 의심도를 복원하고 투표 직전 확신도를 별도로 기록한다."""
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        npc = next(name for name in self.view()["names"] if name != "mudang")
        self.assertContains(page, f'name="entry:{npc}"')
        self.assertNotContains(page, 'name="entry:mudang"')
        self.assertContains(page, 'x-model="value"')
        self.assertContains(page, "/static/js/vendor/alpine.min.js")
        version = self.view()["state_version"]
        body = {"game_id": self.gid, f"entry:{npc}": 73, "vote_confidence": ""}
        for _ in range(2):
            self.assertEqual(self.form("PUT", "/notebook", body).status_code, 204)
        self.assertEqual(self.view()["state_version"], version)
        self.assertEqual(self.view()["player"]["notebook"][npc], 73)
        self.play_until("vote")
        self.assertContains(self.client.get(self.url, HTTP_ACCEPT="text/html"), "투표 직전 확신도")
        body["vote_confidence"] = 88
        self.assertEqual(self.form("PUT", "/notebook", body).status_code, 204)
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        self.assertContains(page, 'name="vote_confidence" min="0" max="100" value="88"')
        events = [event for event in self.service.repository.unpublished(self.gid)
                  if event.event_type == "notebook_updated"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].payload["vote_confidence"], None)
        self.assertEqual(events[1].payload["vote_confidence"], 88)
        self.assertTrue(all(event.topic == "paldo.player" for event in events))

    def test_survey_matches_final_questions_masking_and_htmx_completion(self):
        """확정 문항·선택 자유 의견을 표시하고 완료 조각과 중복 방지를 확인한다."""
        self.assertNotContains(self.client.get(self.url, HTTP_ACCEPT="text/html"), 'id="survey-title"')
        self.play_until()
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        for question in ("이번 판은 재미있었나요?", "이번 판의 결과가 억울하게 느껴졌나요?",
                         "마을 사람들의 말과 행동이 그럴듯했나요?"):
            self.assertContains(page, question)
        body = {"game_id": self.gid, "fun": 5, "unfair": 2, "npc_plausible": 4,
                "comment": "person@example.com 010-1234-5678"}
        self.assertContains(self.form("POST", "/survey", body), "설문을 기록했습니다")
        replay = self.form("POST", "/survey", body)
        self.assertEqual(replay.status_code, 204)
        self.assertEqual(replay.headers["HX-Refresh"], "true")
        self.assertEqual(self.survey_json(dict(body, skipped=False)).status_code, 204)
        self.assertEqual(self.form("POST", "/survey", dict(body, fun=3)).status_code, 409)
        self.assertEqual(self.survey_json(dict(body, comment="other@example.com 010-9999-9999")).status_code, 409)
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        self.assertContains(page, "설문을 기록했습니다")
        self.assertNotContains(page, 'name="fun"')
        events = [event for event in self.service.repository.unpublished(self.gid)
                  if event.event_type == "survey_submitted"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].topic, "paldo.player")
        self.assertEqual(events[0].payload, {"skipped": False, "fun": 5, "unfair": 2, "npc_plausible": 4,
                                             "comment": "<MASK> <MASK>", "mask_version": "b-mask-1"})
        self.assertNotIn("person@example.com", repr(self.service.interactions.store.records[self.gid].survey))

    def _save_confidence_once_per_vote(self):
        """새 투표 저장은 1건, 같은 투표의 동일 재전송은 추가 0건이어야 한다."""
        view = self.view()
        npc = next(name for name in view["names"] if name != "mudang")
        body = {"game_id": self.gid, f"entry:{npc}": 73, "vote_confidence": 88}
        def events():
            return [event for event in self.service.repository.unpublished(self.gid)
                    if event.event_type == "notebook_updated"]
        before = len(events())
        for _ in range(2):
            self.assertEqual(self.form("PUT", "/notebook", body).status_code, 204)
            self.assertEqual(len(events()), before + 1)
        event = events()[-1]
        self.assertEqual((event.day, event.phase), (view["day"], view["phase"]))
        self.assertEqual(event.topic, "paldo.player")
        self.assertEqual(event.payload, {"entries": {npc: 73}, "vote_confidence": 88})
        self.assertEqual(self.view()["state_version"], view["state_version"])
        self.assertContains(self.client.get(self.url, HTTP_ACCEPT="text/html"),
                            'name="vote_confidence" min="0" max="100" value="88"')

    def test_same_confidence_is_recorded_again_on_next_day(self):
        """다음 날 같은 값도 새 투표의 기록으로 남긴다."""
        days = []
        for step in self.service.engine.steps[1:]:
            view = self.view()
            if view["phase"] == "vote":
                self._save_confidence_once_per_vote()
                days.append(view["day"])
            response = self.client.post(self.url + "/commands", json.dumps(dict(step["request"],
                game_id=self.gid, command_id=str(uuid4()))), content_type="application/json",
                HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(days, [1, 2])

    def test_same_confidence_is_recorded_again_in_real_engine_runoff(self):
        """실제 엔진의 본투표→결선에서도 같은 값을 각각 기록한다."""
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / "fixtures/scenarios/runoff_tie_no_lock.json").read_text(encoding="utf-8"))
        ballots = fixture["checks"][1]["input"]
        engine = PaldoEngine(ScriptedPolicy({(1, name): {v: t for v, t in votes.items() if v != PLAYER_ID}
                                            for name, votes in ballots.items()}))
        state, truth = game_base6(engine)
        state, truth = drive_to(self, engine, state, truth, Phase.VOTE)
        profile = self.user.paldo_profile
        state = replace(state, meta=replace(state.meta, player_pseudonym=str(profile.player_pseudonym)))
        self.service.engine = engine
        self.service.repository.create(state, truth, [])
        self.gid, self.url = state.meta.game_id, f"/games/{state.meta.game_id}"
        self._save_confidence_once_per_vote()
        response = self.client.post(self.url + "/commands", json.dumps({"game_id": self.gid,
            "command_id": str(uuid4()), "expected_state_version": state.state_version,
            "command": {"kind": "vote", "actor": PLAYER_ID, "target": ballots["main"][PLAYER_ID]}}),
            content_type="application/json", HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.view()["phase"], "runoff")
        self._save_confidence_once_per_vote()

    def test_skip_survey_form_and_json_replay(self):
        """건너뛰기는 세 null 점수와 한 이벤트를 남기고 나중 응답을 거부한다."""
        skip = {"skipped": True, "fun": None, "unfair": None, "npc_plausible": None, "comment": ""}
        self.assertEqual(self.survey_json(skip).status_code, 409)
        self.play_until()
        version = self.view()["state_version"]
        page = self.client.get(self.url, HTTP_ACCEPT="text/html")
        self.assertContains(page, 'name="skipped" value="true"')
        self.assertContains(page, "건너뛰기")
        repository = self.service.repository
        with patch.object(repository, "append_events", wraps=repository.append_events) as append:
            self.assertContains(self.form("POST", "/survey", {"game_id": self.gid, "skipped": "true"}),
                                "설문을 기록했습니다")
            self.assertEqual(self.survey_json(skip).status_code, 204)
            self.assertEqual(self.survey_json({"fun": 5, "unfair": 1, "npc_plausible": 4}).status_code, 409)
            self.assertEqual(append.call_count, 1)
        events = [event for event in repository.unpublished(self.gid) if event.event_type == "survey_submitted"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].topic, "paldo.player")
        self.assertEqual(events[0].payload, {**skip, "mask_version": "b-mask-1"})
        self.assertEqual(self.view()["state_version"], version)
        self.assertNotContains(self.client.get(self.url, HTTP_ACCEPT="text/html"), 'name="skipped"')

    def test_survey_rejects_mixed_skip_and_response_inputs(self):
        """점수 일부·잘못된 불리언·건너뛸 때 남은 점수나 의견은 400이다."""
        self.play_until()
        scores = {"fun": 5, "unfair": 2, "npc_plausible": 4}
        null_scores = dict.fromkeys(scores)
        for body in ({"fun": 5, "unfair": 2}, dict(scores, fun=None), dict(scores, fun=True),
                     dict(scores, skipped=1), dict(scores, skipped="false"),
                     dict(scores, skipped=True), dict(null_scores, skipped=True, comment=" "),
                     {"skipped": True}, dict(null_scores, skipped=False),
                     dict(scores, comment="a" * 4001)):
            with self.subTest(fields=list(body)):
                self.assertEqual(self.survey_json(body).status_code, 400)
        for form in ({"skipped": "yes"}, {"skipped": "true", "fun": "5"},
                     {"skipped": "true", "comment": "의견"}):
            with self.subTest(form=form):
                self.assertEqual(self.form("POST", "/survey", {"game_id": self.gid, **form}).status_code, 400)
        self.assertFalse(any(event.event_type == "survey_submitted"
                             for event in self.service.repository.unpublished(self.gid)))

    def test_form_validation_ownership_and_csrf(self):
        """폼도 JSON과 같은 범위·경로·소유권·CSRF 검사를 받는다."""
        body = {"game_id": self.gid, "entry:bogus": 50}
        self.assertEqual(self.form("PUT", "/notebook", body).status_code, 400)
        self.assertEqual(self.form("PUT", "/notebook", {"game_id": self.gid, "vote_confidence": 101}).status_code, 400)
        self.assertEqual(self.form("PUT", "/notebook", {"game_id": "foreign"}).status_code, 400)
        self.assertEqual(self.form("PUT", "/notebook", {"game_id": self.gid, "admin": 1}).status_code, 400)
        self.assertEqual(self.form("PUT", "/notebook", body, csrf=False).status_code, 403)
        other = User.objects.create_user(username="participant-other")
        self.register(other)
        stranger = Client()
        stranger.force_login(other)
        for method, path in (("PUT", "/notebook"), ("POST", "/survey")):
            self.assertEqual(self.form(method, path, body, client=stranger).status_code, 404)
        self.play_until()
        self.assertEqual(self.form("POST", "/survey", {"game_id": self.gid, "fun": 5}).status_code, 400)
        self.assertEqual(self.form("POST", "/survey", {"game_id": self.gid, "fun": 0, "unfair": 2,
                                                       "npc_plausible": 3}).status_code, 400)

    def test_three_player_records_use_player_topic_without_state_consumption(self):
        """수첩·투표 확신도·설문은 세 아웃박스 기록이며 버전을 소비하지 않는다."""
        npc = next(name for name in self.view()["names"] if name != "mudang")
        body = {"game_id": self.gid, f"entry:{npc}": 20}
        self.assertEqual(self.form("PUT", "/notebook", body).status_code, 204)
        self.play_until("vote")
        self.assertEqual(self.form("PUT", "/notebook", dict(body, vote_confidence=95)).status_code, 204)
        self.play_until()
        version = self.view()["state_version"]
        self.assertEqual(self.form("POST", "/survey", {"game_id": self.gid, "fun": 5,
                         "unfair": 1, "npc_plausible": 4, "comment": ""}).status_code, 200)
        self.assertEqual(self.view()["state_version"], version)
        events = [event for event in self.service.repository.unpublished(self.gid) if event.topic == "paldo.player"]
        self.assertEqual([event.event_type for event in events], ["notebook_updated", "notebook_updated", "survey_submitted"])
