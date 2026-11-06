"""명시 실행하는 로컬 Mongo B 회차 저장·재연결·동시 광고 가명 시험."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings
from django.test import SimpleTestCase
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri
from server.app.test_sessions import TestSessionService
from server.infra.test_sessions import MongoSessionStore


def _local_client():
    uri = settings.MONGO_TEST_URI
    if not uri.startswith("mongodb://"):
        raise ValueError("Mongo 시험은 로컬 접속만 지원합니다.")
    parsed = parse_uri(uri)
    if any(host not in {"127.0.0.1", "localhost", "::1"} for host, _ in parsed["nodelist"]):
        raise ValueError("Mongo 시험은 로컬 접속만 지원합니다.")
    return MongoClient(uri, serverSelectionTimeoutMS=2000, directConnection=True)


class MongoSessions(SimpleTestCase):
    """C 게임 저장 코드 없이 B 소유 두 컬렉션을 실제 Mongo에서 검증한다."""

    def test_reconnect_concurrency_and_player_deletion(self):
        """새 연결의 회차 보존·동시 20회 동일 가명·선택적 삭제를 확인한다."""
        database_name = "test_paldo_b_sessions_" + uuid.uuid4().hex
        player, other, browser = (str(uuid.uuid4()) for _ in range(3))
        with _local_client() as client:
            try:
                store = MongoSessionStore(client[database_name])
                service = TestSessionService(store)
                service.create("nov06", [{"preset_id": "base6", "seed": 7}], [player, other],
                               {"rule_version": "v1", "engine_version": "scripted", "content_version": "fixture"})
                with ThreadPoolExecutor(max_workers=20) as pool:
                    identities = list(pool.map(lambda _: service.ad_identity(player, browser), range(20)))
                self.assertEqual(len(set(identities)), 1)
                other_id = service.ad_identity(other, browser)
                with _local_client() as reconnected:
                    new = MongoSessionStore(reconnected[database_name])
                    self.assertEqual(new.get("nov06")["plans"], [{"preset_id": "base6", "seed": 7}])
                    self.assertEqual(new.ad_identity(player, browser), identities[0])
                    self.assertEqual(new.ad_identities(player), [identities[0]])
                    new.delete_player(player)
                    new.delete_player(player)
                    self.assertEqual(new.ad_identities(player), [])
                    self.assertEqual(new.ad_identities(other), [other_id])
                    self.assertEqual(new.get("nov06")["participants"], [other])
                    self.assertEqual(new.for_player(player), [])
                    self.assertEqual(len(new.for_player(other)), 1)
                info = client.server_info()
                print(f'MongoDB {info["version"]}: 회차 재연결, 광고 가명 동시 발급 20건, 참가자별 삭제 확인')
            finally:
                client.drop_database(database_name)
