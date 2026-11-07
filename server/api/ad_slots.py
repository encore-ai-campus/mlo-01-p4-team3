"""광고 지면을 별도 GET으로 읽어 게임 서버 출처의 HTML만 반환한다."""
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from prometheus_client import Counter

from ads.domain.model import AdContext, AdRequest, AdSlot, SLOT_SIZES
from server.api.sessions import ensure_ad_identity
from server.app.usecases import StartUnavailable
from server.paldo_web import composition
from server.paldo_web.middleware import LOGGER
from .views import game_request

FETCH_TOTAL = Counter("paldo_ad_fetch_total", "광고 지면 요청 결과", ["result"])


def _local_image(path):
    parts = urlsplit(path)
    return (path.startswith("/static/") and not parts.scheme and not parts.netloc
            and "\\" not in path and not any(ord(char) < 32 for char in path))


@game_request
@require_GET
def slot(request, slot):
    """세션 광고 가명만 전달한다. 미연결·광고 오류는 빈 지면으로 끝낸다."""
    try:
        ad_slot = AdSlot(slot)
    except ValueError:
        return HttpResponse(status=404)
    width, height = SLOT_SIZES[ad_slot]
    context = {"slot": slot, "width": width, "height": height}
    try:
        session_id = ensure_ad_identity(request)
    except StartUnavailable:
        FETCH_TOTAL.labels("error").inc()
        LOGGER.warning("ad_identity_unavailable", extra={"component": "ads"})
        return render(request, "ad_slot.html", context)
    try:
        gateway = composition.get_ad_gateway()
    except composition.GameServiceUnavailable:
        gateway = None
    if gateway is None:
        FETCH_TOTAL.labels("error").inc()
        LOGGER.warning("ad_gateway_unavailable", extra={"component": "ads"})
        return render(request, "ad_slot.html", context)
    request_id = getattr(request, "request_id", str(uuid4()))
    response, result = gateway.fetch(AdRequest(
        request_id, ad_slot, session_id, AdContext(hour=timezone.localtime().hour)))
    if result == "ok" and not _local_image(response.creative.image_path):
        result = "error"
    FETCH_TOTAL.labels(result).inc()
    LOGGER.log(30 if result in {"error", "timeout"} else 20, "ad_fetch",
               extra={"component": "ads", "fields": {"result": result, "request_id": request_id}})
    if result == "ok":
        context.update(creative=response.creative, token=response.serve_token,
                       click_path="/ads/click?" + urlencode({"t": response.serve_token}))
    return render(request, "ad_slot.html", context)
