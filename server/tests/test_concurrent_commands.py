"""원자 저장 포트 대역으로 20개 HTTP 명령의 B 경합 처리를 검증한다. Mongo 시험은 별도다."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connections
from django.test import Client, TransactionTestCase

from engine.testing.fakes import InMemoryGameRepository
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, issue_test_csrf, make_service


class AtomicRepositoryDouble(InMemoryGameRepository):
    """시험에서만 공통 메모리 가짜에 포트 계약의 원자적 읽기·commit을 제공한다."""

    def __init__(self):
        super().__init__()
        self.lock = threading.RLock()

    def load(self, game_id):
        """다른 commit 도중의 중간 상태를 읽지 않는다."""
        with self.lock:
            return super().load(game_id)

    def find_command(self, game_id, command_id):
        """상태와 함께 저장된 명령 기록을 조회한다."""
        with self.lock:
            return super().find_command(game_id, command_id)

    def commit(self, *args):
        """C 포트가 약속한 버전 검사·상태·명령 저장을 한 번에 수행한다."""
        with self.lock:
            return super().commit(*args)


class ConcurrentCommands(TransactionTestCase):
    """서로 다른 DB 연결의 HTTP 요청을 같은 판·같은 기대 버전에 동시에 보낸다."""

    def setUp(self):
        """계정과 원자 저장 대역을 준비하고 실제 CSRF 쿠키를 얻는다."""
        user = User.objects.create_user(username="concurrent-player")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        self.csrf_token = issue_test_csrf(self.client)
        self.service = self.make_game_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}),
                                    content_type="application/json", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        self.gid = response.json()["view"]["game_id"]

    def make_game_service(self):
        """기본 시험은 메모리 대역을 사용하고 Mongo 시험에서 저장소를 교체한다."""
        return make_service(repository=AtomicRepositoryDouble())

    def race(self, command_ids):
        """20개 요청이 전이에 도달한 뒤 저장 경합을 시작하도록 동기화한다."""
        ready = threading.Barrier(20)
        engine_apply = self.service.engine.apply
        cookies = {name: value.value for name, value in self.client.cookies.items()}

        def simultaneous_apply(*args):
            """모든 요청이 같은 버전을 읽었음을 보장한 뒤 엔진을 호출한다."""
            ready.wait(timeout=15)
            return engine_apply(*args)

        def request_command(command_id):
            """요청별 클라이언트·DB 연결을 사용하고 응답 뒤 연결을 닫는다."""
            client = Client(enforce_csrf_checks=True)
            for name, value in cookies.items():
                client.cookies[name] = value
            try:
                response = client.post(f"/games/{self.gid}/commands", json.dumps({
                    "game_id": self.gid, "command_id": command_id, "expected_state_version": 0,
                    "command": {"kind": "advance", "actor": "mudang"},
                }), content_type="application/json", HTTP_ACCEPT="application/json", HTTP_X_CSRFTOKEN=self.csrf_token)
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with patch.object(self.service.engine, "apply", side_effect=simultaneous_apply), ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(request_command, command_ids))
        repository = self.service.repository
        version = repository.load(self.gid)[0].state_version
        self.assertEqual(version, 1)
        stored = {cid for cid in set(command_ids) if repository.find_command(self.gid, cid) is not None}
        accepted = {cid for cid, (status, _) in zip(command_ids, results, strict=True) if status == 200}
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored, accepted)
        print(json.dumps({
            "scenario": "same_command" if len(set(command_ids)) == 1 else "distinct_commands",
            "requests": len(results),
            "accepted": sum(status == 200 and not output["replayed"] for status, output in results),
            "replayed": sum(status == 200 and output["replayed"] for status, output in results),
            "conflicts": sum(status == 409 for status, _ in results),
            "saved_state_version": version,
        }), flush=True)
        return results

    def test_twenty_distinct_commands_accept_only_one(self):
        """20개 서로 다른 명령 중 1개 수락, 19개 409, 상태 증가는 1회다."""
        results = self.race([str(uuid.uuid4()) for _ in range(20)])
        self.assertEqual(sum(status == 200 for status, _ in results), 1)
        self.assertEqual(sum(status == 409 for status, _ in results), 19)
        for status, output in results:
            self.assertEqual(output["current_state_version"], 1)
            self.assertEqual(output["accepted"], status == 200)

    def test_twenty_identical_commands_return_saved_response(self):
        """같은 명령 20개는 최초 1개와 저장 응답 재생 19개로 처리한다."""
        results = self.race([str(uuid.uuid4())] * 20)
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sum(output["replayed"] for _, output in results), 19)
        first = next(output for _, output in results if not output["replayed"])
        for _, output in results:
            self.assertEqual(dict(output, replayed=False), first)
