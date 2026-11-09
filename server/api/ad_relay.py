"""로그인한 브라우저의 광고 클릭·랜딩 HTTP 중계."""
import uuid
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET
from server.app.ad_events import relay_click, relay_landing, serve_id_of
from server.paldo_web.composition import get_ad_gateway, GameServiceUnavailable
from .ad_metrics import relay_result

# 게임 조회 없는 실패 안내. 돌아가기는 브라우저 기록(같은 출처 이전 화면), 기록이 없으면 홈 링크.
UNAVAILABLE_HTML = ('<!doctype html><meta charset="utf-8"><title>광고</title>'
                    '<p>광고를 불러오지 못했습니다.</p>'
                    '<a href="/" onclick="if(history.length>1){history.back();return false;}">돌아가기</a>')


def _unavailable():
    response = HttpResponse(UNAVAILABLE_HTML)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def click(request):
    """클릭을 중계한다. 실패하면 홈으로 보내지 않고 같은 응답에서 가벼운 안내를 보인다."""
    request_id = getattr(request, "request_id", str(uuid.uuid4()))
    token = request.GET.get("t", "")
    target, result = None, "error"
    if token and len(token) <= 4096:
        try:
            target, result = relay_click(get_ad_gateway(), token, request_id)
        except GameServiceUnavailable:
            pass
    relay_result("click", result, request_id, serve_id_of(token))
    if target is None:
        return _unavailable()
    response = redirect(target)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def landing(request, creative_id):
    """가상 랜딩 HTML을 그대로 전달하고 실패 때 돌아가기 안내를 표시한다."""
    request_id = getattr(request, "request_id", str(uuid.uuid4()))
    try:
        html, result = relay_landing(get_ad_gateway(), creative_id, request_id)
    except GameServiceUnavailable:
        html, result = None, "error"
    relay_result("landing", result, request_id)
    if html is None:
        return _unavailable()
    response = HttpResponse(html)
    response["Cache-Control"] = "private, no-store"
    return response
