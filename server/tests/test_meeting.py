"""회의 화면의 공개 범위와 htmx 명령 흐름을 검사한다."""
from dataclasses import replace
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase

from engine.domain.engine import PaldoEngine
from engine.domain.model import PLAYER_ID, CommandKind, DivinationResult, NightPlan, Phase
from server.paldo_web.composition import configure_game_service
from tests.engine.test_night import NightPolicy, to_night_yakcho
from tests.engine.test_scenarios_engine import DAY1_LOCK_YAKCHO, ScriptedPolicy, drive_to, game_base6, step
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

    def targets_for(self, kind):
        """대상 버튼 폼의 숨은 대상 값을 읽는다."""
        targets = []
        for attrs, inputs in self.forms:
            if "target-form" not in attrs.get("class", "").split():
                continue
            fields = {entry.get("name"): entry.get("value") for entry in inputs if entry.get("type") == "hidden"}
            if fields.get("kind") == kind:
                targets.append(fields["target"])
        return targets


class Meeting(TestCase):
    """Django 라우트와 실제 HTML에서 회의 화면 계약을 검증한다."""

    def setUp(self):
        """계정 프로필과 게임 포트를 시험 전용으로 조립한다."""
        user = User.objects.create_user(username="meeting-player")
        self.player = str(install_test_profiles(self)(user).player_pseudonym)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.recording = self.service.engine.steps
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
                                      "secret_facts", "player_pseudonym", "hidden_visits", "pending_votes"):
                        self.assertNotIn(forbidden, html)
                    self.assertNotIn("view", response.context)
                    forms = _MeetingForms(html)
                    if index < len(self.recording) - 1:
                        self.assertTrue(forms.forms)
                    for attrs, inputs in forms.forms:
                        self.assertEqual(attrs["hx-target"], "#meeting")
                        self.assertEqual(attrs["hx-post"], self.command_url)
                        versions = [entry for entry in inputs if entry.get("name") == "state_version"]
                        self.assertEqual(versions, [{"type": "hidden", "name": "state_version", "value": str(index)}])
                    view = step["view"]
                    self.assertEqual(html.count("data-vote-round="), len(view["public"]["votes"]))
                    if view["phase"] == "vote":
                        self.assertContains(response, "이번 투표는 비공개이며")
                        self.assertCountEqual(forms.targets_for("vote"), set(view["active"]) - {PLAYER_ID})
                    if view["phase"] == "night":
                        self.assertContains(response, "점칠 대상")
                        self.assertCountEqual(forms.targets_for("divine"), set(view["active"]) - {PLAYER_ID})
                        self.assertContains(response, "대장장이 점치기")
                        self.assertNotContains(response, "약초꾼 점치기")
                    if index == 0:
                        self.assertContains(response, "진술표")
                        self.assertContains(response, "훈장·주모을(를) 봤다")
                        self.assertNotContains(response, 'id="published-locks"')
                    if index == 6:
                        self.assertRegex(html, r"약초꾼 감금\s*· 금줄 반응으로 씐 자가 아님이 밝혀졌습니다")
                    if index == 7:
                        self.assertContains(response, "1일째 밤 · 대장장이 · 탁함")
                    if index == 12:
                        self.assertRegex(html, r"대장장이 감금\s*· 금줄 반응으로 씐 자임이 밝혀졌습니다")
        self.assertContains(whole, "판이 종료됐습니다.")

    def test_real_engine_runoff_hides_current_ballots_and_limits_candidates(self):
        """실제 엔진의 본투표와 결선 재동점을 htmx 요청으로 확인한다."""
        root = Path(__file__).resolve().parents[2]
        fixture = json.loads((root / "fixtures/scenarios/runoff_tie_no_lock.json").read_text(encoding="utf-8"))
        ballots = fixture["checks"][1]["input"]
        policy = ScriptedPolicy({
            (1, round_name): {voter: target for voter, target in votes.items() if voter != PLAYER_ID}
            for round_name, votes in ballots.items()
        })
        engine = PaldoEngine(policy)
        state, truth = game_base6(engine)
        state, truth = drive_to(self, engine, state, truth, Phase.VOTE)
        state = replace(state, meta=replace(state.meta, player_pseudonym=self.player))
        self.service.engine = engine
        self.service.repository.create(state, truth, [])
        self.command_url = f"/games/{state.meta.game_id}/commands"
        before = self.client.get(f"/games/{state.meta.game_id}", HTTP_HX_REQUEST="true")
        self.assertTrue(state.pending_votes)
        self.assertContains(before, "이번 투표는 비공개이며")
        self.assertNotContains(before, 'id="published-votes"')
        self.assertNotContains(before, "pending_votes")
        self.assertCountEqual(_MeetingForms(before.content.decode()).targets_for("vote"),
                              set(state.active) - {PLAYER_ID})
        self.assertNotContains(before, '<select name="target"')

        def vote(version, target):
            return self._post_form({"game_id": state.meta.game_id, "kind": "vote", "target": target,
                                    "command_id": str(uuid4()), "state_version": version})

        runoff = vote(state.state_version, ballots["main"][PLAYER_ID])
        self.assertContains(runoff, "결선 투표")
        self.assertContains(runoff, 'data-vote-round="main"', count=len(ballots["main"]))
        self.assertNotContains(runoff, 'data-vote-round="runoff"')
        self.assertNotContains(runoff, 'id="published-locks"')
        candidates = _MeetingForms(runoff.content.decode()).targets_for("vote")
        expected = fixture["checks"][0]["expect"]["runoff_candidates"]
        self.assertCountEqual(candidates, expected)
        self.assertNotContains(runoff, '<select name="target"')
        rejected = vote(state.state_version + 1, "meosum")
        self.assertContains(rejected, "결선 투표", status_code=422)
        self.assertNotContains(rejected, 'data-vote-round="runoff"', status_code=422)

        night = vote(state.state_version + 1, ballots["runoff"][PLAYER_ID])
        self.assertContains(night, "점칠 대상")
        self.assertContains(night, 'data-vote-round="main"', count=len(ballots["main"]))
        self.assertContains(night, 'data-vote-round="runoff"', count=len(ballots["runoff"]))
        self.assertRegex(night.content.decode(), r"1일째 ·\s*무감금")
        self.assertNotContains(night, "금줄 반응")
        latest, _ = self.service.repository.load(state.meta.game_id)
        self.assertIs(latest.phase, Phase.NIGHT)
        self.assertIsNone(latest.public.locks[-1].locked)

    def test_divination_form_can_declare_a_result_other_than_the_private_one(self):
        """실제 엔진에서 공개 주장과 본인의 실제 점괘를 분리한다."""
        engine = PaldoEngine(NightPolicy(votes={(1, "main"): DAY1_LOCK_YAKCHO},
                                         nights={1: NightPlan("blacksmith", "hunjang")}))
        state, truth = to_night_yakcho(self, engine)
        state, truth, _ = step(self, engine, state, truth, CommandKind.DIVINE, target="blacksmith")
        state, truth = drive_to(self, engine, state, truth, Phase.ROUND1)
        state = replace(state, meta=replace(state.meta, player_pseudonym=self.player))
        self.service.engine = engine
        self.service.repository.create(state, truth, [])
        game_url = f"/games/{state.meta.game_id}"
        self.command_url = game_url + "/commands"

        before = self.client.get(game_url, HTTP_HX_REQUEST="true")
        self.assertContains(before, 'value="1:blacksmith:clear"')
        self.assertContains(before, 'value="1:blacksmith:murky"')
        self.assertContains(before, "<li>1일째 밤 · 대장장이 · 탁함</li>", html=True)
        self.assertNotContains(before, "<li>1일째 밤 · 대장장이 · 맑음</li>", html=True)

        after = self._post_form({"game_id": state.meta.game_id, "kind": "reveal_divination",
                                 "divination": "1:blacksmith:clear", "command_id": str(uuid4()),
                                 "state_version": state.state_version})
        self.assertContains(after, "점괘 공개 · 대장장이 · 맑음")
        self.assertContains(after, "<li>1일째 밤 · 대장장이 · 탁함</li>", html=True)
        latest, _ = self.service.repository.load(state.meta.game_id)
        self.assertIs(latest.public.claims[-1].divination_result, DivinationResult.CLEAR)
        self.assertIs(latest.player.divinations[1]["blacksmith"], DivinationResult.MURKY)

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
