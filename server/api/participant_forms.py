"""수첩·확신도·설문 화면 모델과 HTML 폼을 공통 DTO 입력으로 바꾼다."""
import logging
from dataclasses import dataclass

from django.http import QueryDict

from server.app.dto import SaveNotebookInput, SubmitSurveyInput
from server.app.interactions import InteractionUnavailable
from .input_validation import parse_game_input


@dataclass(frozen=True)
class NotebookEntryVM:
    """공개 주민 이름과 플레이어가 저장한 의심도만 담는다."""

    npc_id: str
    name: str
    value: int
    saved: int | None = None   # 저장한 적 없으면 None(모르겠음으로 표시)
    active: bool = True        # 실종·감금이면 False: 수첩 창에서도 버튼을 숨기고 값만 보존


def participant_context(service, view):
    """판 화면에 수첩·설문 상태를 더한다. 저장소 미연결 시 입력 폼을 숨긴다."""
    if service.interactions is None:
        return {"participant_forms_available": False}
    with service.interactions.participant_records(view.game_id) as records:
        confidence = records.notebook.get("vote_confidence")
        submitted = records.survey is not None
    return {
        "participant_forms_available": True,
        "notebook_entries": [NotebookEntryVM(npc, name, view.player.notebook.get(npc, 50), view.player.notebook.get(npc),
                                         npc not in view.locked and npc not in view.missing)
                             for npc, name in view.names.items() if npc != "mudang"],
        "vote_confidence": confidence,
        # 주민 창·수첩 창의 한 번 누름 표시(11/10 사용자 결정). 값은 기존 0~100 형식에 그대로 저장한다.
        "notebook_mark_choices": [("100", "수상함"), ("50", "모르겠음"), ("0", "믿음")],
        "confidence_choices": [(0, "전혀 확신하지 않음"), (25, "조금 확신함"),
                               (50, "보통"), (75, "많이 확신함"), (100, "매우 확신함")],
        "survey_submitted": submitted,
        "survey_questions": [("fun", "이번 판은 재미있었나요?"),
                             ("unfair", "이번 판의 결과가 억울하게 느껴졌나요?"),
                             ("npc_plausible", "마을 사람들의 말과 행동이 그럴듯했나요?")],
        "survey_choices": [(1, "전혀 그렇지 않다"), (2, "그렇지 않다"), (3, "보통이다"),
                           (4, "그렇다"), (5, "매우 그렇다")],
    }


def participant_input(request, dto, game_id):
    """HTML 폼을 DTO 입력으로 변환하고 알 수 없는 필드·반복 필드와 URL 불일치를 거부한다."""
    from .views import _body

    if request.content_type != "application/x-www-form-urlencoded":
        body = _body(request)
    else:
        form = QueryDict(request.body)
        if any(len(form.getlist(key)) != 1 for key in form):
            raise ValueError("duplicate_field")
        allowed = {"csrfmiddlewaretoken", "game_id"}
        if dto is SaveNotebookInput:
            allowed |= {"vote_confidence"} | {key for key in form if key.startswith("entry:")}
            body = {"game_id": form.get("game_id"),
                    "entries": {key[6:]: int(value) for key, value in form.items() if key.startswith("entry:")}}
            if form.get("vote_confidence"):
                body["vote_confidence"] = int(form["vote_confidence"])
        elif dto is SubmitSurveyInput:
            allowed |= {"fun", "unfair", "npc_plausible", "comment", "skipped"}
            skipped = form.get("skipped", "false")
            if skipped not in ("true", "false"):
                raise ValueError("invalid_survey_skipped")
            body = {"game_id": form.get("game_id"), "comment": form.get("comment", ""),
                    "skipped": skipped == "true"}
            for field in ("fun", "unfair", "npc_plausible"):
                if field in form:
                    body[field] = int(form[field])
                elif body["skipped"]:
                    body[field] = None
        else:
            raise ValueError("unknown_participant_input")
        if set(form) - allowed:
            raise ValueError("unknown_field")
    data = parse_game_input(dto, body)
    if data.game_id != game_id:
        raise ValueError("path_body_mismatch")
    return data


def backfill_surveys(service, player, game_ids, load_game):
    """같은 참가자의 다음 요청(홈)에서 설문 이벤트 없는 끝난 판을 건너뜀으로 채운다(B-09-07 9번).

    보완이 실패해도 홈은 막지 않는다. 다음 요청에서 다시 시도한다.
    """
    if service.interactions is None:
        return 0
    try:
        return service.interactions.backfill_skipped_surveys(player, game_ids, load_game)
    except (InteractionUnavailable, ConnectionError, TimeoutError):
        logging.getLogger(__name__).warning("survey_backfill_unavailable")
        return 0
