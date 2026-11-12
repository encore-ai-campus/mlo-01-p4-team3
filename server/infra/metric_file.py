"""Prometheus 텍스트 지표 파일 쓰기(E-12-01).

계층: 인프라(파일). 임시 파일에 쓴 뒤 `os.replace`로 바꿔 수집기가 반쯤 쓴 파일을 읽지 않게 한다.
쓰기 실패는 작업 처리를 막지 않는다(False 반환, 호출자가 로그). 내용은 `server/app/llm_metrics.py`가 만든다.
"""
import os
import time
from typing import Callable, Optional


class MetricFile:
    """한 파일에 지표 텍스트를 원자적으로 쓴다. path가 None이면 아무것도 하지 않는다."""

    def __init__(self, path: Optional[str], min_interval_s: float = 60.0, clock: Callable[[], float] = time.time):
        self.path, self.min_interval_s, self.clock = path, min_interval_s, clock
        self.last_write: Optional[float] = None

    @property
    def enabled(self) -> bool:
        """경로가 있으면 True."""
        return bool(self.path)

    def due(self) -> bool:
        """대기 중 갱신(살아 있음) 시점이 됐는지."""
        return self.enabled and (self.last_write is None or self.clock() - self.last_write >= self.min_interval_s)

    def write(self, render: Callable[[float], str]) -> bool:
        """render(now) 결과를 쓴다. 성공하면 True, 꺼져 있거나 실패하면 False."""
        if not self.enabled:
            return False
        now = self.clock()
        tmp = f"{self.path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(render(now))
            os.replace(tmp, self.path)
        except OSError:
            self.last_write = now   # 실패해도 매 루프 재시도로 로그가 넘치지 않게 한다
            return False
        self.last_write = now
        return True
