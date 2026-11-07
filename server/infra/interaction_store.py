"""B 보조 기록(자유 입력 시도·렌더·수첩·설문, 명령 후속 처리 계획)의 Mongo 저장(A-07-03 운영 조립).

계층: 인프라. `server.app.interactions.InteractionStore` 포트를 구현하며 규칙은 두지 않는다.
판 하나가 문서 하나다: `{_id: game_id, state, state_v, effects, effects_v}`. `edit`와 `edit_effects`는 서로 다른
필드를 각자의 버전으로 CAS 저장하므로 한쪽 롤백이 다른 쪽에 영향을 주지 않는다.

- 같은 프로세스 안에서는 판별 재진입 잠금으로 직렬화한다. 같은 스레드의 중첩 호출(예: 카드 확정 → 명령 → 렌더
  등록)은 바깥 호출이 읽은 같은 객체를 받고, 저장은 가장 바깥 호출이 끝날 때 한 번 한다. 예외 시 중첩 호출의
  변경만 되돌리고, 가장 바깥 호출의 예외는 저장하지 않는다(시험 대역 `MemoryInteractionStore`와 같은 의미).
- 다른 프로세스와는 판 문서의 임대(`lease`, 기본 30초, 최대 5초 대기)로 직렬화한다. 그래서 변경 안의 부수 효과(이벤트 추가·카드 확정
  명령)도 한 번에 한 프로세스만 실행한다. 저장은 자기 임대 표·필드 버전이 맞을 때만 하고, 아니면 `InteractionUnavailable`(덮어쓰지 않음).
  한계: 한 변경이 임대 시간(30초)을 넘기면 그 사이 다른 프로세스의 부수 효과와 겹칠 수 있다(저장만 막는다. 요청은 수 ms~수백 ms).
- 값은 허용한 모듈(`engine.`, `server.app.`)의 dataclass·enum과 기본 타입만 형식 표시를 붙여 JSON으로 저장한다.
  pickle은 쓰지 않는다.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import importlib
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from engine.domain.serde import from_jsonable, to_jsonable
from server.app.interactions import InteractionState, InteractionUnavailable
from server.infra.mongo_repo import majority

ALLOWED_MODULES = ("engine.", "server.app.")


def _type_path(tp: type) -> str:
    return f"{tp.__module__}:{tp.__qualname__}"


def _resolve(path: str) -> type:
    module, _, name = path.partition(":")
    if not module.startswith(ALLOWED_MODULES) or not name or "." in name:
        raise ValueError(f"disallowed_type:{path}")
    tp = getattr(importlib.import_module(module), name)
    if not isinstance(tp, type) or not (dataclasses.is_dataclass(tp) or issubclass(tp, enum.Enum)):
        raise ValueError(f"disallowed_type:{path}")
    return tp


def encode(value):
    """보조 기록 값을 형식 표시가 붙은 JSON 값으로 바꾼다. 모르는 타입은 TypeError."""
    if value is None or isinstance(value, (bool, int, float, str)) and not isinstance(value, enum.Enum):
        return value
    if isinstance(value, enum.Enum):
        _resolve(_type_path(type(value)))
        return {"__enum__": _type_path(type(value)), "v": value.value}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        _resolve(_type_path(type(value)))
        return {"__dc__": _type_path(type(value)), "v": to_jsonable(value)}
    if isinstance(value, tuple):
        return {"__tuple__": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {"__dict__": [[encode(k), encode(v)] for k, v in value.items()]}
    raise TypeError(f"unsupported_value:{type(value).__name__}")


def decode(value):
    """`encode`의 역변환. 허용하지 않은 모듈의 형식 표시는 ValueError."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "__enum__" in value:
        return _resolve(value["__enum__"])(value["v"])
    if "__dc__" in value:
        return from_jsonable(_resolve(value["__dc__"]), value["v"])
    if "__tuple__" in value:
        return tuple(decode(v) for v in value["__tuple__"])
    if "__dict__" in value:
        return {decode(k): decode(v) for k, v in value["__dict__"]}
    raise ValueError("untyped_mapping")


def _encode_state(state: InteractionState):
    return encode({f.name: getattr(state, f.name) for f in dataclasses.fields(state)})


def _decode_state(value) -> InteractionState:
    return InteractionState(**decode(value))


def _restore(value, before) -> None:
    if isinstance(value, dict):
        value.clear()
        value.update(before)
    else:
        value.__dict__.update(before.__dict__)


class MongoInteractionStore:
    """`InteractionStore` 포트의 Mongo 구현. 판 삭제 시 문서 삭제는 판 저장 정리와 함께 한다(C 인수 대상)."""

    def __init__(self, collection, lease_s: float = 30.0, wait_s: float = 5.0):
        self.collection = majority(collection)
        self.lease_s, self.wait_s = lease_s, wait_s
        self._locks: dict[str, threading.RLock] = {}
        self._guard = threading.Lock()
        self._local = threading.local()

    def _lock(self, game_id: str) -> threading.RLock:
        with self._guard:
            return self._locks.setdefault(game_id, threading.RLock())

    def _open(self) -> dict:
        if not hasattr(self._local, "open"):
            self._local.open = {}
        return self._local.open

    def _held(self) -> dict:
        if not hasattr(self._local, "held"):
            self._local.held = {}
        return self._local.held

    @contextmanager
    def _hold(self, game_id):
        """프로세스 안 판별 잠금과 Mongo 임대(lease)를 함께 잡는다. 같은 스레드의 재진입은 한 번만 잡는다.

        임대가 보조 기록 변경과 그 안의 부수 효과(이벤트 추가, 카드 확정 명령)를 프로세스 사이에서도 직렬화한다.
        임대 표(token)를 돌려주고, 저장은 그 표를 가진 임대일 때만 한다(만료 뒤 다른 프로세스가 잡았으면 거절).
        """
        held = self._held()
        with self._lock(game_id):
            if game_id in held:
                yield held[game_id]
                return
            token = self._acquire(game_id)
            held[game_id] = token
            stop = threading.Event()
            renewer = threading.Thread(target=self._renew_loop, args=(game_id, token, stop),
                                       name=f"interaction-lease-{game_id}", daemon=True)
            renewer.start()
            try:
                yield token
            finally:
                stop.set()
                renewer.join()
                del held[game_id]
                self._release(game_id, token)

    def _renew_loop(self, game_id, token, stop: threading.Event) -> None:
        """변경이 끝날 때까지 임대 시간의 1/3마다 자기 임대를 연장한다.

        느린 요청이 살아 있는 동안 다른 프로세스가 만료된 임대를 잡아 같은 부수 효과를 겹쳐 실행하지 않게 한다.
        Mongo 일시 오류는 짧은 간격으로 다시 시도하고, 임대를 다른 프로세스가 가져갔을 때만 멈춘다. 그 뒤 저장은
        `_save`의 임대 표 확인이 거절한다. Mongo가 임대 시간 내내 응답하지 않으면 연장도 못 하지만, 그때는 같은
        Mongo에 쓰는 게임 명령·이벤트 저장도 실패한다(남은 한계는 `docs/modules/web.md` 한계).
        """
        delay = self.lease_s / 3
        while not stop.wait(delay):
            renewed = self._renew(game_id, token)
            if renewed is False:
                return
            delay = self.lease_s / 3 if renewed else min(1.0, self.lease_s / 10)

    def _renew(self, game_id, token):
        """연장하면 True, 임대를 잃었으면 False, Mongo 오류면 None(다시 시도)."""
        from pymongo.errors import PyMongoError
        until = datetime.now(timezone.utc) + timedelta(seconds=self.lease_s)
        try:
            result = self.collection.update_one({"_id": game_id, "lease.owner": token}, {"$set": {"lease.until": until}})
        except PyMongoError:
            return None
        return result.matched_count == 1

    def _acquire(self, game_id) -> str:
        from pymongo.errors import DuplicateKeyError, PyMongoError
        token = uuid.uuid4().hex
        deadline = time.monotonic() + self.wait_s
        while True:
            now = datetime.now(timezone.utc)
            free = {"$or": [{"lease": {"$exists": False}}, {"lease.until": {"$lt": now}}]}
            lease = {"owner": token, "until": now + timedelta(seconds=self.lease_s)}
            try:
                self.collection.update_one({"_id": game_id, **free}, {"$set": {"lease": lease}}, upsert=True)
                return token
            except DuplicateKeyError:
                pass                                    # 다른 프로세스가 임대 중
            except PyMongoError as error:
                raise InteractionUnavailable from error
            if time.monotonic() >= deadline:
                raise InteractionUnavailable("interaction_busy")
            time.sleep(0.02)

    def _release(self, game_id, token) -> None:
        from pymongo.errors import PyMongoError
        try:
            self.collection.update_one({"_id": game_id, "lease.owner": token}, {"$unset": {"lease": ""}})
        except PyMongoError:
            pass                                        # 풀지 못한 임대는 만료로 풀린다

    @contextmanager
    def edit(self, game_id):
        """같은 판의 호출을 직렬화하고 예외 시 보조 변경을 되돌린다."""
        with self._edit(game_id, "state", InteractionState, _decode_state, _encode_state) as value:
            yield value

    @contextmanager
    def edit_effects(self, game_id):
        """명령 후속 처리 계획을 `edit`와 독립된 필드·버전으로 보존한다."""
        with self._edit(game_id, "effects", dict, decode, encode) as value:
            yield value

    @contextmanager
    def _edit(self, game_id, field, empty, load, dump):
        opened = self._open()
        key = (game_id, field)
        if key in opened:
            value = opened[key]
            before = copy.deepcopy(value)
            try:
                yield value
            except Exception:
                _restore(value, before)
                raise
            return
        with self._hold(game_id) as token:
            version_field = field + "_v"
            from pymongo.errors import PyMongoError
            try:
                document = self.collection.find_one({"_id": game_id}, {field: 1, version_field: 1})
            except PyMongoError as error:
                raise InteractionUnavailable from error
            version = document.get(version_field, 0) if document else 0
            stored = document.get(field) if document else None
            value = load(stored) if stored is not None else empty()
            opened[key] = value
            try:
                yield value
            finally:
                del opened[key]
            encoded = dump(value)
            if encoded != stored:
                self._save(game_id, field, version, encoded, token)

    def _save(self, game_id, field, version, encoded, token) -> None:
        from pymongo.errors import DuplicateKeyError, PyMongoError
        version_field = field + "_v"
        match = {"$exists": False} if version == 0 else version
        try:
            result = self.collection.update_one({"_id": game_id, "lease.owner": token, version_field: match},
                                                {"$set": {field: encoded, version_field: version + 1}}, upsert=True)
        except DuplicateKeyError:
            raise InteractionUnavailable("interaction_version_conflict") from None
        except PyMongoError as error:
            raise InteractionUnavailable from error
        if result.matched_count == 0 and result.upserted_id is None:
            raise InteractionUnavailable("interaction_version_conflict")
