"""운영자 고정 시드 회차 등록 화면과 광고용 서버 가명 연결."""
import json
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_http_methods
from server.app.usecases import ConsentRequired, StartUnavailable
from server.paldo_web.composition import get_session_service
from .consent import player_context


@require_http_methods(["GET", "POST"])
def sessions(request):
    """운영자만 회차를 만들고 목록을 볼 수 있다. 기존 회차는 변경하지 않는다."""
    if not request.user.is_authenticated:
        return redirect("admin-login")
    if not request.user.is_staff or not request.user.is_active:
        return HttpResponse("운영자만 사용할 수 있습니다.", status=403)
    error, status = "", 200
    try:
        service = get_session_service()
        if request.method == "POST":
            try:
                _register(request.POST, service)
            except ValueError:
                error, status = "입력 형식·참가자 가명 ID·중복 회차 ID를 확인해 주세요.", 400
            else:
                return redirect("test-sessions")
        registered = service.store.list_sessions()
    except StartUnavailable:
        return HttpResponse("테스트 세션 저장소를 사용할 수 없습니다.", status=503)
    response = render(request, "test_sessions.html", {"sessions": registered, "error": error}, status=status)
    response["Cache-Control"] = "private, no-store"
    return response


def _register(form, service):
    version_fields = ("rule_version", "engine_version", "content_version")
    if set(form) - {"csrfmiddlewaretoken", "session_id", "plans", "participants", *version_fields}:
        raise ValueError("unknown_field")
    plans = json.loads(form.get("plans", ""))
    if not isinstance(plans, list) or any(not isinstance(plan, dict) for plan in plans):
        raise ValueError("invalid_plans")
    service.create(form.get("session_id", ""), plans, form.get("participants", "").split(),
                   {key: form.get(key, "") for key in version_fields})


def ensure_ad_identity(request):
    """후속 광고 요청에서 쓸 UUID를 저장한다. 로그인 세션의 원문은 저장하지 않는다."""
    context = player_context(request.user)
    if not request.user.is_authenticated or context is None or not context[1] or not request.session.session_key:
        raise ConsentRequired
    key = salted_hmac("paldo.ad-session", request.session.session_key, algorithm="sha256").hexdigest()
    return get_session_service().ad_identity(context[0], key)
