"""표현: NPC 대사 LLM(E-11-13) HTTP 경계. 회의 발언 다듬기의 선조회와 화면 고정.

- `GET /games/{id}/lines`: 그날 다듬기 줄의 상태·다듬은 문장·고정값(고정하지 않음). 꺼져 있으면 빈 목록.
- `POST /games/{id}/lines/{speech_id}/shown` `{"variant": "polished"|"template", "job_rev": n}`: 대사창이 띄운
  문장을 첫 확정 1회로 고정하고 고정값을 돌려준다.
규칙은 응용 `server/app/npc_lines.py`에 있고, 여기서는 입력 검증과 괄호 조사 표시 변환만 한다.
"""
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods

from server.app.interactions import InteractionUnavailable
from server.paldo_web.composition import get_game_service
from .views import _body, game_request
from .vn_script import attach_particles


def _lines():
    lines = getattr(get_game_service(), "npc_lines", None)
    if lines is None:
        raise InteractionUnavailable
    return lines


@game_request
@require_GET
def npc_lines(request, game_id):
    """선조회. 판 소유권은 응용이 확인한다."""
    return JsonResponse(_lines().lines(game_id, request.player_pseudonym, display=attach_particles))


@game_request
@require_http_methods(["POST"])
def npc_line_shown(request, game_id, speech_id):
    """띄운 변형 고정."""
    body = _body(request)
    if set(body) - {"variant", "job_rev"}:
        raise ValueError("unknown_field")
    variant, rev = body.get("variant"), body.get("job_rev", 0)
    if not isinstance(variant, str) or not isinstance(rev, int) or isinstance(rev, bool):
        raise ValueError("invalid_input")
    return JsonResponse(_lines().shown(game_id, request.player_pseudonym, speech_id, variant, rev))
