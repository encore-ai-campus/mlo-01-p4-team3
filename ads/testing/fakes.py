"""광고 쪽 가짜 구현. B는 FakeAdProvider로 광고 지면을 먼저 붙이고, 11/8에 실제 광고 서버로 바꾼다."""
from __future__ import annotations

import time
from typing import Optional

from ads.domain.model import AdCreativeView, AdRequest, AdResponse


class FakeAdProvider:
    """고정 소재를 돌려준다. latency_ms로 지연(무응답 흉내)을, fail=True로 예외를, empty=True로 광고 없음을 주입한다."""

    def __init__(self, latency_ms: int = 0, fail: bool = False, empty: bool = False,
                 creative: Optional[AdCreativeView] = None):
        self.latency_ms = latency_ms
        self.fail = fail
        self.empty = empty
        self.creative = creative or AdCreativeView("cr_fake", "주막 국밥", "뜨끈한 국밥 한 그릇", "/static/ads/fake.png")
        self.calls: list[AdRequest] = []

    def request_ad(self, req: AdRequest, connect_timeout_ms: int, total_timeout_ms: int) -> AdResponse:
        self.calls.append(req)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.fail:
            raise ConnectionError("fake ad server down")
        if self.empty:
            return AdResponse(req.request_id, filled=False, policy_version="fake", ctr_version="fake")
        token = f"fake.{req.request_id}"
        return AdResponse(req.request_id, True, self.creative, token, "/ads/v1/impression",
                          f"/ads/v1/click?t={token}", "fake", "fake")
