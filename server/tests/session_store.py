"""로컬 미리보기·HTTP 단위 시험 전용 회차 저장 대역."""
import copy
import uuid
from threading import RLock


class MemorySessionStore:
    """운영에 연결하지 않는 단일 프로세스 회차·대응표 저장소."""

    def __init__(self):
        self.sessions, self.identities = {}, {}
        self.lock = RLock()

    def create(self, session):
        """동일 회차 ID는 거부한다."""
        with self.lock:
            if session["session_id"] in self.sessions:
                raise ValueError("session_exists")
            self.sessions[session["session_id"]] = copy.deepcopy(session)

    def list_sessions(self):
        """회차 목록의 사본을 반환한다."""
        return copy.deepcopy(list(self.sessions.values()))

    def get(self, session_id):
        """회차 하나의 사본을 반환한다."""
        return copy.deepcopy(self.sessions[session_id])

    def for_player(self, player):
        """자기 회차만 반환한다."""
        return [session for session in self.list_sessions() if player in session["participants"]]

    def ad_identity(self, player, session_key):
        """동일 서버 세션에는 동일 광고 가명을 배정한다."""
        with self.lock:
            return self.identities.setdefault((player, session_key), str(uuid.uuid4()))

    def ad_identities(self, player):
        """플레이어의 광고 가명 목록을 반환한다."""
        return sorted(value for (owner, _), value in self.identities.items() if owner == player)

    def delete_player(self, player):
        """참가 배정과 대응표만 삭제한다."""
        with self.lock:
            for session in self.sessions.values():
                session["participants"] = [p for p in session["participants"] if p != player]
            self.identities = {key: value for key, value in self.identities.items() if key[0] != player}
