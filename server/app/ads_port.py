"""게임 쪽 광고 포트. 게임 서버는 이 포트로만 광고 서버를 부른다. 상세: docs/contracts/ports.md 5절

계층: 응용. 실제 구현(내부 HTTP 클라이언트)은 server/infra가, 가짜는 ads/testing/fakes.py가 맡는다.
게임 화면은 광고 없이 먼저 그리고, 광고 칸은 htmx가 별도 요청(GET /ads/slot/{slot})으로 채운다.
그 요청 처리에서 fetch_ad를 부른다. 게임 상태 저장·응답은 광고를 기다리지 않는다.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

from ads.domain.model import AdRequest, AdResponse

CONNECT_TIMEOUT_MS = 100     # 실제 HTTP 구현이 연결 단계에 적용
TOTAL_TIMEOUT_MS = 300
MAX_CONCURRENT = 8
BREAKER_FAILURES = 5
BREAKER_OPEN_MS = 30_000


@runtime_checkable
class AdProvider(Protocol):
    """serve와 비콘·클릭·랜딩을 격리된 광고 서버에 전달하는 포트."""
    def request_ad(self, req: AdRequest, connect_timeout_ms: int, total_timeout_ms: int) -> AdResponse: ...

    def send_impression(self, serve_token: str, visible_ratio: float, visible_ms: int,
                        occurred_at_ms: int, request_id: str, connect_timeout_ms: int,
                        total_timeout_ms: int) -> bool:
        """노출 비콘을 한 번 전달한다."""
        ...

    def send_click(self, serve_token: str, request_id: str, connect_timeout_ms: int,
                   total_timeout_ms: int) -> str | None:
        """리다이렉트를 따라가지 않고 랜딩 경로를 반환한다."""
        ...

    def fetch_landing(self, creative_id: str, request_id: str, connect_timeout_ms: int,
                      total_timeout_ms: int) -> str | None:
        """가상 랜딩 HTML을 가져온다."""
        ...


@dataclass
class CircuitBreaker:
    """닫힘 → (연속 실패 BREAKER_FAILURES회) → 열림(BREAKER_OPEN_MS 동안 호출 생략) → 반열림(시험 호출 1개만 허용).
    시험 호출이 성공하면 닫힘, 실패하면 곧바로 다시 열림."""
    clock_ms: Callable[[], float] = lambda: time.monotonic() * 1000
    failures: int = 0
    open_until_ms: float = 0.0
    is_open: bool = False
    probing: bool = False
    generation: int = 0                # 열릴 때마다 증가. 이전 세대에서 시작한 호출의 결과는 무시한다

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def allow(self) -> Optional[int]:
        """호출해도 되면 세대 번호, 아니면 None."""
        with self._lock:
            if not self.is_open:
                return self.generation
            if self.probing or self.clock_ms() < self.open_until_ms:
                return None
            self.probing = True            # 반열림: 이 호출 하나만 통과
            return self.generation

    def cancel_probe(self) -> None:
        with self._lock:
            self.probing = False

    def record(self, ok: bool, generation: int) -> None:
        with self._lock:
            if generation != self.generation:
                return                     # 열리기 전에 시작한 늦은 결과: 열린 상태를 바꾸지 않는다
            if ok:
                self.failures, self.is_open, self.probing = 0, False, False
                return
            self.failures += 1
            if self.probing or self.failures >= BREAKER_FAILURES:
                self.is_open, self.probing, self.failures = True, False, 0
                self.generation += 1
                self.open_until_ms = self.clock_ms() + BREAKER_OPEN_MS


class AdGateway:
    """게임 경로 보호 래퍼. 어떤 실패든 빈 광고를 돌려주고 예외를 올리지 않는다. 재시도하지 않는다."""

    def __init__(self, provider: AdProvider, breaker: Optional[CircuitBreaker] = None,
                 total_timeout_ms: int = TOTAL_TIMEOUT_MS, max_concurrent: int = MAX_CONCURRENT):
        self.provider = provider
        self.breaker = breaker or CircuitBreaker()
        self.total_timeout_ms = total_timeout_ms
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="adgw")
        self._relay_slots = threading.BoundedSemaphore(max_concurrent)
        self._relay_pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="adrelay")

    def report_impression(self, token, ratio, visible_ms, occurred_at_ms, request_id):
        """노출을 한 번 중계하고 성공 여부와 계측 결과를 반환한다."""
        return self._relay("send_impression", (token, ratio, visible_ms, occurred_at_ms, request_id), bool, False)

    def click(self, token, request_id):
        """클릭을 한 번 중계한다. 외부 주소는 응용 계층에서 거부한다."""
        return self._relay("send_click", (token, request_id), str, None)

    def landing(self, creative_id, request_id):
        """가상 랜딩을 한 번 가져온다."""
        return self._relay("fetch_landing", (creative_id, request_id), str, None)

    def _relay(self, method, args, expected, fallback):
        gen = self.breaker.allow()
        if gen is None:
            return fallback, "circuit_open"
        if not self._relay_slots.acquire(blocking=False):
            self.breaker.cancel_probe()
            return fallback, "busy"
        try:
            future = self._relay_pool.submit(getattr(self.provider, method), *args,
                                            CONNECT_TIMEOUT_MS, self.total_timeout_ms)
        except Exception:
            self._relay_slots.release()
            self.breaker.record(False, gen)
            return fallback, "error"
        future.add_done_callback(lambda _: self._relay_slots.release())
        try:
            value = future.result(timeout=self.total_timeout_ms / 1000)
        except FutureTimeout:
            self.breaker.record(False, gen)
            return fallback, "timeout"
        except Exception:
            self.breaker.record(False, gen)
            return fallback, "error"
        ok = isinstance(value, expected) and bool(value)
        self.breaker.record(ok, gen)
        return (value, "ok") if ok else (fallback, "error")

    def fetch(self, req: AdRequest) -> tuple[AdResponse, str]:
        """반환: (응답, 결과 코드 ok|empty|timeout|error|busy|circuit_open). 결과 코드는 지표용이다."""
        empty = AdResponse(req.request_id, filled=False)
        gen = self.breaker.allow()
        if gen is None:
            return empty, "circuit_open"
        if not self._slots.acquire(blocking=False):
            self.breaker.cancel_probe()          # 시험 호출 기회를 썼지만 호출하지 못함 → 다음 요청이 시험하게 둔다
            return empty, "busy"
        try:
            fut = self._pool.submit(self.provider.request_ad, req, CONNECT_TIMEOUT_MS, self.total_timeout_ms)
        except Exception:              # noqa: BLE001
            self._slots.release()
            self.breaker.record(False, gen)
            return empty, "error"
        # 슬롯은 호출이 실제로 끝날 때 돌려준다(시간 초과로 버린 호출도 끝날 때까지 상한에 포함)
        fut.add_done_callback(lambda _f: self._slots.release())
        try:
            resp = fut.result(timeout=self.total_timeout_ms / 1000)
        except FutureTimeout:
            self.breaker.record(False, gen)
            return empty, "timeout"
        except Exception:              # noqa: BLE001 - 광고 실패가 게임을 막으면 안 된다
            self.breaker.record(False, gen)
            return empty, "error"
        if not isinstance(resp, AdResponse) or resp.request_id != req.request_id \
                or (resp.filled and (resp.creative is None or not resp.serve_token)):
            self.breaker.record(False, gen)
            return empty, "error"
        self.breaker.record(True, gen)
        return resp, ("ok" if resp.filled else "empty")
