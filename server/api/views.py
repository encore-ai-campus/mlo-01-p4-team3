"""게임 JSON API의 인증·입력 검증·응답 경계."""
import json
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods

from engine.domain.model import RejectCode
from engine.domain.serde import SchemaError, to_jsonable
from server.app.dto import StartGameInput, SubmitCommandInput
from server.app.interactions import InteractionConflict, InteractionUnavailable
from server.app.usecases import ConsentRequired, ResourceNotFound, ReviewNotReady, StartUnavailable
from server.paldo_web.composition import GameServiceUnavailable, get_game_service
from .input_validation import parse_game_input


def _player_context(user):
    # 계정 태스크가 연결하는 프로필만 신뢰한다. Django ID로 가명을 대체하지 않는다.
    profile = getattr(user, "paldo_profile", None)
    if profile is None:
        return None
    return str(profile.player_pseudonym), profile.consented_at is not None


def _private_response(response):
    response["Cache-Control"] = "private, no-store"
    patch_vary_headers(response, ["Accept", "HX-Request", "Cookie"])
    return response


def game_request(view):
    """로그인·프로필을 확인하고 공개 오류 응답의 형식을 맞춘다."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(headers={"HX-Redirect": settings.LOGIN_URL})
            elif "application/json" in request.headers.get("Accept", ""):
                response = JsonResponse({"error": "authentication_required"}, status=401)
            else:
                response = redirect(settings.LOGIN_URL)
            return _private_response(response)
        context = _player_context(request.user)
        if context is None:
            return _private_response(JsonResponse({"error": "profile_required"}, status=403))
        request.player_pseudonym, request.consented = context
        try:
            response = view(request, *args, **kwargs)
        except ResourceNotFound:
            response = JsonResponse({"error": "not_found"}, status=404)
        except ConsentRequired:
            response = JsonResponse({"error": "consent_required"}, status=403)
        except ReviewNotReady:
            response = JsonResponse({"error": "review_requires_finished_game"}, status=403)
        except (SchemaError, ValueError):
            response = JsonResponse({"error": "invalid_input"}, status=400)
        except StartUnavailable:
            response = JsonResponse({"error": "start_policy_unavailable"}, status=503)
        except InteractionUnavailable:
            response = JsonResponse({"error": "interaction_service_unavailable"}, status=503)
        except InteractionConflict:
            response = JsonResponse({"error": "interaction_conflict"}, status=409)
        except GameServiceUnavailable:
            response = JsonResponse({"error": "game_service_unavailable"}, status=503)
        return _private_response(response)
    return wrapped


def _body(request):
    if request.content_type != "application/json":
        raise ValueError("json_required")
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError("object_required")
    return data


def command_response(output):
    """수락·재생 200, 상태 버전 충돌 409, 엔진 거절 422를 반환한다."""
    if output.accepted or output.replayed:
        status = 200
    elif output.reject == RejectCode.STATE_VERSION_CONFLICT:
        status = 409
    else:
        status = 422
    return JsonResponse(to_jsonable(output), status=status)


@game_request
@require_http_methods(["POST"])
def start_game(request):
    """세션 계정의 가명 ID를 주입해 판 시작을 요청한다."""
    body = _body(request)
    if "player_pseudonym" in body:
        raise ValueError("server_field")
    body["player_pseudonym"] = request.player_pseudonym
    data = parse_game_input(StartGameInput, body)
    output = get_game_service().start(data, request.player_pseudonym, request.consented)
    request.game_id = output.view.game_id
    return JsonResponse(to_jsonable(output))


@game_request
@require_GET
def game_detail(request, game_id):
    """자기 판의 공개 상태를 JSON으로 반환한다."""
    output = get_game_service().detail(game_id, request.player_pseudonym)
    request.game_id = output.view.game_id
    return JsonResponse(to_jsonable(output))


@game_request
@require_http_methods(["POST"])
def submit_command(request, game_id):
    """소유권 확인 뒤 URL·본문의 판 ID와 명령 DTO를 검증한다."""
    service = get_game_service()
    state, _ = service.owned(game_id, request.player_pseudonym)
    request.game_id = state.meta.game_id
    data = parse_game_input(SubmitCommandInput, _body(request))
    if data.game_id != game_id or data.expected_state_version < 0:
        raise ValueError("invalid_command")
    UUID(data.command_id)
    if data.intent_attempt_id is None:
        return command_response(service.command(data, request.player_pseudonym))
    if service.interactions is None:
        raise InteractionUnavailable
    return command_response(service.interactions.submit_linked(data, request.player_pseudonym))


@game_request
@require_GET
def progress(request):
    """로그인 계정의 진척을 JSON으로 반환한다."""
    return JsonResponse(to_jsonable(get_game_service().player_progress(request.player_pseudonym)))


@game_request
@require_GET
def review(request, game_id):
    """종료된 자기 판에서 공개 가능한 복기 DTO만 반환한다."""
    output = get_game_service().review(game_id, request.player_pseudonym)
    request.game_id = output.game_id
    return JsonResponse(to_jsonable(output))
