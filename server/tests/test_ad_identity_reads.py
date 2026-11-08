"""광고 가명의 조회 우선 경로와 동시 최초 생성 복구를 검증한다."""
from unittest import TestCase
from unittest.mock import MagicMock
from pymongo.errors import DuplicateKeyError
from server.infra.test_sessions import MongoSessionStore


class IdentityReads(TestCase):
    """영구 캐시 없이 매번 저장소의 현재 대응표를 확인한다."""

    def test_existing_identity_never_updates_and_deleted_identity_is_not_cached(self):
        """기존 UUID 조회는 갱신을 하지 않고 삭제 뒤 조회는 신규 생성한다."""
        store = MongoSessionStore(MagicMock())
        store.identities.find_one.side_effect = [{'session_pseudonym': 'existing'}, None]
        store.identities.find_one_and_update.return_value = {'session_pseudonym': 'new'}
        self.assertEqual(store.ad_identity('player', 'session'), 'existing')
        store.identities.find_one_and_update.assert_not_called()
        self.assertEqual(store.ad_identity('player', 'session'), 'new')
        store.identities.find_one_and_update.assert_called_once()

    def test_concurrent_create_returns_winner_and_missing_winner_fails(self):
        """동시 최초 생성의 고유 키 경합은 승자의 UUID로 복구한다."""
        store = MongoSessionStore(MagicMock())
        store.identities.find_one.side_effect = [None, {'session_pseudonym': 'winner'}, None, None]
        store.identities.find_one_and_update.side_effect = DuplicateKeyError('race')
        self.assertEqual(store.ad_identity('player', 'session'), 'winner')
        from server.app.usecases import StartUnavailable
        with self.assertRaises(StartUnavailable):
            store.ad_identity('player', 'session')
