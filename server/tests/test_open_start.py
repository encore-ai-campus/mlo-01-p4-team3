"""E-08-14 회차 없는 일반 판(랜덤 시드) 시작 시험: 시작 정책 단위와 실제 Django 경로."""
import json
import uuid
from functools import partial
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from engine.domain.model import ENGINE_VERSION, PRESETS
from server.app.dto import StartGameInput
from server.app.open_start import (SEED_LIMIT, StartPolicy, StartVersions, deployed_versions, open_game_id,
                                   open_preset_ids, unfinished_open_games)
from server.app.test_sessions import TestSessionService
from server.app.usecases import StartUnavailable
from server.paldo_web.composition import configure_game_service, configure_session_service, get_game_service
from .session_store import MemorySessionStore
from .support import install_test_profiles, make_service

VERSIONS = StartVersions("rule-test", ENGINE_VERSION, "content-test")


def start_input(player, **values):
    """일반 판 기본 입력(회차 없음, 시작 UUID 있음)."""
    values.setdefault("start_token", str(uuid.uuid4()))
    return StartGameInput(player, values.pop("preset_id", "base6"), values.pop("session_id", None), "f", **values)


class OpenStartPolicy(SimpleTestCase):
    """응용 계층 시작 정책을 저장소·Django 없이 확인한다."""

    def setUp(self):
        self.player = str(uuid.uuid4())
        self.session_calls = []
        self.draws = []

        def randbelow(limit):
            self.draws.append(limit)
            return 424242
        self.games = {}

        def load_game(game_id):
            return self.games[game_id]
        self.policy = StartPolicy(lambda data, player: self.session_calls.append((data, player)) or "session-meta",
                                  lambda: VERSIONS, load_game, randbelow)

    def test_open_meta_uses_server_seed_versions_and_pseudonym(self):
        """시드는 서버 난수(회차 등록과 같은 범위), 버전 3개는 배포 값, 회차 ID는 없다."""
        data = start_input(self.player)
        meta = self.policy(data, self.player)
        self.assertEqual((meta.seed, self.draws), (424242, [SEED_LIMIT]))
        self.assertEqual((meta.rule_version, meta.engine_version, meta.content_version),
                         ("rule-test", ENGINE_VERSION, "content-test"))
        self.assertEqual((meta.player_pseudonym, meta.session_id, meta.preset.preset_id), (self.player, None, "base6"))
        self.assertEqual(meta.game_id, open_game_id(self.player, data.start_token))
        self.assertEqual(self.session_calls, [])

    def test_session_id_goes_to_session_policy_unchanged(self):
        """회차 ID가 있으면 일반 판으로 우회하지 않고 회차 정책에 그대로 넘긴다."""
        data = start_input(self.player, session_id="nov06")
        self.assertEqual(self.policy(data, self.player), "session-meta")
        self.assertEqual(self.session_calls, [(data, self.player)])

    def test_rejects_client_seed_closed_preset_and_bad_token(self):
        """클라이언트 시드·비공개 프리셋·시작 UUID 누락·형식 오류는 거부한다."""
        closed = next(pid for pid in PRESETS if pid not in open_preset_ids())
        cases = {"seed_not_allowed": start_input(self.player, seed=7),
                 "preset_not_open": start_input(self.player, preset_id=closed),
                 "start_token_required": start_input(self.player, start_token=None)}
        for code, data in cases.items():
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                self.policy(data, self.player)
        with self.assertRaises(ValueError):
            self.policy(start_input(self.player, start_token="not-a-uuid"), self.player)
        self.assertEqual(self.draws, [])

    def test_existing_game_meta_returned_before_new_game_checks(self):
        """같은 시작 UUID의 판이 있으면 프리셋·버전 검사와 난수 없이 그 판 메타를 돌려준다."""
        data = start_input(self.player)
        existing = MagicMock()
        self.games[open_game_id(self.player, data.start_token)] = (existing, None)
        self.policy.versions = lambda: self.fail("버전을 읽으면 안 된다")
        closed = next(pid for pid in PRESETS if pid not in open_preset_ids())
        self.assertIs(self.policy(start_input(self.player, preset_id=closed, start_token=data.start_token),
                                  self.player), existing.meta)
        self.assertEqual(self.draws, [])

    def test_open_presets_default_to_test_default(self):
        """공개 프리셋 설정 파일 전에는 사용자 테스트 기본 프리셋만 연다."""
        self.assertEqual(open_preset_ids(), ["base6"])

    def test_game_id_is_per_player_and_token(self):
        """같은 사용자·같은 시작 UUID만 같은 판 ID가 된다."""
        token = str(uuid.uuid4())
        self.assertEqual(open_game_id(self.player, token), open_game_id(self.player, token))
        self.assertNotEqual(open_game_id(self.player, token), open_game_id(str(uuid.uuid4()), token))
        self.assertNotEqual(open_game_id(self.player, token), open_game_id(self.player, str(uuid.uuid4())))

    def test_missing_content_version_is_unavailable(self):
        """운영 콘텐츠 버전이 없으면 빈 값으로 시작하지 않고 503 계열 오류를 낸다."""
        with self.assertRaisesRegex(StartUnavailable, "content_version_not_configured"):
            deployed_versions(lambda: "rule-test", "")
        self.assertEqual(deployed_versions(lambda: "rule-test", "c1"), StartVersions("rule-test", ENGINE_VERSION, "c1"))

    def test_mongo_runtime_wires_same_policy(self):
        """운영 Mongo 조립도 회차 정책을 일반 판 정책으로 감싼다."""
        from server.paldo_web.mongo import MongoRuntime
        runtime = MongoRuntime(MagicMock(), MagicMock(), object(), None, "t_")
        self.assertIsInstance(runtime.game.prepare_start, StartPolicy)
        self.assertIsInstance(runtime.game.prepare_start.session_prepare, partial)
        self.assertEqual(runtime.game.prepare_start.session_prepare.func, runtime.sessions.prepare)


class OpenStartAPI(TestCase):
    """기본 조립(composition) 경로로 홈 화면·시작·수첩·설문까지 확인한다."""

    def setUp(self):
        self.user = User.objects.create_user(username="open-start-owner")
        self.profile = install_test_profiles(self)(self.user)
        self.player = str(self.profile.player_pseudonym)
        self.client.force_login(self.user)
        self.service = make_service()
        self.service.prepare_start = None   # 기본 조립이 시작 정책을 끼우게 한다
        configure_game_service(self.service)
        configure_session_service(TestSessionService(MemorySessionStore()))
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        content_version = self.service.dependencies(None).content.content_version
        settings = override_settings(ACTIVE_CONTENT_VERSION=content_version)
        settings.enable()
        self.addCleanup(settings.disable)
        self.content_version = content_version

    def send(self, method, path, body=None):
        return self.client.generic(method, path, json.dumps(body or {}), content_type="application/json",
                                   HTTP_ACCEPT="application/json")

    def start(self, **body):
        body = {"preset_id": "base6", "mudang_variant": "f", "start_token": str(uuid.uuid4()), **body}
        return self.send("POST", "/games", body)

    def test_default_composition_wires_policy(self):
        """기본 조립은 prepare_start가 비어 있으면 일반 판 정책을 끼운다."""
        self.assertIsInstance(get_game_service().prepare_start, StartPolicy)

    def test_open_start_records_random_seed_versions_and_pseudonym(self):
        """회차 생략·null·빈 문자열은 모두 일반 판이고, 시드·버전·가명이 메타에 남는다."""
        from engine.domain import params
        for session in ({}, {"session_id": None}, {"session_id": ""}):
            with self.subTest(session=session):
                response = self.start(**session)
                self.assertEqual(response.status_code, 200, response.content)
                state, _ = self.service.repository.load(response.json()["view"]["game_id"])
                self.assertIsNone(state.meta.session_id)
                self.assertEqual(state.meta.player_pseudonym, self.player)
                self.assertTrue(0 <= state.meta.seed < SEED_LIMIT)
                self.assertEqual((state.meta.rule_version, state.meta.engine_version, state.meta.content_version),
                                 (params.RULE_PARAMS_VERSION, ENGINE_VERSION, self.content_version))

    def test_seeds_differ_between_games(self):
        """서버 난수 시드라 판마다 시드가 달라진다(2^63 범위에서 충돌 확률 무시)."""
        seeds = set()
        for _ in range(3):
            game_id = self.start().json()["view"]["game_id"]
            seeds.add(self.service.repository.load(game_id)[0].meta.seed)
        self.assertEqual(len(seeds), 3)

    def test_rejections(self):
        """클라이언트 시드·시작 UUID 누락은 400, 없는 회차는 일반 판으로 우회하지 않고 404."""
        self.assertEqual(self.start(seed=7).status_code, 400)
        self.assertEqual(self.send("POST", "/games", {"preset_id": "base6", "mudang_variant": "f"}).status_code, 400)
        self.assertEqual(self.start(session_id="no-such-session").status_code, 404)
        self.assertEqual(self.service.progress.load(self.player).game_ids, [])

    def test_same_start_token_returns_one_game(self):
        """두 번 누른 같은 시작 요청은 판 하나만 만든다."""
        token = str(uuid.uuid4())
        first, second = self.start(start_token=token), self.start(start_token=token)
        self.assertEqual(first.json()["view"]["game_id"], second.json()["view"]["game_id"])
        self.assertEqual(self.service.progress.load(self.player).game_ids, [first.json()["view"]["game_id"]])

    def test_retry_returns_existing_game_after_config_change(self):
        """응답을 잃은 재전송은 그사이 콘텐츠 버전 설정이 빠져도 이미 만든 판을 돌려준다."""
        token = str(uuid.uuid4())
        game_id = self.start(start_token=token).json()["view"]["game_id"]
        with override_settings(ACTIVE_CONTENT_VERSION=""):
            retry = self.start(start_token=token)
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.json()["view"]["game_id"], game_id)

    def test_missing_content_version_returns_503(self):
        """운영 콘텐츠 버전이 설정되지 않으면 일반 판 시작은 503으로 거부한다."""
        with override_settings(ACTIVE_CONTENT_VERSION=""):
            response = self.start()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.progress.load(self.player).game_ids, [])

    def test_home_form_start_and_resume(self):
        """홈의 새 게임 폼으로 시작하고, 진행 중 일반 판은 이어 하기로, 끝난 판은 목록에서 빠진다."""
        home = self.client.get("/")
        self.assertContains(home, "새 게임 시작")
        token = home.context["open_start_token"]
        form = urlencode({"start_token": token, "preset_id": "base6", "mudang_variant": "m"})
        response = self.client.post("/games", form, content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 302, response.content)
        game_id = open_game_id(self.player, token)
        self.assertEqual(response["Location"], f"/games/{game_id}")
        home = self.client.get("/")
        self.assertContains(home, f"/games/{game_id}")
        self.assertNotEqual(home.context["open_start_token"], token)
        self.finish(game_id)
        self.assertNotContains(self.client.get("/"), f"/games/{game_id}")

    def test_unfinished_open_games_skip_sessions_finished_and_missing(self):
        """이어 하기 목록은 회차 판·끝난 판·없는 판을 뺀다."""
        open_id = self.start().json()["view"]["game_id"]
        state = self.service.repository.load(open_id)[0]
        session_state = MagicMock(meta=MagicMock(session_id="nov06"), result=None)
        loads = {open_id: (state, None), "session-game": (session_state, None)}

        def load(game_id):
            return loads[game_id]
        self.assertEqual(unfinished_open_games(["session-game", "missing", open_id], load), [open_id])

    def finish(self, game_id):
        for step in self.service.engine.steps[1:]:
            response = self.send("POST", f"/games/{game_id}/commands",
                                 dict(step["request"], game_id=game_id, command_id=str(uuid.uuid4())))
            self.assertEqual(response.status_code, 200, response.content)

    def test_notebook_and_survey_recorded_like_session_games(self):
        """일반 판도 수첩·설문 이벤트가 회차 판과 같은 주제로 남는다."""
        game_id = self.start().json()["view"]["game_id"]
        view = self.client.get(f"/games/{game_id}", HTTP_ACCEPT="application/json").json()["view"]
        npc = next(name for name in view["names"] if name != "mudang")
        self.assertEqual(self.send("PUT", f"/games/{game_id}/notebook",
                                   {"game_id": game_id, "entries": {npc: 70}, "vote_confidence": 60}).status_code, 204)
        self.finish(game_id)
        survey = {"game_id": game_id, "fun": 4, "unfair": 2, "npc_plausible": 5, "comment": ""}
        self.assertEqual(self.send("POST", f"/games/{game_id}/survey", survey).status_code, 204)
        events = self.service.repository.unpublished(game_id)
        kinds = {event.event_type: event for event in events}
        for kind in ("notebook_updated", "survey_submitted"):
            self.assertEqual(kinds[kind].topic, "paldo.player")
        seed = self.service.repository.load(game_id)[0].meta.seed
        self.assertEqual({(e.player_pseudonym, e.session_id, e.seed, e.engine_version) for e in events},
                         {(self.player, None, seed, ENGINE_VERSION)})


@override_settings(ACTIVE_CONTENT_VERSION="")
class OpenStartUnconfigured(TestCase):
    """콘텐츠 버전이 없어도 홈은 열리고, 기존 회차 판 시작 경로는 그대로 동작한다."""

    def test_session_start_unaffected(self):
        user = User.objects.create_user(username="open-start-session")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        service = make_service()   # 녹화 판 회차 정책(test-session)을 그대로 쓴다
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.assertEqual(self.client.get("/").status_code, 200)
        body = {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}
        response = self.client.post("/games", json.dumps(body), content_type="application/json",
                                    HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content)
