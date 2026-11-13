"""광고 쪽 가짜 구현. B는 FakeAdProvider로 광고 지면을 먼저 붙이고, 11/8에 실제 광고 서버로 바꾼다."""
from __future__ import annotations

import dataclasses
import time
from typing import Optional

from ads.domain.model import AdCreativeView, AdRequest, AdResponse, slot_image_path


class FakeAdProvider:
    """고정 소재를 돌려준다. latency_ms로 지연(무응답 흉내)을, fail=True로 예외를, empty=True로 광고 없음을 주입한다.

    `creative.image_path`는 기본 경로로 보고, 응답에는 요청 지면 규격의 경로(`slot_image_path`)를 넣는다(실제 서버와 같다).
    """

    def __init__(self, latency_ms: int = 0, fail: bool = False, empty: bool = False,
                 creative: Optional[AdCreativeView] = None):
        self.latency_ms = latency_ms
        self.fail = fail
        self.empty = empty
        self.creative = creative or AdCreativeView("cr_fake", "주막 국밥", "뜨끈한 국밥 한 그릇", "/static/promo/fake.png")
        self.calls: list[AdRequest] = []
        self.relay_calls: list[tuple] = []

    def _relay(self, kind, *args):
        self.relay_calls.append((kind, *args))
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.fail:
            raise ConnectionError("fake ad server down")

    def send_impression(self, serve_token, visible_ratio, visible_ms, occurred_at_ms,
                        request_id, connect_timeout_ms, total_timeout_ms):
        """비콘 중계 계약의 인자와 실패 주입을 검증한다."""
        self._relay("impression", serve_token, visible_ratio, visible_ms, occurred_at_ms,
                    request_id, connect_timeout_ms, total_timeout_ms)
        return True

    def send_click(self, serve_token, request_id, connect_timeout_ms, total_timeout_ms):
        """같은 출처 가상 랜딩 경로를 반환한다."""
        self._relay("click", serve_token, request_id, connect_timeout_ms, total_timeout_ms)
        return "/ads/landing/" + self.creative.creative_id

    def fetch_landing(self, creative_id, request_id, connect_timeout_ms, total_timeout_ms):
        """외부 리소스를 요청하지 않는 가상 랜딩을 반환한다."""
        self._relay("landing", creative_id, request_id, connect_timeout_ms, total_timeout_ms)
        return '<html lang="ko"><body><h1>가상 광고</h1><a href="/">돌아가기</a></body></html>'

    def request_ad(self, req: AdRequest, connect_timeout_ms: int, total_timeout_ms: int) -> AdResponse:
        self.calls.append(req)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.fail:
            raise ConnectionError("fake ad server down")
        if self.empty:
            return AdResponse(req.request_id, filled=False, policy_version="fake", ctr_version="fake")
        token = f"fake.{req.request_id}"
        view = dataclasses.replace(self.creative, image_path=slot_image_path(self.creative.image_path, req.slot))
        return AdResponse(req.request_id, True, view, token, "/ads/v1/impression",
                          f"/ads/v1/click?t={token}", "fake", "fake")
