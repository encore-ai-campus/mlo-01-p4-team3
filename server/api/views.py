"""게임 API와 회의 화면의 인증·입력 검증·응답 경계."""
import json
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.shortcuts import redirect, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods

from engine.domain import tutorial
from engine.domain.model import CommandKind, EvidenceType, RejectCode
from engine.domain.serde import SchemaError, to_jsonable
from server.app.dto import StartGameInput, StartTutorialInput, SubmitCommandInput
from server.app.interactions import InteractionConflict, InteractionUnavailable
from server.app.usecases import ConsentRequired, OpenGameInProgress, ResourceNotFound, ReviewNotReady, StartUnavailable
from server.paldo_web.composition import GameServiceUnavailable, get_game_service
from .consent import player_context
from .input_validation import parse_game_input
from .meeting_view import meeting_context


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
        context = player_context(request.user)
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
        except OpenGameInProgress as exc:
            # 진행 중인 일반 판이 있으면 새 판을 만들지 않는다. 이어 할 판 ID만 알려 준다(11/10 사용자 결정).
            response = JsonResponse({"error": "open_game_in_progress", "game_id": exc.game_id}, status=409)
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
    return JsonResponse(to_jsonable(output), status=_command_status(output))


def _command_status(output):
    if output.accepted or output.replayed:
        return 200
    if output.reject == RejectCode.STATE_VERSION_CONFLICT:
        return 409
    return 422


def _wants_meeting(request):
    return request.headers.get("HX-Request") == "true" or request.get_preferred_type(
        ["application/json", "text/html"]
    ) == "text/html"


def _meeting_response(request, view, variant, status=200, previous_interrogation=None, reject=None, scenario_id=None):
    from .participant_forms import participant_context
    from .tutorial_view import tutorial_context

    template = "meeting.html" if request.headers.get("HX-Request") == "true" else "game.html"
    service = get_game_service()
    first_game, guide_k, clue_notices = service.guide_info(view.game_id, request.player_pseudonym)
    context = meeting_context(view, guide_k, first_game, clue_notices, service.temperaments(view.game_id, request.player_pseudonym))
    if view.result is not None:
        from .story_view import result_context
        state, _ = service.owned(view.game_id, request.player_pseudonym)
        context.update(result_context(view, state.meta.ending_choice, state.meta.content_version))
    rendered = service.interactions.render_snapshot(view.game_id, request.player_pseudonym) if service.interactions else {}
    jobs = {speech.speech_id: speech.render_job_id for speech in view.public.speeches}
    # 튜토리얼 판(scenario_id)이면 단계 정보·족제비 대사·자유 입력 미리 채움을 더한다(형식: tutorial_step_format.md).
    context.update(tutorial_context(view, scenario_id, variant, settings.FREE_TEXT_INPUT))
    # NPC 대사 LLM(E-11-13). 꺼져 있으면 아무 속성도 더하지 않아 지금 화면 그대로다. 튜토리얼 판은 늘 끈다.
    npc = _npc_flags(service, view, request.player_pseudonym) if not context["tutorial_mode"]         else {"npc_polish": False, "npc_live": False}
    # 저장된 작업의 발언 ID까지 일치할 때만 문장을 연결한다.
    for speech in context["vm"].speeches:
        result = rendered.get(jobs.get(speech.speech_id))
        if result is not None and result.speech_id == speech.speech_id:
            speech.text = "…" if npc["npc_live"] and not result.final else result.text
            speech.pending_job_id = None if result.final else result.job_id
    context.update(npc)
    return render(request, template, {**context, **participant_context(service, view), "rejected": status >= 400,
                                     "reject_code": reject.value if reject is not None else "",
                                     "previous_interrogation": previous_interrogation or {},
                                     "mudang_portrait_id": f"portrait:mudang_{variant or 'f'}"}, status=status)


def _npc_flags(service, view, player):
    """다듬기 켬(선조회 경로를 화면에 붙임)·실시간 켬(캐묻기 답 "…"과 0.5초 폴링). 판이 끝났으면 둘 다 끔."""
    lines = getattr(service, "npc_lines", None)
    if lines is None or view.result is not None:
        return {"npc_polish": False, "npc_live": False}
    from .vn_script import attach_particles
    polish = lines.prepare(view.game_id, player, display=attach_particles)
    return {"npc_polish": polish, "npc_live": lines.live_on(),
            "npc_finals": lines.fixed_polished(view.game_id, view.day) if polish else {}}


def _previous_interrogation(command, before, view):
    """같은 날·단계에 다시 그린 심문 양식이 직전 대상·질문 종류를 기본으로 고르게 한다."""
    if command.kind is not CommandKind.INTERROGATE or (before.day, before.phase) != (view.day, view.phase):
        return None
    return {"addressee": command.addressee or "",
            "interrogation_kind": command.interrogation_kind.value if command.interrogation_kind else ""}


def _meeting_command_body(form):
    allowed = {"csrfmiddlewaretoken", "game_id", "command_id", "state_version", "kind", "target",
               "addressee", "night", "slot", "interrogation_kind", "evidence", "divination"}
    if set(form) - allowed:
        raise ValueError("unknown_field")
    command = {"kind": form.get("kind"), "actor": "mudang", "evidence": []}
    for field in ("target", "addressee", "slot", "interrogation_kind"):
        if form.get(field):
            command[field] = form[field]
    if form.get("night"):
        command["night"] = int(form["night"])
    for value in form.getlist("evidence"):
        evidence_type, separator, record_id = value.partition(":")
        if not separator or evidence_type not in {EvidenceType.CLAIM.value, EvidenceType.DIVINATION.value} or not record_id:
            raise ValueError("invalid_evidence")
        command["evidence"].append({"type": evidence_type, "record_id": record_id})
    if form.get("divination"):
        night, target, result = form["divination"].split(":")
        command["divination"] = {"night": int(night), "target": target, "result": result}
    return {"game_id": form.get("game_id"), "command_id": form.get("command_id"),
            "expected_state_version": int(form.get("state_version", "-1")), "command": command}


@game_request
@require_http_methods(["POST"])
def start_game(request):
    """세션 계정의 가명 ID를 주입해 JSON 또는 계정 메뉴의 판 시작을 처리한다."""
    from_form = request.content_type == "application/x-www-form-urlencoded"
    if from_form:
        form = request.POST
        if set(form) - {"csrfmiddlewaretoken", "preset_id", "session_id", "mudang_variant", "seed", "start_token", "ending_choice",
                        "replace_open"}:
            raise ValueError("unknown_field")
        body = {key: form.get(key) for key in ("preset_id", "session_id", "mudang_variant", "start_token")}
        if form.get("replace_open") == "1":
            body["replace_open"] = True
        if "ending_choice" in form:
            body["ending_choice"] = form["ending_choice"]
        if "seed" in form:
            body["seed"] = int(form["seed"])
    else:
        body = _body(request)
    if "player_pseudonym" in body:
        raise ValueError("server_field")
    body["player_pseudonym"] = request.player_pseudonym
    if body.get("session_id") in (None, ""):
        body["session_id"] = None   # 생략·null·빈 문자열 = 회차 없는 일반 판(E-08-14)
    data = parse_game_input(StartGameInput, body)
    try:
        output = get_game_service().start(data, request.player_pseudonym, request.consented)
    except OpenGameInProgress:
        if from_form and request.headers.get("HX-Request") != "true":
            # 홈으로 돌려 이어 하기 / 중단하고 새로 시작을 고르게 한다.
            return redirect(f"{reverse('home')}?open=choose")
        raise
    request.game_id = output.view.game_id
    if from_form and request.headers.get("HX-Request") != "true":
        return redirect("game-detail", game_id=output.view.game_id)
    if _wants_meeting(request):
        return _meeting_response(request, output.view, output.mudang_variant, scenario_id=output.scenario_id)
    return JsonResponse(to_jsonable(output))


@game_request
@require_http_methods(["POST"])
def start_tutorial(request):
    """튜토리얼 판 시작(`POST /tutorial`). 입력은 시작 UUID·무당 모습만 받고, 시드·프리셋·회차는 서버 상수다."""
    from_form = request.content_type == "application/x-www-form-urlencoded"
    if from_form:
        form = request.POST
        if set(form) - {"csrfmiddlewaretoken", "start_token", "mudang_variant"} or any(
                len(form.getlist(key)) != 1 for key in form):
            raise ValueError("unknown_field")
        body = {key: form[key] for key in ("start_token", "mudang_variant") if key in form}
    else:
        body = _body(request)
        if set(body) - {"start_token", "mudang_variant"}:
            raise ValueError("unknown_field")
    body["player_pseudonym"] = request.player_pseudonym
    data = parse_game_input(StartTutorialInput, body)
    UUID(data.start_token)
    output = get_game_service().start_tutorial(data, request.player_pseudonym, request.consented)
    request.game_id = output.view.game_id
    if from_form and request.headers.get("HX-Request") != "true":
        return redirect("game-detail", game_id=output.view.game_id)
    if _wants_meeting(request):
        return _meeting_response(request, output.view, output.mudang_variant, scenario_id=output.scenario_id)
    return JsonResponse(to_jsonable(output), status=201)


@game_request
@require_GET
def game_detail(request, game_id):
    """자기 판의 공개 상태를 JSON·전체 화면·htmx 조각으로 반환한다."""
    output = get_game_service().detail(game_id, request.player_pseudonym)
    request.game_id = output.view.game_id
    if _wants_meeting(request):
        return _meeting_response(request, output.view, output.mudang_variant, scenario_id=output.scenario_id)
    return JsonResponse(to_jsonable(output))


@game_request
@require_http_methods(["POST"])
def submit_command(request, game_id):
    """소유권 확인 뒤 URL·본문의 판 ID와 명령 DTO를 검증한다."""
    service = get_game_service()
    state, truth = service.owned(game_id, request.player_pseudonym)
    request.game_id = state.meta.game_id
    wants_meeting = _wants_meeting(request)
    if wants_meeting and request.content_type == "application/x-www-form-urlencoded":
        body = _meeting_command_body(request.POST)
    else:
        body = _body(request)
    data = parse_game_input(SubmitCommandInput, body)
    if data.game_id != game_id or data.expected_state_version < 0:
        raise ValueError("invalid_command")
    UUID(data.command_id)
    if data.intent_attempt_id is None:
        output = service.command_from_owned(data, request.player_pseudonym, state, truth)
    else:
        if service.interactions is None:
            raise InteractionUnavailable
        output = service.interactions.submit_linked(data, request.player_pseudonym)
    if not output.accepted and not output.replayed:
        request.command_reject = output.reject
    if not wants_meeting:
        return command_response(output)
    if output.replayed:
        # HTML만 최신 상태로 다시 그린다. JSON은 계약대로 저장 응답을 재생한다.
        current = service.detail(game_id, request.player_pseudonym)
        return _meeting_response(request, current.view, current.mudang_variant, scenario_id=current.scenario_id)
    return _meeting_response(request, output.view, state.meta.mudang_variant, _command_status(output),
                             _previous_interrogation(data.command, state, output.view),
                             None if output.accepted else output.reject, state.meta.scenario_id)


@game_request
@require_GET
def progress(request):
    """본인의 진척을 JSON 또는 수집한 단서만 포함한 전체 화면으로 반환한다."""
    output = get_game_service().player_progress(request.player_pseudonym)
    if _wants_meeting(request):
        from .story_view import progress_context
        # htmx는 판 화면 안 '원한 단서' 패널(B-10-01, 11/9 사용자 결정)이 쓰는 조각, 일반 요청은 로비의 전체 화면이다.
        template = "progress_panel.html" if request.headers.get("HX-Request") == "true" else "progress.html"
        return render(request, template, progress_context(output.progress))
    return JsonResponse(to_jsonable(output))


@game_request
@require_GET
def review(request, game_id):
    """종료된 자기 판에서 공개 가능한 복기 DTO만 반환한다.

    htmx 요청은 판 화면 안 복기 패널 조각(`review_panel.html`, B-10-01)을 받는다. 종료 전에는 어느 표현이든 403이다.
    """
    service = get_game_service()
    output = service.review(game_id, request.player_pseudonym)
    request.game_id = output.game_id
    if _wants_meeting(request):
        from .story_view import review_context
        state, _ = service.owned(game_id, request.player_pseudonym)
        template = "review_panel.html" if request.headers.get("HX-Request") == "true" else "review.html"
        content = service.dependencies(state.meta).content
        context = review_context(output, content, state.public)
        # 튜토리얼 판 복기는 원한 단서 칸과 복기 광고를 뺀다(진척·광고 제외 결정).
        context["tutorial_mode"] = tutorial.is_tutorial(state.meta.scenario_id)
        from .story_view import narration_context
        context.update(narration_context(service.review_narration(game_id, request.player_pseudonym), content,
                                         output.game_id))
        return render(request, template, context)
    return JsonResponse(to_jsonable(output))


@game_request
@require_GET
def review_narration(request, game_id):
    """복기 해설 LLM 요약(E-10-01). htmx는 폴링 조각, 그 밖에는 `ReviewNarrationOutput` JSON. 종료 전 403."""
    service = get_game_service()
    output = service.review_narration(game_id, request.player_pseudonym)
    request.game_id = game_id
    if request.headers.get("HX-Request") == "true":
        from .story_view import narration_context
        state, _ = service.owned(game_id, request.player_pseudonym)
        return render(request, "review_narration.html",
                      narration_context(output, service.dependencies(state.meta).content, state.meta.game_id))
    return JsonResponse(to_jsonable(output))
