"""C Mongo 포트와 B 회차 정책을 연결한다. 엔진·모델은 호스트가 명시적으로 주입한다."""
from functools import partial
import re
from pymongo.errors import ConnectionFailure
from server.infra.mongo_repo import MongoGameRepository

from .composition import GameServiceUnavailable, configure_game_service, configure_session_service


class WebMongoGameRepository(MongoGameRepository):
    """B 경계에서 Mongo 연결 오류를 응용의 1회 commit 재시도 예외로 변환한다."""

    def commit(self, *args, **kwargs):
        """저장 인자를 그대로 전달하며 여기서는 재시도하거나 엔진을 실행하지 않는다."""
        try:
            return super().commit(*args, **kwargs)
        except ConnectionFailure:
            raise ConnectionError("mongo_commit_unavailable") from None

    def append_events(self, *args, **kwargs):
        """참가자 기록 복구는 B 응용이 맡고 연결 예외만 공통 포트 예외로 바꾼다."""
        try:
            return super().append_events(*args, **kwargs)
        except ConnectionFailure:
            raise ConnectionError('mongo_append_unavailable') from None


class MongoRuntime:
    """공유 연결과 게임·진척·콘텐츠·회차 서비스를 보유하는 실행 자원."""

    def __init__(self, client, database, engine, suspicion, prefix):
        from engine.ports import EngineDeps
        from server.app.usecases import GameService
        from server.app.test_sessions import TestSessionService
        from server.infra.content_store import MongoContentSource
        from server.infra.progress_store import MongoProgressRepository
        from server.infra.test_sessions import MongoSessionStore

        self.client = client
        self.database = database
        self.collection_names = [prefix + name for name in
                                 ("games", "progress", "content", "b_test_sessions", "b_ad_identities")]
        self.content = MongoContentSource(database[self.collection_names[2]])
        self.sessions = TestSessionService(MongoSessionStore(database, prefix))

        def dependencies(meta):
            """적재된 정확한 버전만 읽고 미적재 버전은 공개 서비스 오류로 변환한다."""
            try:
                content = self.content.load(meta.content_version)
            except KeyError:
                raise GameServiceUnavailable("content_version_unavailable") from None
            return EngineDeps(content, suspicion)

        self.game = GameService(engine, WebMongoGameRepository(database[self.collection_names[0]]),
                                MongoProgressRepository(database[self.collection_names[1]]),
                                dependencies)
        from .composition import start_policy
        self.game.prepare_start = start_policy(partial(self.sessions.prepare, load_game=self.game.repository.load))

    def configure(self):
        """HTTP 처리 전 이 실행 자원을 B의 전역 서비스 조립에 연결한다."""
        configure_session_service(self.sessions)
        configure_game_service(self.game)

    def factory_service(self):
        """기존 GAME_SERVICE_FACTORY 안에서는 게임 잠금을 다시 잡지 않고 반환한다."""
        configure_session_service(self.sessions)
        return self.game

    def close(self):
        """요청 처리가 끝난 뒤 이 실행 자원의 공유 Mongo 연결을 닫는다."""
        self.client.close()


def open_mongo_runtime(*, engine, suspicion, uri=None, collection_prefix=""):
    """설정 URI의 Replica Set에 명시적으로 연결한다. 시험은 고유 prefix로 분리한다."""
    from django.conf import settings
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    if not re.fullmatch(r"[a-zA-Z0-9_]{0,80}", collection_prefix):
        raise ValueError("invalid_collection_prefix")
    uri = uri if uri is not None else settings.SESSION_MONGO_URI
    if not uri:
        raise GameServiceUnavailable("mongo_not_configured")
    client = None
    try:
        client = MongoClient(uri, timeoutMS=5000, serverSelectionTimeoutMS=5000)
        if not client.admin.command("hello").get("setName"):
            raise GameServiceUnavailable("mongo_replica_set_required")
        from pymongo.read_concern import ReadConcern
        from pymongo.write_concern import WriteConcern
        database = client.get_default_database("paldo_web", read_concern=ReadConcern("majority"),
                                               write_concern=WriteConcern(w="majority"))
        return MongoRuntime(client, database, engine, suspicion, collection_prefix)
    except (PyMongoError, ValueError):
        if client is not None:
            client.close()
        raise GameServiceUnavailable("mongo_connection_unavailable") from None
    except Exception:
        if client is not None:
            client.close()
        raise
