"""진행 판 이어 하기 / 새로 시작(기존 판 중단) 양자택일 시험(11/10 사용자 결정)."""
import json
import threading
import uuid
from dataclasses import replace
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from engine.domain import serde
from engine.domain.model import EndReason
from engine.ports import VersionConflict
from ml.bandit import games_from_events
from pipeline.spark.silver_contract import completed_truths
from server.accounts.views import OPEN_GAME_TEXT
from server.app.dto import StartGameInput
from server.app.open_start import open_game_id
from server.app.usecases import OpenGameInProgress
from server.app.test_sessions import TestSessionService
from server.paldo_web.composition import configure_game_service, configure_session_service, get_game_service
from .session_store import MemorySessionStore
from .support import install_test_profiles, make_service


class AbandonOpenGame(TestCase):
    """기본 조립 경로(일반 판 시작 정책)로 막힘·고르기·중단·학습 제외를 확인한다."""

    def setUp(self):
        """동의한 계정과 녹화 판 서비스, 콘텐츠 버전 설정을 준비한다."""
        self.user = User.objects.create_user(username="abandon-owner")
        self.player = str(install_test_profiles(self)(self.user).player_pseudonym)
        self.client.force_login(self.user)
        self.service = make_service()
        self.service.prepare_start = None   # 기본 조립이 일반 판 정책을 끼우게 한다
        configure_game_service(self.service)
        configure_session_service(TestSessionService(MemorySessionStore()))
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        settings = override_settings(ACTIVE_CONTENT_VERSION=self.service.dependencies(None).content.content_version)
        settings.enable()
        self.addCleanup(settings.disable)

    def start(self, **body):
        """JSON 일반 판 시작 요청."""
        body = {"preset_id": "base6", "mudang_variant": "f", "start_token": str(uuid.uuid4()), **body}
        return self.client.post("/games", json.dumps(body), content_type="application/json",
                                HTTP_ACCEPT="application/json")

    def form_start(self, **extra):
        """홈 화면 폼과 같은 일반 판 시작 요청. (시작 UUID, 응답)을 돌려준다."""
        token = self.client.get("/").context["open_start_token"]
        form = {"start_token": token, "preset_id": "base6", "mudang_variant": "f", **extra}
        return token, self.client.post("/games", urlencode(form), content_type="application/x-www-form-urlencoded")

    def game_ids(self):
        """계정 진척에 남은 판 ID 목록(시작 순)."""
        return self.service.progress.load(self.player).game_ids

    def ended_events(self, game_id):
        """그 판 아웃박스의 game_ended 이벤트."""
        return [e for e in self.service.repository.unpublished(game_id) if e.event_type == "game_ended"]

    def test_json_start_blocked_with_open_game(self):
        """진행 판이 있으면 replace_open 없는 JSON 시작은 409와 진행 판 ID를 받고 새 판을 만들지 않는다."""
        first = self.start().json()["view"]["game_id"]
        response = self.start()
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json(), {"error": "open_game_in_progress", "game_id": first})
        self.assertEqual(self.game_ids(), [first])
        self.assertIsNone(self.service.repository.load(first)[0].result)

    def test_form_start_blocked_redirects_to_choose_screen(self):
        """폼 시작은 홈 고르기 화면으로 돌아가고, 그 화면은 같은 두 버튼(이어 하기·중단하고 새로 시작)을 보인다."""
        _, created = self.form_start()
        first = created["Location"].rsplit("/", 1)[-1]
        _, blocked = self.form_start()
        self.assertEqual((blocked.status_code, blocked["Location"]), (302, "/?open=choose"))
        self.assertEqual(self.game_ids(), [first])
        home = self.client.get("/?open=choose")
        self.assertContains(home, OPEN_GAME_TEXT["notice"])
        self.assertContains(home, OPEN_GAME_TEXT["restart"])
        self.assertContains(home, OPEN_GAME_TEXT["restart_warning"])
        self.assertContains(home, f'href="/games/{first}"')
        self.assertContains(home, 'name="replace_open" value="1"')
        self.assertNotContains(home, "<details class=\"open-restart")
        self.assertNotContains(home, "새 게임 시작")
        self.assertNotContains(home, "confirm(")

    def test_restart_abandons_old_game_and_creates_new(self):
        """새로 시작(replace_open=1)은 기존 판을 중단(삭제 없음)하고 game_ended(completed=false)를 남긴 뒤 새 판을 만든다."""
        _, created = self.form_start()
        old = created["Location"].rsplit("/", 1)[-1]
        token, response = self.form_start(replace_open="1")
        new = open_game_id(self.player, token)
        self.assertEqual(response["Location"], f"/games/{new}")
        self.assertEqual(self.game_ids(), [old, new])
        state, truth = self.service.repository.load(old)
        self.assertEqual((state.result.winner, state.result.reason, state.result.completed),
                         (None, EndReason.ABORTED, False))
        self.assertIsNotNone(truth)
        ended = self.ended_events(old)
        self.assertEqual([e.payload for e in ended], [{"winner": None, "reason": "aborted", "completed": False}])
        self.assertEqual(ended[0].topic, "paldo.game")
        self.assertFalse(any(e.event_type == "truth_revealed" for e in self.service.repository.unpublished(old)))
        self.assertIsNone(self.service.repository.load(new)[0].result)

    def test_home_lists_only_latest_open_game_and_skips_abandoned(self):
        """이어 하기는 가장 최근 진행 판 하나만, 중단한 판은 보이지 않는다."""
        old = self.start().json()["view"]["game_id"]
        new = self.start(replace_open=True).json()["view"]["game_id"]
        home = self.client.get("/")
        self.assertEqual(home.context["open_games"], [new])
        self.assertNotContains(home, f"/games/{old}")
        self.assertContains(home, OPEN_GAME_TEXT["notice"])
        self.assertContains(home, OPEN_GAME_TEXT["resume"])

    def test_home_without_open_game_keeps_plain_start(self):
        """진행 판이 없으면 기존 "새 게임 시작" 버튼만 보이고 중단 버튼은 없다."""
        home = self.client.get("/")
        self.assertContains(home, "새 게임 시작")
        self.assertNotContains(home, 'name="replace_open"')

    def test_abandoned_game_screens_open(self):
        """중단한 판의 판 화면은 "중단된 판입니다."를 보이고 승패를 보이지 않으며, 복기 화면도 열린다.

        녹화 판 엔진은 저장 상태의 결과를 화면에 옮기지 않으므로, 실제 엔진처럼 상태의 결과·단계를 화면에 싣는다.
        """
        old = self.start().json()["view"]["game_id"]
        self.start(replace_open=True)
        recorded = self.service.engine.player_view

        def player_view(state, content):
            return replace(recorded(state, content), result=state.result, phase=state.phase)
        with patch.object(self.service.engine, "player_view", side_effect=player_view):
            for headers in ({"HTTP_ACCEPT": "text/html"}, {"HTTP_HX_REQUEST": "true"}):
                page = self.client.get(f"/games/{old}", **headers)
                self.assertContains(page, "중단된 판입니다.")
                self.assertNotContains(page, "창귀 승리")
                self.assertNotContains(page, "마을 승리")
            review = self.client.get(f"/games/{old}/review", HTTP_ACCEPT="application/json")
            self.assertEqual(review.status_code, 200, review.content)

    def test_same_token_retry_after_restart_returns_same_game(self):
        """새로 시작 요청을 같은 시작 UUID로 다시 보내면 같은 판을 돌려주고 중단을 다시 하지 않는다."""
        old = self.start().json()["view"]["game_id"]
        token = str(uuid.uuid4())
        first = self.start(start_token=token, replace_open=True).json()["view"]["game_id"]
        again = self.start(start_token=token)
        self.assertEqual((again.status_code, again.json()["view"]["game_id"]), (200, first))
        self.assertEqual(len(self.ended_events(old)), 1)
        self.assertEqual(self.game_ids(), [old, first])

    def run_concurrently(self, inputs):
        """두 시작 요청을 동시에 보낸다. 첫 요청은 새 판 준비 중 두 번째 요청이 출발할 때까지 기다린다."""
        results, errors, second_started = [], [], threading.Event()
        original = self.service.engine.new_game

        def slow_new_game(meta, deps):
            second_started.wait(timeout=1)
            return original(meta, deps)
        self.service.engine.new_game = slow_new_game

        def run(data, signal):
            if signal:
                second_started.set()
            try:
                results.append(self.service.start(data, self.player, True).view.game_id)
            except Exception as error:  # noqa: BLE001 - 스레드 결과를 시험에서 확인한다
                errors.append(error)
        threads = [threading.Thread(target=run, args=(data, i == 1)) for i, data in enumerate(inputs)]
        threads[0].start()
        threads[1].start()
        for thread in threads:
            thread.join(timeout=10)
        return results, errors

    def test_concurrent_same_token_restart_yields_one_game(self):
        """같은 시작 UUID의 새로 시작이 동시에 두 번 와도 새 판 하나, 중단 이벤트 하나다."""
        old = self.start().json()["view"]["game_id"]
        token = str(uuid.uuid4())
        data = StartGameInput(self.player, "base6", None, "f", start_token=token, replace_open=True)
        results, errors = self.run_concurrently([data, data])
        self.assertEqual(errors, [])
        self.assertEqual(results, [open_game_id(self.player, token)] * 2)
        self.assertEqual(sorted(self.service.repository.docs), sorted([old, results[0]]))
        self.assertEqual(len(self.ended_events(old)), 1)

    def test_concurrent_different_token_starts_leave_one_open_game(self):
        """진행 판이 없을 때 다른 시작 UUID 두 요청이 동시에 와도 진행 판은 하나, 나머지는 고르기로 막힌다."""
        get_game_service()   # 기본 조립이 일반 판 시작 정책을 끼운다
        inputs = [StartGameInput(self.player, "base6", None, "f", start_token=str(uuid.uuid4())) for _ in range(2)]
        results, errors = self.run_concurrently(inputs)
        self.assertEqual(len(results), 1, errors)
        self.assertEqual([type(e) for e in errors], [OpenGameInProgress])
        self.assertEqual(self.service.unfinished_open_games(self.player), results)

    def test_failed_preparation_keeps_old_game(self):
        """새 판 준비(엔진 초기화)가 실패하면 기존 판은 중단되지 않는다."""
        old = self.start().json()["view"]["game_id"]
        with patch.object(self.service.engine, "new_game", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.service.start(StartGameInput(self.player, "base6", None, "f", start_token=str(uuid.uuid4()),
                                                  replace_open=True), self.player, True)
        self.assertIsNone(self.service.repository.load(old)[0].result)
        self.assertEqual(self.ended_events(old), [])

    def test_failed_storage_keeps_old_game(self):
        """새 판 저장이 실패하면 기존 판은 중단되지 않아 이어 할 수 있다."""
        old = self.start().json()["view"]["game_id"]
        with patch.object(self.service.repository, "create", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.service.start(StartGameInput(self.player, "base6", None, "f", start_token=str(uuid.uuid4()),
                                                  replace_open=True), self.player, True)
        self.assertIsNone(self.service.repository.load(old)[0].result)
        self.assertEqual(self.game_ids(), [old])

    def test_unresolved_abandon_conflict_keeps_both_games(self):
        """기존 판 중단이 버전 충돌로 끝나지 않아도 새 판은 시작되고, 기존 판은 지우지 않고 진행 중으로 남는다."""
        old = self.start().json()["view"]["game_id"]
        with patch.object(self.service.repository, "commit", side_effect=VersionConflict(1, 2)),                 self.assertLogs("paldo.progress", level="WARNING"):
            response = self.start(replace_open=True)
        self.assertEqual(response.status_code, 200, response.content)
        new = response.json()["view"]["game_id"]
        self.assertEqual(self.service.unfinished_open_games(self.player), [old, new])
        self.assertEqual(self.client.get("/").context["open_games"], [new])
        self.assertEqual(self.start(replace_open=True).status_code, 200)   # 다음 새로 시작 때 둘 다 중단
        self.assertEqual(self.service.repository.load(old)[0].result.reason, EndReason.ABORTED)

    def test_retry_finishes_interrupted_abandon(self):
        """새 판 저장 뒤 중단이 실패한 요청을 같은 시작 UUID로 다시 보내면 중단을 마저 하고, 뒤에 시작한 판은 건드리지 않는다."""
        old = self.start().json()["view"]["game_id"]
        token = str(uuid.uuid4())
        with patch.object(self.service, "abandon_game", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.service.start(StartGameInput(self.player, "base6", None, "f", start_token=token,
                                                  replace_open=True), self.player, True)
        new = open_game_id(self.player, token)
        self.assertEqual(self.service.unfinished_open_games(self.player), [old, new])
        retry = self.start(start_token=token, replace_open=True)
        self.assertEqual((retry.status_code, retry.json()["view"]["game_id"]), (200, new))
        self.assertEqual(self.service.repository.load(old)[0].result.reason, EndReason.ABORTED)
        self.assertEqual(self.service.unfinished_open_games(self.player), [new])
        later = self.start(replace_open=True).json()["view"]["game_id"]
        self.assertEqual(self.start(start_token=token, replace_open=True).status_code, 200)
        self.assertEqual(self.service.unfinished_open_games(self.player), [later])

    def test_session_games_neither_block_nor_get_abandoned(self):
        """회차 판(test-session)은 진행 중이어도 일반 판 시작을 막지 않고 중단되지도 않는다."""
        get_game_service().prepare_start.session_prepare = make_service().prepare_start   # 녹화 판 회차 정책
        session_body = {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}
        created = self.client.post("/games", json.dumps(session_body), content_type="application/json",
                                   HTTP_ACCEPT="application/json")
        self.assertEqual(created.status_code, 200, created.content)
        session_game = created.json()["view"]["game_id"]
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.start(replace_open=True).status_code, 200)
        self.assertIsNone(self.service.repository.load(session_game)[0].result)
        with self.assertRaisesRegex(ValueError, "session_game_not_abandonable"):
            self.service.abandon_game(session_game, self.player)

    def test_abandoned_game_excluded_from_labels_and_reward(self):
        """중단 판의 game_ended(completed=false)는 라벨 조인과 밴딧 보상 조인에서 빠진다(진실이 뒤따라도)."""
        old = self.start().json()["view"]["game_id"]
        self.start(replace_open=True)
        events = [serde.to_jsonable(e) for e in self.service.repository.unpublished(old)]
        ended = next(e for e in events if e["event_type"] == "game_ended")
        forged_truth = dict(ended, event_type="truth_revealed", topic="paldo.truth", seq=ended["seq"] + 1)
        self.assertEqual(completed_truths(events + [forged_truth]), {})
        human = dict(ended, source="human", load_test=False, player_pseudonym="p1",
                     model_versions={"bandit_arm": "a", "bandit": "b"})
        self.assertEqual(games_from_events([human], {"p1"}), [])
