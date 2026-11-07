"""B-06-03 운영자 화면·고정 판 배정·가명 대응표 시험."""
import json
import uuid
from urllib.parse import urlencode
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone
from engine.domain.engine import PaldoEngine
from server.accounts.models import AccountProfile
from server.api.sessions import ensure_ad_identity
from server.app.test_sessions import TestSessionService
from server.paldo_web.composition import (configure_game_service, configure_session_service,
                                         get_game_service, get_session_service)
from .support import issue_test_csrf, make_service
from .session_store import MemorySessionStore


class SessionAPI(TestCase):
    """운영자 등록부터 참가자 판 시작과 C 삭제 경계까지 확인한다."""

    def setUp(self):
        """계정 두 개와 운영자를 준비한다."""
        self.users, self.players = [], []
        for name in ("session-owner", "session-second"):
            user = User.objects.create_user(username=name)
            self.users.append(user)
            self.players.append(str(AccountProfile.objects.create(user=user, consented_at=timezone.now()).player_pseudonym))
        self.staff = User.objects.create_user(username="session-operator", is_staff=True)
        self.sessions = TestSessionService(MemorySessionStore())
        self.game = make_service()
        self.game.prepare_start = None
        configure_game_service(self.game)
        configure_session_service(self.sessions)
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        self.seeds = (910001, 910002, 910003, 910004)
        self.body = {"preset_id": "base6", "session_id": "nov06", "mudang_variant": "f"}
        self.form = {"session_id": "nov06", "plans": json.dumps([{"preset_id": "base6", "seed": seed} for seed in self.seeds]),
                     "participants": "\n".join(self.players), "rule_version": "test-rule", "engine_version": "scripted",
                     "content_version": self.game.dependencies(None).content.content_version}

    def register(self):
        """운영자가 폼으로 회차를 등록한다."""
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post("/admin/sessions", self.form).status_code, 302)

    def test_staff_auth_csrf_validation_and_immutable_sessions(self):
        """일반 참가자는 관리 불가이며 등록한 구성을 덮어쓰지 못한다."""
        self.assertEqual(self.client.get("/admin/sessions").status_code, 302)
        self.client.force_login(self.users[0])
        self.assertEqual(self.client.get("/admin/sessions").status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.staff)
        self.assertEqual(csrf.post("/admin/sessions", self.form).status_code, 403)
        self.register()
        self.assertContains(self.client.get("/admin/sessions"), "nov06")
        self.assertEqual(self.client.post("/admin/sessions", self.form).status_code, 400)
        self.assertEqual(len(self.sessions.store.list_sessions()), 1)
        for plans in ('{}', '[{"preset_id":"bogus","seed":1}]', '[{"preset_id":"base6","seed":true}]',
                      '[{"preset_id":[],"seed":1}]', '[{"preset_id":"base6","seed":1},{"preset_id":"base6","seed":1}]'):
            self.assertEqual(self.client.post("/admin/sessions", dict(self.form, session_id="bad", plans=plans)).status_code, 400)

    def test_two_players_get_same_fixed_meta_and_ad_mapping(self):
        """실제 엔진의 인물·역할 배정이 같고 이벤트와 광고 가명은 계정별로 분리된다."""
        self.game.engine = PaldoEngine()
        self.register()
        configurations, ads = [], []
        for user, player in zip(self.users, self.players):
            self.client.force_login(user)
            self.assertContains(self.client.get("/"), "nov06")
            response = self.client.post("/games", self.body, content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            gid = response.json()["view"]["game_id"]
            state, truth = self.game.repository.load(gid)
            self.assertEqual(state.meta.seed, self.seeds[0])
            configurations.append((state.active, truth.roles, truth.secrets))
            event = self.game.repository.unpublished(gid)[0]
            self.assertEqual((event.source.value, event.session_id, event.player_pseudonym), ("human", "nov06", player))
            request = self.client.get("/").wsgi_request
            ads.append(ensure_ad_identity(request))
            self.assertEqual(ensure_ad_identity(request), ads[-1])
            self.assertEqual(self.sessions.store.ad_identities(player), [ads[-1]])
            self.assertNotEqual(player, ads[-1])
            self.assertNotIn(request.session.session_key, str(self.sessions.store.identities))
            self.assertNotContains(self.client.get("/"), ads[-1])
        self.assertEqual(*configurations)
        self.assertNotEqual(*ads)

    def test_unassigned_participant_seed_and_missing_consent(self):
        """클라이언트가 임의 회차·시드·참가자를 선택할 수 없다."""
        self.register()
        self.client.force_login(self.users[0])
        for seed in (999, self.seeds[1]):
            self.assertEqual(self.client.post("/games", dict(self.body, seed=seed), content_type="application/json").status_code, 400)
        self.sessions.store.delete_player(self.players[0])
        self.assertEqual(self.client.post("/games", self.body, content_type="application/json").status_code, 404)
        self.client.force_login(self.users[1])
        AccountProfile.objects.filter(user=self.users[1]).update(consented_at=None)
        self.assertNotContains(self.client.get("/"), "nov06")
        self.assertEqual(self.client.post("/games", self.body, content_type="application/json").status_code, 403)

    def test_html_start_and_idempotent_ad_identity_delete(self):
        """홈 폼으로 게임 화면에 진입하며 대응표 삭제는 다른 참가자에게 영향이 없다."""
        self.register()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.users[0])
        data = urlencode(dict(self.body, seed=self.seeds[0]))
        self.assertEqual(client.post("/games", data, content_type="application/x-www-form-urlencoded").status_code, 403)
        response = client.post("/games", data, content_type="application/x-www-form-urlencoded",
                               HTTP_X_CSRFTOKEN=issue_test_csrf(client))
        self.assertEqual(response.status_code, 302)
        self.assertContains(client.get(response["Location"], HTTP_ACCEPT="text/html"), 'id="meeting"')
        browser_id = str(uuid.uuid4())
        first = self.sessions.ad_identity(self.players[0], browser_id)
        self.assertEqual(self.sessions.ad_identity(self.players[0], browser_id), first)
        other = self.sessions.ad_identity(self.players[1], browser_id)
        for _ in range(2):
            self.sessions.store.delete_player(self.players[0])
        self.assertEqual(self.sessions.store.ad_identities(self.players[0]), [])
        self.assertEqual(self.sessions.store.ad_identities(self.players[1]), [other])
        self.assertEqual(self.sessions.assigned(self.players[0], self.game.repository.load), [])

    def test_htmx_form_start_returns_meeting_fragment(self):
        """htmx 폼의 시작·재요청은 CSRF 검사 후 회의 조각을 바로 반환한다."""
        self.register()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.users[0])
        data = urlencode(dict(self.body, seed=self.seeds[0]))
        headers = {"HTTP_HX_REQUEST": "true", "HTTP_ACCEPT": "text/html"}
        response = client.post("/games", data, content_type="application/x-www-form-urlencoded", **headers)
        self.assertEqual(response.status_code, 403)
        headers["HTTP_X_CSRFTOKEN"] = issue_test_csrf(client)
        game_ids = []
        for _ in range(2):
            response = client.post("/games", data, content_type="application/x-www-form-urlencoded", **headers)
            self.assertContains(response, 'id="meeting"', status_code=200)
            self.assertNotContains(response, "<html")
            self.assertNotIn("Location", response.headers)
            self.assertTemplateUsed(response, "meeting.html")
            game_ids.append(response.context["vm"].game_id)
        self.assertEqual(*game_ids)
        self.assertEqual(len(self.game.repository.unpublished(game_ids[0])), 1)

    def test_four_rounds_in_order_and_resume(self):
        """네 판을 순서대로 열고, 시작을 반복해도 기존 판과 이벤트를 유지한다."""
        self.register()
        self.client.force_login(self.users[0])
        for number, seed in enumerate(self.seeds, 1):
            with self.subTest(number=number):
                self.assertContains(self.client.get("/"), f"{number}번째 판 시작")
                self.assertContains(self.client.get("/"), f'value="{seed}"')
                body = dict(self.body, seed=seed)
                first = self.client.post("/games", body, content_type="application/json")
                again = self.client.post("/games", body, content_type="application/json")
                self.assertEqual(first.status_code, 200, first.content)
                self.assertEqual(again.status_code, 200, again.content)
                game_id = first.json()["view"]["game_id"]
                self.assertEqual(again.json()["view"]["game_id"], game_id)
                self.assertEqual(len(self.game.repository.unpublished(game_id)), 1)
                self.assertContains(self.client.get("/"), f"{number}번째 판 이어 하기")
                for recorded in self.game.engine.steps[1:]:
                    command = dict(recorded["request"], game_id=game_id, command_id=str(uuid.uuid4()))
                    result = self.client.post(f"/games/{game_id}/commands", command, content_type="application/json")
                    self.assertEqual(result.status_code, 200, result.content)
                self.assertContains(self.client.get(f"/games/{game_id}", HTTP_ACCEPT="text/html"), "테스트 회차로 돌아가기")
        self.assertContains(self.client.get("/"), "배정된 판을 모두 마쳤습니다.")

    def test_lazy_factory_connects_home_sessions_and_start_policy(self):
        """회차 저장소와 게임 서비스를 각각 필요할 때만 조립한다."""
        configure_session_service(None)
        configure_game_service(None)
        with self.settings(GAME_SERVICE_FACTORY="deployment.factory", SESSION_MONGO_URI="mongodb://localhost"), patch(
                "server.paldo_web.composition.import_string", return_value=lambda: self.game) as game_factory, patch(
                "server.infra.test_sessions.connect_session_store", return_value=self.sessions.store) as session_factory:
            self.assertIs(get_session_service().store, self.sessions.store)
            game_factory.assert_not_called()
            self.assertIs(get_game_service(), self.game)
            self.register()
            self.client.force_login(self.users[0])
            self.assertEqual(self.client.post("/games", self.body, content_type="application/json").status_code, 200)
            session_factory.assert_called_once_with(settings.SESSION_MONGO_URI)

    def test_unconfigured_store_leaves_account_home_available(self):
        """회차 미연결은 계정 화면을 막지 않고 관리와 판 시작에서 503으로 드러난다."""
        configure_session_service(None)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/admin/sessions").status_code, 503)
        self.client.force_login(self.users[0])
        self.assertContains(self.client.get("/"), "지금은 테스트 회차를 조회할 수 없습니다.")
        self.assertEqual(self.client.post("/games", self.body, content_type="application/json").status_code, 503)
