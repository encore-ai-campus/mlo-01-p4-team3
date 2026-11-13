"""광고 중계 순서와 같은 출처 랜딩 정책. HTTP·웹소켓 객체를 받지 않는다."""
import base64
import json
import re

# 광고 서버가 돌려주는 가상 랜딩 경로. 합성 소재 ID는 "syn:" 접두어를 쓰므로 ':'도 허용한다.
LANDING_PATH = re.compile(r"/ads/landing/[A-Za-z0-9_:-]{1,128}")
_SERVE_ID = re.compile(r"[A-Za-z0-9_:.-]{1,200}")


def relay_impression(gateway, token, ratio, visible_ms, occurred_at_ms, request_id):
    """검증된 비콘을 재시도 없이 전달한다."""
    return gateway.report_impression(token, ratio, visible_ms, occurred_at_ms, request_id)


def relay_click(gateway, token, request_id):
    """광고 서버가 돌려준 가상 랜딩 경로만 허용한다.

    반환: (랜딩 경로 또는 None, 결과 코드). None이면 화면은 게임 조회 없는 실패 안내를 보인다
    (홈으로 보내면 장애 동안 클릭마다 진척·판 조회가 늘어 게임 API가 느려진다, B-08-03 E 보완).
    """
    target, result = gateway.click(token, request_id)
    if target and LANDING_PATH.fullmatch(target):
        return target, result
    return None, result if target is None else "error"


def relay_landing(gateway, creative_id, request_id):
    """가상 랜딩 HTML 또는 실패 표시를 반환한다."""
    return gateway.landing(creative_id, request_id)


def serve_id_of(token):
    """로그 대조용으로 serve 토큰 본문의 serve_id만 꺼낸다. 서명은 검증하지 않는다(광고 서버가 한다).

    형식이 다르거나 값이 이상하면 None. 토큰 원문·세션 가명은 돌려주지 않는다.
    """
    if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 1:
        return None
    body = token.split(".", 1)[0]
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError, RecursionError):   # 깊게 중첩된 본문도 잘못된 토큰으로 본다
        return None
    serve_id = claims.get("serve_id") if isinstance(claims, dict) else None
    return serve_id if isinstance(serve_id, str) and _SERVE_ID.fullmatch(serve_id) else None
