"""응용의 경합·결과 불명확 재시도 계약. 실제 Mongo 동시성 검증은 별도다."""
import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from engine.domain.model import Command, CommandKind
from engine.ports import VersionConflict
from server.app.dto import StartGameInput, SubmitCommandInput
from .support import make_service


class CommandProtocol(TestCase):
    """엔진을 다시 적용하지 않고 저장 결과만 재확인하는지 검사한다."""

    def setUp(self):
        """각 시험에 독립된 계정·게임·저장소를 준비한다."""
        self.service = make_service()
        game = self.service.start(StartGameInput("player", "base6", "test-session", "f"), "player", True)
        self.data = SubmitCommandInput(game.view.game_id, "command", 0, Command(CommandKind.ADVANCE, "mudang"))

    def test_ambiguous_success_retries_same_commit_and_replays(self):
        """불명확한 저장 성공은 동일 commit 재전송 후 replay로 처리한다."""
        commit = self.service.repository.commit
        calls = []
        def uncertain(*args):
            calls.append(args)
            commit(*args)
            if len(calls) == 1:
                raise TimeoutError
        with patch.object(self.service.repository, "commit", side_effect=uncertain), patch.object(self.service.engine, "apply", wraps=self.service.engine.apply) as apply:
            result = self.service.command(self.data, "player")
        self.assertTrue(result.replayed)
        self.assertEqual(len(calls), 2)
        self.assertEqual(apply.call_count, 1)
        self.assertIs(calls[0][2], calls[1][2])

    def test_other_command_wins_race(self):
        """다른 명령의 저장 성공 뒤 경합이면 최신 버전 충돌을 반환한다."""
        commit = self.service.repository.commit
        def competing(*args):
            commit(*args[:-1], replace(args[-1], command_id="competitor"))
            raise VersionConflict(0, 1)
        with patch.object(self.service.repository, "commit", side_effect=competing), patch.object(
                self.service.interactions, "register_renders", side_effect=AssertionError("losing transition")), patch.object(
                self.service.interactions, "cancel_previous", side_effect=AssertionError("losing transition")):
            result = self.service.command(self.data, "player")
        self.assertFalse(result.accepted)
        self.assertEqual(result.current_state_version, 1)
        self.assertEqual(result.reject.value, "state_version_conflict")

    def test_replay_never_applies_engine(self):
        """이미 저장된 명령은 엔진을 다시 호출하지 않는다."""
        first = self.service.command(self.data, "player")
        with patch.object(self.service.engine, "apply", side_effect=AssertionError):
            replay = self.service.command(self.data, "player")
        self.assertEqual(replace(replay, replayed=False), first)

    def test_different_saved_response_does_not_use_losing_render_work(self):
        """같은 ID의 다른 결과가 저장됐으면 그 응답만 재생하고 패배한 렌더는 버린다."""
        commit = self.service.repository.commit
        def competing(*args):
            body = json.loads(args[-1].response_json)
            body["pending_render_jobs"] = ["winner-job"]
            commit(*args[:-1], replace(args[-1], response_json=json.dumps(body)))
            raise VersionConflict(0, 1)
        with patch.object(self.service.repository, "commit", side_effect=competing), patch.object(
                self.service.interactions, "register_renders", side_effect=AssertionError("losing render")), patch.object(
                self.service.interactions, "cancel_previous", side_effect=AssertionError("losing render")):
            result = self.service.command(self.data, "player")
        self.assertTrue(result.replayed)
        self.assertEqual(result.pending_render_jobs, ["winner-job"])
