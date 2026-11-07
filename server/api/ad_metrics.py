"""웹소켓·광고 중계 지표와 개인정보 없는 구조화 로그."""
from prometheus_client import Counter, Gauge
from server.paldo_web.middleware import LOGGER

CONNECTIONS = Gauge("paldo_ws_connections", "Active authenticated websocket connections")
MESSAGES = Counter("paldo_ws_messages_total", "Websocket messages", ["type", "result"])
RELAY = Counter("paldo_ad_relay_total", "Ad relay results", ["kind", "result"])
DROPPED = Counter("paldo_ad_beacon_dropped_total", "Dropped beacons lower bound", ["reason"])
for _reason in ("queue_full", "page_exit", "rate_limited"):
    DROPPED.labels(_reason)


def relay_result(kind, result, request_id):
    """본문·토큰·로그인 ID 없이 중계 결과만 기록한다."""
    RELAY.labels(kind, result).inc()
    LOGGER.info("ad_relay", extra={"component": "ad_relay", "fields": {
        "request_id": request_id, "kind": kind, "result": result}})
