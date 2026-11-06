"""B 소유 테스트 회차·광고 가명 대응표의 Mongo 어댑터. C 게임 저장소와 독립적이다."""
import uuid
from functools import wraps

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.write_concern import WriteConcern
from server.app.usecases import StartUnavailable


def _store_operation(operation):
    @wraps(operation)
    def run(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except PyMongoError:
            raise StartUnavailable from None
    return run


@_store_operation
def connect_session_store(uri):
    """계약의 연결 문자열로 Mongo에 접속하고 회차 저장소를 준비한다."""
    client = MongoClient(uri, timeoutMS=2000, serverSelectionTimeoutMS=2000)
    try:
        database = client.get_default_database("paldo_web", write_concern=WriteConcern(w="majority"))
        return MongoSessionStore(database)
    except PyMongoError:
        client.close()
        raise


class MongoSessionStore:
    """지정 DB의 b_test_sessions·b_ad_identities만 사용한다."""

    def __init__(self, database):
        self.sessions = database["b_test_sessions"]
        self.identities = database["b_ad_identities"]
        self.sessions.create_index("participants")
        self.identities.create_index("player_pseudonym")
        self.identities.create_index("session_pseudonym", unique=True)

    @_store_operation
    def create(self, session):
        """회차 ID의 고유 키로 중복 생성과 덮어쓰기를 막는다."""
        try:
            self.sessions.insert_one({"_id": session["session_id"], **session})
        except DuplicateKeyError:
            raise ValueError("session_exists") from None

    @_store_operation
    def list_sessions(self):
        """운영자 회차 목록을 ID순으로 읽는다."""
        return list(self.sessions.find({}, {"_id": 0}).sort("session_id", 1))

    @_store_operation
    def get(self, session_id):
        """고정 시드 목록과 참가자 가명을 조회한다."""
        value = self.sessions.find_one({"_id": session_id}, {"_id": 0})
        if value is None:
            raise KeyError(session_id)
        return value

    @_store_operation
    def for_player(self, player):
        """배정된 회차만 읽는다."""
        return list(self.sessions.find({"participants": player}, {"_id": 0}).sort("session_id", 1))

    @_store_operation
    def ad_identity(self, player, session_key):
        """동시 요청도 같은 UUID를 읽도록 upsert의 고유 키를 사용한다."""
        key = player + ":" + session_key
        value = {"player_pseudonym": player, "session_pseudonym": str(uuid.uuid4())}
        try:
            result = self.identities.find_one_and_update({"_id": key}, {"$setOnInsert": value}, upsert=True,
                                                        return_document=ReturnDocument.AFTER)
        except DuplicateKeyError:
            result = self.identities.find_one({"_id": key})
            if result is None:
                raise
        return result["session_pseudonym"]

    @_store_operation
    def ad_identities(self, player):
        """삭제 대상 광고 UUID 목록을 계정 정보 없이 반환한다."""
        return sorted(row["session_pseudonym"] for row in self.identities.find({"player_pseudonym": player}))

    @_store_operation
    def delete_player(self, player):
        """C가 광고 기록을 지운 다음 호출할 멱등 삭제 경계."""
        self.sessions.update_many({"participants": player}, {"$pull": {"participants": player}})
        self.identities.delete_many({"player_pseudonym": player})
