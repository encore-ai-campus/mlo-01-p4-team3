"""광고 중계 순서와 같은 출처 랜딩 정책. HTTP·웹소켓 객체를 받지 않는다."""
import re


def relay_impression(gateway, token, ratio, visible_ms, occurred_at_ms, request_id):
    """검증된 비콘을 재시도 없이 전달한다."""
    return gateway.report_impression(token, ratio, visible_ms, occurred_at_ms, request_id)


def relay_click(gateway, token, request_id):
    """광고 서버가 돌려준 가상 랜딩 경로만 허용한다."""
    target, result = gateway.click(token, request_id)
    if target and re.fullmatch(r"/ads/landing/[A-Za-z0-9_-]+", target):
        return target, result
    return "/", result if target is None else "error"


def relay_landing(gateway, creative_id, request_id):
    """가상 랜딩 HTML 또는 실패 표시를 반환한다."""
    return gateway.landing(creative_id, request_id)
