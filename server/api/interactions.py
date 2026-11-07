"""공통 API의 자유 입력·렌더·수첩·설문 HTTP 경계."""
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods
from engine.domain.serde import to_jsonable
from server.app.dto import (CommandOutput, ParseUtteranceInput, ResolveUtteranceInput,
                            SaveNotebookInput, SubmitSurveyInput)
from server.app.interactions import InteractionUnavailable
from server.paldo_web.composition import get_game_service
from .input_validation import parse_game_input
from .views import game_request, command_response, _body, _meeting_response, _command_status
from .interaction_forms import utterance_form
from .interaction_view import cards_context, line_context


def _service(request, game_id):
    service = get_game_service()
    state, _ = service.owned(game_id, request.player_pseudonym)
    request.game_id = state.meta.game_id
    if service.interactions is None:
        raise InteractionUnavailable
    return service.interactions


def _input(request, dto, game_id, attempt_id=None):
    body = (utterance_form(request.POST, attempt_id is not None)
            if dto in (ParseUtteranceInput, ResolveUtteranceInput)
            and request.content_type == "application/x-www-form-urlencoded" else _body(request))
    data = parse_game_input(dto, body)
    if data.game_id != game_id or (attempt_id is not None and data.attempt_id != attempt_id):
        raise ValueError("path_body_mismatch")
    return data


def _response(request, game_id, output):
    if request.headers.get("HX-Request") == "true":
        game = get_game_service()
        current = game.detail(game_id, request.player_pseudonym)
        if isinstance(output, CommandOutput):
            response = _meeting_response(request, current.view if output.replayed else output.view,
                                         current.mudang_variant, _command_status(output))
            response["HX-Retarget"] = "#meeting"
            response["HX-Reswap"] = "outerHTML"
            return response
        if output.failure and output.failure.value == "empty_input":
            return HttpResponse(status=204)
        response = render(request, "speech_cards.html", cards_context(output, current.view))
        response["HX-Retarget"] = "#speech-cards"
        response["HX-Reswap"] = "outerHTML"
        return response
    if isinstance(output, CommandOutput):
        return command_response(output)
    return JsonResponse(to_jsonable(output))


@game_request
@require_http_methods(["POST"])
def parse_utterance(request, game_id):
    """자기 판의 엄격한 JSON 해석 요청을 처리한다."""
    service = _service(request, game_id)
    return _response(request, game_id, service.parse(_input(request, ParseUtteranceInput, game_id), request.player_pseudonym))


@game_request
@require_http_methods(["POST"])
def resolve_utterance(request, game_id, attempt_id):
    """판·시도 소유권을 확인한 카드 반응을 처리한다."""
    service = _service(request, game_id)
    return _response(request, game_id, service.resolve(_input(request, ResolveUtteranceInput, game_id, attempt_id), request.player_pseudonym))


@game_request
@require_GET
def poll_render(request, game_id, job_id):
    """공개 문장 JSON 또는 완료 시 폴링 속성이 없는 htmx 조각을 반환한다."""
    output = _service(request, game_id).poll(game_id, job_id, request.player_pseudonym)
    if request.headers.get("HX-Request") == "true":
        return render(request, "speech_line.html", line_context(game_id, output))
    return JsonResponse(to_jsonable(output))


@game_request
@require_http_methods(["PUT"])
def save_notebook(request, game_id):
    """의심도·확신도를 검증하고 성공 시 본문 없는 204를 반환한다."""
    service = _service(request, game_id)
    service.notebook(_input(request, SaveNotebookInput, game_id), request.player_pseudonym)
    return HttpResponse(status=204)


@game_request
@require_http_methods(["POST"])
def submit_survey(request, game_id):
    """종료 후 설문을 저장하고 본문 없는 204를 반환한다."""
    service = _service(request, game_id)
    service.survey(_input(request, SubmitSurveyInput, game_id), request.player_pseudonym)
    return HttpResponse(status=204)
