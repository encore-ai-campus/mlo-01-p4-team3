"""광고 serve 한 건의 단계별 소요 시간 수집(11/11 지연 원인 분리용).

요청마다 contextvar에 수집기를 하나 두고, 인프라 코드가 단계 시간을 더한다. 값(ms·횟수·참거짓)만 담고
세션 가명·요청 본문은 담지 않는다. 수집기가 없으면(작업자·시험) 아무것도 하지 않는다.
"""
import contextvars
import threading
import time
from contextlib import contextmanager

from django.db import connection

_current = contextvars.ContextVar("ads_serve_timing", default=None)
# 스레드별 첫 DB 연결 여부: gthread 스레드는 각자 연결을 가지므로, 스레드가 처음 연결하는지 따로 본다.
_thread_seen = threading.local()


class ServeTiming:
    """단계 이름별 누적 ms와 횟수, DB 쿼리 수·시간."""

    def __init__(self):
        self.ms, self.counts = {}, {}
        self.queries, self.query_ms = 0, 0.0
        self.flags = {}

    def add(self, name, seconds):
        self.ms[name] = self.ms.get(name, 0.0) + seconds * 1000
        self.counts[name] = self.counts.get(name, 0) + 1

    def fields(self):
        """로그에 남길 값. ms는 소수 셋째 자리까지."""
        out = {f"{k}_ms": round(v, 3) for k, v in sorted(self.ms.items())}
        out.update({f"{k}_n": n for k, n in sorted(self.counts.items()) if n > 1})
        out.update(db_queries=self.queries, db_ms=round(self.query_ms, 3), thread=threading.current_thread().name)
        out.update(self.flags)
        return out


def _query_wrapper(execute, sql, params, many, context):
    timing = _current.get()
    started = time.perf_counter()
    try:
        return execute(sql, params, many, context)
    finally:
        if timing is not None:
            timing.queries += 1
            timing.query_ms += (time.perf_counter() - started) * 1000


@contextmanager
def collect():
    """serve 한 건을 감싼다. 연결 상태를 먼저 확인해 새 연결 비용을 따로 잰다."""
    timing = ServeTiming()
    token = _current.set(timing)
    try:
        timing.flags["thread_first_connection"] = not getattr(_thread_seen, "connected", False)
        started = time.perf_counter()
        had = connection.connection is not None
        # Django는 상태 확인(CONN_HEALTH_CHECKS)을 첫 커서에서야 한다. 미리 해서 끊긴 연결을 다시 맺는 비용도
        # connect에 넣는다(Codex 지적).
        connection.close_if_health_check_failed()
        timing.flags["stale_connection_replaced"] = had and connection.connection is None
        timing.flags["new_connection"] = connection.connection is None
        connection.ensure_connection()
        timing.add("connect", time.perf_counter() - started)
        _thread_seen.connected = True
        with connection.execute_wrapper(_query_wrapper):
            yield timing
    finally:
        _current.reset(token)


def record(name, seconds):
    """수집 중이면 이미 잰 구간 시간을 name에 더한다."""
    timing = _current.get()
    if timing is not None:
        timing.add(name, seconds)


@contextmanager
def phase(name):
    """수집 중이면 구간 시간을 name에 더한다."""
    timing = _current.get()
    if timing is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        timing.add(name, time.perf_counter() - started)
