"""B Mongo 조립의 설정·실패 격리. 실제 DB 시험은 mongo_game_sessions에서 수행한다."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, override_settings
from pymongo.errors import ExecutionTimeout, OperationFailure, ServerSelectionTimeoutError
from server.paldo_web.composition import (GameServiceUnavailable, configure_game_service,
                                         configure_session_service, get_game_service, get_session_service)
from server.paldo_web.mongo import MongoRuntime, WebMongoGameRepository, open_mongo_runtime


class MongoComposition(SimpleTestCase):
    """접속 정보 노출과 시험 대역 자동 주입 없이 명시적인 설정만 받는다."""

    def test_open_game_query_is_bounded_to_ids_and_preserves_input_order(self):
        repository = object.__new__(WebMongoGameRepository)
        repository.collection = Mock()
        repository.collection.find.return_value = [{'_id': 'b'}, {'_id': 'a'}]
        self.assertTrue(callable(getattr(repository, 'unfinished_open_game_ids', None)))
        with patch('server.infra.mongo_repo.from_jsonable', side_effect=AssertionError('full state read')):
            self.assertEqual(repository.unfinished_open_game_ids(['a', 'missing', 'b', 'a']), ['a', 'b', 'a'])
        repository.collection.find.assert_called_once_with(
            {'_id': {'$in': ['a', 'missing', 'b', 'a']}, 'state.meta.session_id': None, 'state.result': None},
            {'_id': 1})
        repository.collection.find.reset_mock()
        self.assertEqual(repository.unfinished_open_game_ids([]), [])
        repository.collection.find.assert_not_called()

    def test_service_uses_injected_open_query_without_loading_each_state(self):
        from server.app.usecases import GameService
        state = SimpleNamespace(meta=SimpleNamespace(session_id=None), result=None)
        repository = SimpleNamespace(load=Mock(return_value=(state, None)))
        progress = SimpleNamespace(load=Mock(return_value=SimpleNamespace(game_ids=['a', 'b'])))
        service = GameService(None, repository, progress, None)
        service.find_open_games = Mock(return_value=['a', 'b'])
        self.assertEqual(service.unfinished_open_games('player'), ['a', 'b'])
        service.find_open_games.assert_called_once_with(['a', 'b'])
        repository.load.assert_not_called()

    def test_mongo_runtime_connects_open_query(self):
        database = Mock()
        database.__getitem__ = Mock(return_value=Mock())
        runtime = MongoRuntime(Mock(), database, object(), None, 'probe_')
        self.assertIs(getattr(getattr(runtime.game, 'find_open_games', None), '__self__', None), runtime.game.repository)

    def test_non_connection_failures_are_not_converted_to_retryable_errors(self):
        """권한·실행 제한·입력 오류를 일시 연결 오류로 바꾸지 않는다."""
        repository = object.__new__(WebMongoGameRepository)
        for error in (OperationFailure('denied', code=13), ExecutionTimeout('execution limit'), ValueError('invalid')):
            with self.subTest(error=type(error).__name__), patch(
                    'server.infra.mongo_repo.MongoGameRepository.commit', side_effect=error) as commit:
                with self.assertRaises(type(error)) as caught:
                    repository.commit('same-arguments')
                self.assertIs(caught.exception, error)
                commit.assert_called_once_with('same-arguments')

    @override_settings(GAME_SERVICE_FACTORY='test.factory')
    def test_existing_factory_uses_same_game_and_sessions_without_relocking(self):
        """기존 lazy 팩토리의 게임 잠금 안에서도 같은 자원을 안전하게 설치한다."""
        from .support import make_service
        runtime = object.__new__(MongoRuntime)
        runtime.game, runtime.sessions = make_service(), object()
        configure_game_service(None)
        configure_session_service(None)
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        with patch('server.paldo_web.composition.import_string', return_value=runtime.factory_service):
            self.assertIs(get_game_service(), runtime.game)
            self.assertIs(get_game_service(), runtime.game)
            self.assertIs(get_session_service(), runtime.sessions)

    @override_settings(SESSION_MONGO_URI="")
    def test_missing_uri_and_invalid_prefix_do_not_connect(self):
        """미설정과 잘못된 시험 영역은 네트워크 호출 전에 거부한다."""
        with patch('pymongo.MongoClient') as client:
            with self.assertRaises(GameServiceUnavailable):
                open_mongo_runtime(engine=object(), suspicion=None)
            with self.assertRaises(ValueError):
                open_mongo_runtime(engine=object(), suspicion=None, collection_prefix='../other')
            client.assert_not_called()

    def test_standalone_is_rejected_and_closed(self):
        """콘텐츠 트랜잭션을 지원할 Replica Set 없이 조립하지 않는다."""
        with patch('pymongo.MongoClient') as factory:
            client = factory.return_value
            client.admin.command.return_value = {'ok': 1}
            with self.assertRaisesRegex(GameServiceUnavailable, 'mongo_replica_set_required'):
                open_mongo_runtime(engine=object(), suspicion=None, uri='mongodb://localhost')
            client.close.assert_called_once()

    def test_connection_failure_is_sanitized(self):
        """드라이버 오류에 접속 주소가 포함돼도 공개 오류에는 전달하지 않는다."""
        with patch('pymongo.MongoClient') as factory:
            factory.return_value.admin.command.side_effect = ServerSelectionTimeoutError('private-connection-detail')
            with self.assertRaises(GameServiceUnavailable) as caught:
                open_mongo_runtime(engine=object(), suspicion=None, uri='mongodb://localhost')
            self.assertEqual(str(caught.exception), 'mongo_connection_unavailable')
            factory.return_value.close.assert_called_once()
