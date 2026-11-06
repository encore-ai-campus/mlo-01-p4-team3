"""B 보조 저장의 시험 전용 대역. 운영·재시작 보존 용도로 사용하지 않는다."""
import copy
from contextlib import contextmanager
from threading import RLock
from server.app.interactions import InteractionState


class MemoryInteractionStore:
    """한 프로세스의 판별 보조 요청을 직렬화하는 시험 저장소."""

    def __init__(self):
        self.records = {}
        self.effects = {}
        self.lock = RLock()

    @contextmanager
    def edit(self, game_id):
        """예외 시 보조 기록을 복구한다. 외부 게임 저장과의 트랜잭션은 아니다."""
        with self.lock:
            value = self.records.setdefault(game_id, InteractionState())
            before = copy.deepcopy(value)
            try:
                yield value
            except Exception:
                value.__dict__.update(before.__dict__)
                raise

    @contextmanager
    def edit_effects(self, game_id):
        """카드 기록 롤백과 독립된 명령 후속 처리 계획의 시험 대역."""
        with self.lock:
            value = self.effects.setdefault(game_id, {})
            before = copy.deepcopy(value)
            try:
                yield value
            except Exception:
                value.clear()
                value.update(before)
                raise
