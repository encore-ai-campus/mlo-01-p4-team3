"""로그인한 브라우저의 광고 클릭·랜딩 HTTP 중계."""
import uuid
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET
from server.app.ad_events import relay_click, relay_landing
from server.paldo_web.composition import get_ad_gateway, GameServiceUnavailable
from .ad_metrics import relay_result


@login_required
@require_GET
def click(request):
    """클릭 실패에도 같은 출처의 안전한 페이지로 이동한다."""
    request_id = getattr(request, "request_id", str(uuid.uuid4()))
    token = request.GET.get("t", "")
    target, result = "/", "error"
    if token and len(token) <= 4096:
        try:
            target, result = relay_click(get_ad_gateway(), token, request_id)
        except GameServiceUnavailable:
            pass
    relay_result("click", result, request_id)
    response = redirect(target)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_GET
def landing(request, creative_id):
    """가상 랜딩 HTML을 그대로 전달하고 실패 때 돌아가기 링크를 표시한다."""
    request_id = getattr(request, "request_id", str(uuid.uuid4()))
    try:
        html, result = relay_landing(get_ad_gateway(), creative_id, request_id)
    except GameServiceUnavailable:
        html, result = None, "error"
    relay_result("landing", result, request_id)
    response = HttpResponse(html if html is not None else '<p>광고를 불러오지 못했습니다.</p><a href="/">돌아가기</a>')
    response["Cache-Control"] = "private, no-store"
    return response
