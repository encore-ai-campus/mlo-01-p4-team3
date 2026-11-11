"""인프라: 운영 설정 문서 읽기·쓰기(E-11-13, `npc_llm_design.md` 4절 D4·D5).

`<접두어>ops_settings` 컬렉션의 `_id="npc_llm"` 문서 한 곳에 NPC 대사 LLM 모드(`off|polish|live`)와
실시간 마감(`live_deadline_ms`)·다듬기 대기 상한(`polish_queue_max`)을 둔다. 위에 env `PALDO_NPC_LLM_URL`이
상한 스위치로 있어, 주소가 비면 문서와 관계없이 off다. 문서가 없으면 env `PALDO_NPC_LLM_MODE`, 그것도 없으면 off.

- 웹 프로세스는 `current()`(10초 캐시)를, 작업 프로세스는 `fresh()`(캐시 없음)를 쓴다.
- 읽기에 실패하면 마지막으로 읽은 값을 쓰고, 60초 넘게 못 읽으면 off로 본다.
- 문서는 관리 명령 `npc_llm_mode`만 고친다(`set_mode`).
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from pymongo.errors import PyMongoError

from server.app.npc_lines import MODES, NpcLlmConfig, resolve_mode

COLLECTION = "ops_settings"
DOC_ID = "npc_llm"
TTL_S = 10.0
STALE_S = 60.0


class MongoOpsSettings:
    """`OpsSettings` 포트의 Mongo 구현."""

    def __init__(self, collection, url: str, env_mode: str = "", ttl: float = TTL_S, stale: float = STALE_S,
                 clock: Callable[[], float] = time.monotonic):
        self.c, self.url, self.env_mode, self.ttl, self.stale, self.clock = collection, url, env_mode, ttl, stale, clock
        self._cached: Optional[NpcLlmConfig] = None
        self._read_at = float("-inf")
        self._ok_at = float("-inf")
        self._lock = threading.Lock()

    def _parse(self, doc: Optional[dict]) -> NpcLlmConfig:
        doc = doc or {}
        base = NpcLlmConfig()
        return NpcLlmConfig(resolve_mode(self.url, doc.get("mode"), self.env_mode),
                            int(doc.get("live_deadline_ms") or base.live_deadline_ms),
                            int(doc.get("polish_queue_max") if doc.get("polish_queue_max") is not None
                                else base.polish_queue_max))

    def _load(self) -> NpcLlmConfig:
        now = self.clock()
        if not self.url:
            self._cached, self._read_at, self._ok_at = NpcLlmConfig(), now, now
            return self._cached
        try:
            cfg = self._parse(self.c.find_one({"_id": DOC_ID}))
        except PyMongoError:
            self._read_at = now
            if self._cached is None or now - self._ok_at > self.stale:
                self._cached = NpcLlmConfig()    # 장애가 이어지는 동안 캐시도 off로 둔다(옛 켜짐 값으로 돌아가지 않게)
            return self._cached
        self._cached, self._read_at, self._ok_at = cfg, now, now
        return cfg

    def current(self) -> NpcLlmConfig:
        """10초 캐시 값."""
        with self._lock:
            if self._cached is not None and self.clock() - self._read_at < self.ttl:
                return self._cached
            return self._load()

    def fresh(self) -> NpcLlmConfig:
        """캐시 없이 바로 읽은 값."""
        with self._lock:
            return self._load()

    def set_mode(self, mode: str, live_deadline_ms: Optional[int] = None, polish_queue_max: Optional[int] = None) -> dict:
        """설정 문서를 고치고 저장된 문서를 돌려준다."""
        if mode not in MODES:
            raise ValueError("invalid_mode")
        fields: dict = {"mode": mode, "updated_at": time.time()}
        if live_deadline_ms is not None:
            if not 200 <= live_deadline_ms <= 15000:
                raise ValueError("invalid_live_deadline_ms")
            fields["live_deadline_ms"] = int(live_deadline_ms)
        if polish_queue_max is not None:
            if not 0 <= polish_queue_max <= 1000:
                raise ValueError("invalid_polish_queue_max")
            fields["polish_queue_max"] = int(polish_queue_max)
        self.c.update_one({"_id": DOC_ID}, {"$set": fields}, upsert=True)
        with self._lock:
            self._cached = None
        return self.c.find_one({"_id": DOC_ID}) or {}


class StaticOpsSettings:
    """고정 값(시험·측정용). `config`를 바꾸면 다음 읽기부터 반영된다."""

    def __init__(self, config: NpcLlmConfig = NpcLlmConfig()):
        self.config = config

    def current(self) -> NpcLlmConfig:
        """현재 설정(웹은 캐시 값)."""
        return self.config

    def fresh(self) -> NpcLlmConfig:
        """캐시 없이 읽은 설정."""
        return self.config
