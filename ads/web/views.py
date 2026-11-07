"""내부 광고 API 4종. 브라우저 출처가 아닌 게임 웹 중계용이다."""
import json
import math
import time
from dataclasses import asdict

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from ads.app.beacon import handle_impression
from ads.app.click import handle_click
from ads.app.serve import RequestConflict
from ads.domain.model import AdContext, AdRequest, AdSlot, GameOutcome
from ads.domain.rules import charge_id, verify_token
from ads.project.composition import get_service
from ads.web.models import Creative
from ads.web.telemetry import CLICK, DURATION, IMPRESSION, REGISTRY, SERVES


def _object(request, allowed, required):
    data = json.loads(request.body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(data, dict) or set(data) - allowed or required - set(data):
        raise ValueError("JSON fields")
    return data


def _text(value, max_length=128):
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ValueError("string")
    return value


def _integer(value, low=0, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError("integer")
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("boolean")
    return value


def _event_fields(result):
    r, j = result.record, result.judgement
    return dict(serve_id=r.serve_id if r else None, charge_id=result.charge_id,
        charge_mw=result.charge_mw, judge_status=j.status.value,
        invalid_reason=j.reason.value if j.reason else None,
        implied_by_click=j.implied_impression, suspect=j.suspect)


@csrf_exempt
@require_POST
def serve(request):
    """AdRequest JSON을 받아 가격을 포함하지 않는 AdResponse만 반환한다."""
    started = time.perf_counter()
    try:
        d = _object(request, {"request_id", "slot", "session_pseudonym", "context", "source", "load_test"},
                    {"request_id", "slot", "session_pseudonym", "context"})
        c = d["context"]
        if not isinstance(c, dict) or set(c) - {"preset_id", "outcome", "returning", "hour"}:
            raise ValueError("context")
        ctx = AdContext(_text(c["preset_id"]) if c.get("preset_id") is not None else None,
            GameOutcome(c["outcome"]) if c.get("outcome") is not None else None,
            _boolean(c.get("returning", False)), _integer(c.get("hour", 0), high=23))
        source = d.get("source", "human")
        if source not in ("human", "synthetic"):
            raise ValueError("source")
        req = AdRequest(_text(d["request_id"]), AdSlot(d["slot"]), _text(d["session_pseudonym"]),
                        ctx, source, _boolean(d.get("load_test", False)))
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    service = get_service()
    try:
        out = service.serve(req)
    except RequestConflict:
        return JsonResponse({"error": "request_id_conflict"}, status=409)
    if out.filled:
        claims, _ = verify_token(out.serve_token, service.key, service.clock())
        record = service.state.serves[claims.serve_id]
        request.ad_fields = dict(serve_id=record.serve_id, charge_id=charge_id(record.serve_id, record.billing),
                                 filled=True)
    else:
        request.ad_fields = dict(filled=False)
    request.ad_fields["candidates"] = [asdict(c) for c in service.candidates[req.request_id]]
    SERVES.labels(str(out.filled).lower()).inc()
    DURATION.labels("serve", "200").observe(time.perf_counter() - started)
    return JsonResponse(asdict(out))


@csrf_exempt
@require_POST
def impression(request):
    """Content-Type과 무관하게 비콘 JSON을 파싱하고 판정과 무관하게 204를 돌려준다."""
    try:
        d = _object(request, {"token", "visible_ratio", "visible_ms", "occurred_at_ms"},
                    {"token", "visible_ratio", "visible_ms", "occurred_at_ms"})
        token = _text(d["token"], 4096)
        ratio = d["visible_ratio"]
        if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
            raise ValueError("visible_ratio")
        visible_ms = _integer(d["visible_ms"])
        _integer(d["occurred_at_ms"])
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    result = handle_impression(get_service(), token, ratio, visible_ms)
    request.ad_fields = _event_fields(result)
    IMPRESSION.labels(result.judgement.status.value).inc()
    return HttpResponse(status=204)


@csrf_exempt
@require_GET
def click(request):
    """서명·중복·만료를 판정하고 항상 내부 상대 경로로 이동한다."""
    token = request.GET.get("t", "")
    if len(token) > 4096:
        token = ""
    result = handle_click(get_service(), token)
    request.ad_fields = _event_fields(result)
    CLICK.labels(result.judgement.status.value).inc()
    return HttpResponseRedirect(result.redirect_path)


@require_GET
def landing(request, creative_id):
    """외부 링크 없는 가상 랜딩 HTML을 반환한다."""
    cr = Creative.objects.filter(pk=creative_id).first()
    title, body = (cr.title, cr.body) if cr else ("광고 안내", "현재 표시할 가상 광고가 없습니다.")
    return HttpResponse(format_html(
        '<!doctype html><html lang="ko"><meta charset="utf-8"><title>{}</title>'
        '<main><h1>{}</h1><p>{}</p><p>프로젝트용 가상 광고입니다.</p></main></html>', title, title, body))


@require_GET
def metrics(request):
    """광고 처리 횟수와 지연을 Prometheus 형식으로 반환한다."""
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)
