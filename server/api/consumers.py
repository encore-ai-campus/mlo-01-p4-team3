"""웹소켓 메시지를 검증하고 광고 응용 중계를 스레드 경계 밖에서 실행한다."""
from collections import deque
import json
import time
from types import SimpleNamespace
from uuid import UUID, uuid4
from importlib import import_module
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user
from server.app.ad_events import relay_impression
from server.paldo_web.composition import get_ad_gateway, GameServiceUnavailable
from .ad_metrics import CONNECTIONS, MESSAGES, DROPPED, relay_result


_CLIENT_DROP_REASONS = {"queue_full", "page_exit"}


class GameConsumer(AsyncJsonWebsocketConsumer):
    """로그인 세션과 출처로 보호하는 광고 비콘 전용 연결."""

    async def connect(self):
        """익명 연결도 수락 직후 4401로 닫아 재연결을 중지시킨다."""
        self._counted = False
        self._arrivals = deque()
        self._hello = False
        await self.accept()
        user = self.scope.get("user")
        if not user or not user.is_authenticated or not user.is_active:
            await self.close(code=4401)
            return
        self._counted = True
        CONNECTIONS.inc()

    async def disconnect(self, code):
        """수락한 인증 연결만 게이지에서 제거한다."""
        if self._counted:
            CONNECTIONS.dec()
            self._counted = False

    async def receive(self, text_data=None, bytes_data=None, **kwargs):
        """크기를 먼저 제한하고 JSON 형식 오류도 연결을 유지한 채 응답한다."""
        if not self._counted:
            return
        size = len(bytes_data) if bytes_data is not None else len((text_data or "").encode("utf-8"))
        if size > 4096:
            await self.close(code=1009)
            return
        if bytes_data is not None:
            if await self._allow_message(None, "unknown"):
                await self._error(None, "bad_message")
            return
        try:
            data = json.loads(text_data)
        except (ValueError, TypeError):
            if await self._allow_message(None, "unknown"):
                await self._error(None, "bad_message")
            return
        if isinstance(data, dict) and data.get("type") == "hello":
            await self.receive_json(data)
            return
        msg_id = data.get("msg_id") if isinstance(data, dict) else None
        try:
            UUID(msg_id)
        except (ValueError, TypeError, AttributeError):
            msg_id = None
        kind = "ad.impression" if isinstance(data, dict) and data.get("type") == "ad.impression" else "unknown"
        if kind == "ad.impression" and not await self._session_active():
            await self.close(code=4401)
            return
        if not await self._allow_message(msg_id, kind):
            return
        await self.receive_json(data)

    async def receive_json(self, content, **kwargs):
        """두 메시지만 받고 동기 광고 호출을 이벤트 루프 밖으로 보낸다."""
        if not isinstance(content, dict):
            await self._error(None, "bad_message")
            return
        kind = content.get("type")
        message_id = content.get("msg_id")
        try:
            UUID(message_id)
        except (ValueError, TypeError, AttributeError):
            message_id = None
        if kind == "hello":
            dropped = content.get("dropped")
            reasons = content.get("dropped_by_reason")
            if (self._hello or set(content) != {"type", "dropped", "dropped_by_reason"}
                    or type(dropped) is not int or dropped < 0
                    or not isinstance(reasons, dict) or set(reasons) != _CLIENT_DROP_REASONS
                    or any(type(count) is not int or count < 0 for count in reasons.values())
                    or dropped != sum(reasons.values())):
                await self._error(None, "bad_message", "hello")
                return
            try:
                counts = {reason: float(count) for reason, count in reasons.items()}
            except OverflowError:
                await self._error(None, "bad_message", "hello")
                return
            self._hello = True
            for reason, count in counts.items():
                DROPPED.labels(reason).inc(count)
            MESSAGES.labels("hello", "ok").inc()
            return
        if kind != "ad.impression":
            await self._error(message_id, "unknown_type")
            return
        if not _valid_impression(content, message_id):
            await self._error(message_id, "bad_message", "ad.impression")
            return
        request_id = str(uuid4())
        relayed, result = await sync_to_async(_relay, thread_sensitive=False)(content, request_id)
        relay_result("impression", result, request_id)
        MESSAGES.labels("ad.impression", result).inc()
        await self.send_json({"type": "ack", "msg_id": message_id, "relayed": relayed})

    @database_sync_to_async
    def _session_active(self):
        # AuthMiddlewareStack's cached session must not survive logout/password reset.
        store = import_module(settings.SESSION_ENGINE).SessionStore(self.scope["session"].session_key)
        user = get_user(SimpleNamespace(session=store))
        return user.is_authenticated and user.is_active and user.pk == self.scope["user"].pk

    async def _error(self, message_id, code, kind="unknown"):
        MESSAGES.labels(kind, code).inc()
        await self.send_json({"type": "error", "msg_id": message_id, "code": code})

    async def _allow_message(self, message_id, kind):
        now = time.monotonic()
        while self._arrivals and self._arrivals[0] <= now - 1:
            self._arrivals.popleft()
        if len(self._arrivals) >= 5:
            if kind == "ad.impression":
                DROPPED.labels("rate_limited").inc()
            await self._error(message_id, "rate_limited", kind)
            return False
        self._arrivals.append(now)
        return True


def _valid_impression(data, message_id):
    if set(data) != {"type", "msg_id", "token", "visible_ratio", "visible_ms", "occurred_at_ms"} or message_id is None:
        return False
    ratio = data["visible_ratio"]
    return (isinstance(data["token"], str) and bool(data["token"])
            and type(ratio) in (int, float) and 0 <= ratio <= 1
            and type(data["visible_ms"]) is int and data["visible_ms"] >= 0
            and type(data["occurred_at_ms"]) is int and data["occurred_at_ms"] >= 0)


def _relay(content, request_id):
    try:
        gateway = get_ad_gateway()
    except GameServiceUnavailable:
        return False, "error"
    return relay_impression(gateway, content["token"], content["visible_ratio"],
                            content["visible_ms"], content["occurred_at_ms"], request_id)
