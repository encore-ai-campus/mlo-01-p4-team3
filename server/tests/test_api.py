"""녹화 판·HTTP 경계·권한·개인정보·운영 점검 회귀 시험."""
import json
import uuid
from dataclasses import replace
from io import StringIO
from logging import StreamHandler
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone

from server.paldo_web.composition import configure_game_service
from server.paldo_web.middleware import JsonFormatter, LOGGER
from .support import install_test_profiles, make_service

ROOT = Path(__file__).resolve().parents[2]


class GameAPI(TestCase):
    """로그인 계정으로 실제 Django 라우트를 호출한다."""

    def setUp(self):
        """각 시험에 독립된 계정·게임·저장소를 준비한다."""
        self.user = User.objects.create_user(username="api-player")
        self.register_profile = install_test_profiles(self)
        self.profile = self.register_profile(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.start = {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}

    def post_json(self, path, data):
        """명시적으로 JSON 응답을 요청한다."""
        return self.client.post(path, data=json.dumps(data), content_type="application/json", HTTP_ACCEPT="application/json")

    def start_game(self):
        """기록된 판 하나를 HTTP로 만든다."""
        response = self.post_json("/games", self.start)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["view"]["game_id"]

    def test_recording_thirteen_json_views(self):
        """녹화 판 13단계의 공개 JSON과 시작 이벤트를 검증한다."""
        recording = json.loads((ROOT / "fixtures/api/recorded_game_base6.json").read_text(encoding="utf-8"))
        self.assertEqual(len(recording["steps"]), 13)
        gid = self.start_game()
        for index, step in enumerate(recording["steps"]):
            expected = dict(step["view"], game_id=gid)
            if index:
                payload = dict(step["request"], game_id=gid, command_id=str(uuid.uuid4()))
                response = self.post_json(f"/games/{gid}/commands", payload)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["view"], expected)
            response = self.client.get(f"/games/{gid}", HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["view"], expected)
            for forbidden in ("claim_truths", "npc_states", "beliefs", "private", "night_plans"):
                self.assertNotIn(f'"{forbidden}"', response.content.decode())
        state, _ = self.service.repository.load(gid)
        self.assertEqual(state.state_version, 12)
        self.assertEqual(state.meta.player_pseudonym, str(self.profile.player_pseudonym))
        self.assertEqual(self.service.repository.unpublished(gid)[0].session_id, "test-session")

    def test_strict_input_and_server_pseudonym(self):
        """미정의 필드·타입 오류·서버 전용 입력을 거부한다."""
        for extra in ({"unknown": True}, {"player_pseudonym": "spoof"}, {"seed": True}, {"mudang_variant": "invalid"}):
            self.assertEqual(self.post_json("/games", dict(self.start, **extra)).status_code, 400)
        for body in (b"{", b"[]", b"\xff"):
            self.assertEqual(self.client.post("/games", body, content_type="application/json").status_code, 400)
        self.assertEqual(self.client.post("/games", self.start).status_code, 400)

    def test_consent_and_missing_start_policy(self):
        """동의나 세션 배정 정책이 없으면 판을 만들지 않는다."""
        self.profile.consented_at = None
        self.assertEqual(self.post_json("/games", self.start).status_code, 403)
        self.profile.consented_at = timezone.now()
        self.service.prepare_start = None
        self.assertEqual(self.post_json("/games", self.start).status_code, 503)

    def test_auth_ownership_and_replay_order(self):
        """멱등 응답을 읽기 전에 소유권을 확인한다."""
        gid = self.start_game()
        payload = {"game_id": gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                   "command": {"kind": "advance", "actor": "mudang"}}
        self.assertEqual(self.post_json(f"/games/{gid}/commands", payload).status_code, 200)
        other = User.objects.create_user(username="other-player")
        self.register_profile(other)
        self.client.force_login(other)
        with patch.object(self.service.repository, "find_command", side_effect=AssertionError("ownership must be first")):
            self.assertEqual(self.post_json(f"/games/{gid}/commands", payload).status_code, 404)
        self.assertEqual(self.client.get(f"/games/{gid}", HTTP_ACCEPT="application/json").status_code, 404)
        self.client.logout()
        for path in (f"/games/{gid}", "/players/me/progress"):
            self.assertEqual(self.client.get(path, HTTP_ACCEPT="application/json").status_code, 401)
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_command_reuses_owned_state_and_content(self):
        """일반 명령의 상태·콘텐츠는 한 번 읽으며 경합 저장 보호는 그대로 둔다."""
        gid = self.start_game()
        # 보조 기록의 별도 소유권 재확인은 유지한다. 성능 시험과 같은 일반 경로를 검사한다.
        self.service.interactions = None
        payload = {"game_id": gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                   "command": {"kind": "advance", "actor": "mudang"}}
        with patch.object(self.service, 'owned', wraps=self.service.owned) as reads, patch.object(
                self.service, 'dependencies', wraps=self.service.dependencies) as content:
            self.assertEqual(self.post_json(f'/games/{gid}/commands', payload).status_code, 200)
            self.assertEqual(reads.call_count, 1)
            self.assertEqual(content.call_count, 1)

    def test_start_reuses_content_only_inside_each_request_and_keeps_readback(self):
        """신규 시작마다 콘텐츠를 한 번 읽으며 저장된 판 재조회는 생략하지 않는다."""
        with patch.object(self.service, 'dependencies', wraps=self.service.dependencies) as content, patch.object(
                self.service.repository, 'load', wraps=self.service.repository.load) as readback:
            first = self.start_game()
            second = self.start_game()
            self.assertNotEqual(first, second)
            self.assertEqual(content.call_count, 2)
            self.assertEqual(readback.call_count, 2)

    def test_start_readback_different_content_version_is_loaded_again(self):
        """재조회 버전이 다르면 이전 콘텐츠를 재사용하거나 미적재 오류를 숨기지 않는다."""
        from server.paldo_web.composition import GameServiceUnavailable
        gid = self.start_game()
        state, truth = self.service.repository.load(gid)
        deps = self.service.dependencies(state.meta)
        changed = replace(state, meta=replace(state.meta, content_version='unavailable-version'))
        with patch.object(self.service, 'owned', return_value=(changed, truth)), patch.object(
                self.service, 'dependencies', side_effect=GameServiceUnavailable) as load:
            with self.assertRaises(GameServiceUnavailable):
                self.service._detail(gid, state.meta.player_pseudonym, deps)
            load.assert_called_once_with(changed.meta)

    def test_reused_snapshot_rejects_wrong_game_and_owner(self):
        """재사용 경계도 판·소유자가 다르면 멱등 저장을 조회하지 않는다."""
        from server.app.dto import SubmitCommandInput
        from server.api.input_validation import parse_game_input
        from server.app.usecases import ResourceNotFound
        gid = self.start_game()
        state, truth = self.service.repository.load(gid)
        body = {"game_id": gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                "command": {"kind": "advance", "actor": "mudang"}}
        for game_id, player in ((gid, 'other'), (str(uuid.uuid4()), state.meta.player_pseudonym)):
            data = parse_game_input(SubmitCommandInput, dict(body, game_id=game_id))
            with patch.object(self.service.repository, 'find_command', side_effect=AssertionError('ownership first')):
                with self.assertRaises(ResourceNotFound):
                    self.service.command_from_owned(data, player, state, truth)

    def test_replay_conflict_rejection_and_csrf(self):
        """엔진 거절, 저장·중복 재생, 오래된 버전과 CSRF를 검증한다."""
        gid = self.start_game()
        repo = self.service.repository
        initial_events = list(repo.unpublished(gid))
        path = f"/games/{gid}/commands"
        payload = {"game_id": gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                   "command": {"kind": "explain", "actor": "mudang"}}
        self.assertEqual(self.post_json(path, payload).status_code, 422)
        self.assertEqual(repo.load(gid)[0].state_version, 0)
        self.assertIsNone(repo.find_command(gid, payload["command_id"]))
        self.assertEqual(repo.unpublished(gid), initial_events)
        payload["command"]["kind"] = "advance"
        with patch.object(repo, "commit", wraps=repo.commit) as commit:
            accepted = self.post_json(path, payload)
            self.assertEqual(accepted.status_code, 200)
            commit.assert_called_once()
        game_id, expected, state, truth, events, stored = commit.call_args.args
        self.assertEqual((game_id, expected, state.state_version), (gid, 0, 1))
        self.assertEqual(repo.load(gid), (state, truth))
        self.assertTrue(events)
        self.assertEqual([event.event_type for event in repo.unpublished(gid)[len(initial_events):]],
                         [event.event_type for event in events])
        self.assertEqual(repo.find_command(gid, payload["command_id"]), stored)
        with patch.object(self.service.engine, "apply", side_effect=AssertionError("must not apply")):
            replay = self.post_json(path, payload)
            self.assertEqual(replay.status_code, 200)
            self.assertEqual(replay.json(), dict(accepted.json(), replayed=True))
            stale = self.post_json(path, dict(payload, command_id=str(uuid.uuid4())))
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["reject"], "state_version_conflict")
        payload["command_id"] = str(uuid.uuid4())
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(path, data=json.dumps(payload), content_type="application/json").status_code, 403)

    def test_commit_race_replays_only_the_same_id(self):
        """저장 경합 후 같은 ID는 재생하고 다른 ID의 성공은 409로 반환한다."""
        repo = self.service.repository
        commit = repo.commit
        for same_id in (True, False):
            with self.subTest(same_id=same_id):
                gid = self.start_game()
                command_id = str(uuid.uuid4())
                winner_id = command_id if same_id else str(uuid.uuid4())
                body = {"game_id": gid, "command_id": command_id, "expected_state_version": 0,
                        "command": {"kind": "advance", "actor": "mudang"}}

                def competing_write(*args):
                    commit(*args[:-1], replace(args[-1], command_id=winner_id))
                    return commit(*args)

                with patch.object(repo, "commit", side_effect=competing_write):
                    response = self.post_json(f"/games/{gid}/commands", body)
                self.assertEqual(response.status_code, 200 if same_id else 409)
                self.assertEqual(response.json()["replayed"], same_id)
                self.assertEqual(response.json()["current_state_version"], 1)
                self.assertEqual(repo.load(gid)[0].state_version, 1)

    def test_missing_profile_or_service_is_rejected(self):
        """미연결 운영 경계에서 계정 ID나 시험 대역으로 자동 대체하지 않는다."""
        user = User.objects.create_user(username="no-profile")
        self.client.force_login(user)
        self.assertEqual(self.post_json("/games", self.start).status_code, 403)
        self.client.force_login(self.user)
        configure_game_service(None)
        self.assertEqual(self.post_json("/games", self.start).status_code, 503)

    def test_progress_json_includes_owned_game(self):
        """자기 판 시작 뒤 진척 DTO에 해당 판을 반환한다."""
        gid = self.start_game()
        response = self.client.get("/players/me/progress", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["progress"]["game_ids"], [gid])

    def test_game_telemetry_only_logs_verified_game_ids(self):
        """소유한 판의 ID는 기록하고, 검증하지 않은 URL 값은 버린다."""
        gid = self.start_game()
        stream = StringIO()
        handler = StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        LOGGER.addHandler(handler)
        try:
            for tail in ("", "/review", "/commands"):
                path = f"/games/{self.user.username}{tail}"
                response = self.client.post(path) if tail == "/commands" else self.client.get(path)
                self.assertEqual(response.status_code, 404)
            self.assertEqual(self.client.get(f"/games/{gid}").status_code, 200)
        finally:
            LOGGER.removeHandler(handler)
            handler.close()
        self.assertNotIn(self.user.username, stream.getvalue())
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([record["game_id"] for record in records], [None, None, None, gid])

    def test_all_registered_game_endpoints_require_authentication_and_csrf(self):
        """게임 API 열 개의 인증과 모든 변경 경로의 CSRF를 확인한다."""
        gid = self.start_game()
        base = f"/games/{gid}"
        routes = (("post", "/games"), ("get", base),
                  ("post", f"{base}/commands"), ("post", f"{base}/utterances"),
                  ("post", f"{base}/utterances/example"), ("get", f"{base}/renders/example"),
                  ("put", f"{base}/notebook"), ("post", f"{base}/survey"), ("get", f"{base}/review"),
                  ("get", "/players/me/progress"))
        self.client.logout()
        for method, path in routes:
            with self.subTest(path=path):
                request = getattr(self.client, method)
                self.assertEqual(request(path, HTTP_ACCEPT="application/json").status_code, 401)
                ordinary = request(path)
                self.assertEqual(ordinary.status_code, 302)
                self.assertEqual(ordinary["Location"], "/accounts/login")
                partial = request(path, HTTP_HX_REQUEST="true")
                self.assertEqual(partial.status_code, 200)
                self.assertEqual(partial["HX-Redirect"], "/accounts/login")
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        for method, path in routes:
            if method != "get":
                response = csrf.generic(method.upper(), path, "{}", content_type="application/json",
                                        HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 403)
