"""새 판 시작 조회는 이력 순서·삭제·회차·완료 구분을 유지한다."""
import unittest
from types import SimpleNamespace
from server.app.usecases import GameService
from server.infra.mongo_repo import MongoGameRepository


class OpenGameScan(unittest.TestCase):
    """완료 이력을 전량 역직렬화하지 않는 선택적 저장소 기능 회귀."""

    def test_mongo_uses_one_projected_query_and_preserves_progress_order(self):
        """DB 반환 순서가 달라도 진척 순서로 반환하고 상태·진실은 읽지 않는다."""
        calls = []
        collection = SimpleNamespace(find=lambda query, projection:
                                     calls.append((query, projection)) or [{'_id': 'second'}, {'_id': 'first'}])
        repository = object.__new__(MongoGameRepository)
        repository.collection = collection
        history = ['finished', 'first', 'deleted', 'second', 'session-game']
        self.assertEqual(repository.unfinished_open_game_ids(history), ['first', 'second'])
        self.assertEqual(calls, [({'_id': {'$in': history}, 'state.meta.session_id': None,
                                 'state.result': None}, {'_id': 1})])
        self.assertEqual(repository.unfinished_open_game_ids([]), [])
        self.assertEqual(len(calls), 1)

    def test_service_uses_projected_reader_instead_of_per_game_load(self):
        """긴 완료 이력도 저장소 한 번 조회로 넘기며 개별 load를 호출하지 않는다."""
        history = [str(i) for i in range(200)]
        calls = []
        def no_load(game):
            raise AssertionError('individual game load must not run')
        repository = SimpleNamespace(load=no_load)
        service = SimpleNamespace(repository=repository, find_open_games=lambda ids: calls.append(ids) or ['199'],
                                  progress=SimpleNamespace(load=lambda player: SimpleNamespace(game_ids=history)))
        self.assertEqual(GameService.unfinished_open_games(service, 'player'), ['199'])
        self.assertEqual(calls, [history])

    def test_other_repositories_keep_existing_filter_and_missing_game_behavior(self):
        """선택적 기능이 없는 저장소는 기존 상태 기반 필터를 그대로 사용한다."""
        states = {'finished': (None, object()), 'session': ('fixed-session', None),
                  'first': (None, None), 'last': (None, None)}
        def load(game):
            if game not in states:
                raise KeyError(game)
            session, result = states[game]
            return SimpleNamespace(meta=SimpleNamespace(session_id=session), result=result), None
        service = SimpleNamespace(repository=SimpleNamespace(load=load), find_open_games=None, progress=SimpleNamespace(
            load=lambda player: SimpleNamespace(game_ids=['finished', 'first', 'missing', 'session', 'last'])))
        self.assertEqual(GameService.unfinished_open_games(service, 'player'), ['first', 'last'])
