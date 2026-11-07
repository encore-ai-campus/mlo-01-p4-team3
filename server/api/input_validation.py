"""공통 DTO 형식과 B 게임 API의 확정된 입력 제약을 검사한다."""
from uuid import UUID

from engine.domain.serde import SchemaError, from_jsonable
from server.app.dto import ParseUtteranceInput, ResolveUtteranceInput, SaveNotebookInput, SubmitSurveyInput, UtteranceReaction


def parse_game_input(dto_type, body):
    """JSON을 공통 DTO로 바꾸고 저장소와 무관한 입력 규칙을 적용한다."""
    data = from_jsonable(dto_type, body)
    if isinstance(data, ParseUtteranceInput) and (data.expected_state_version < 0 or len(data.text) > 4000):
        raise SchemaError("$", "버전은 0 이상이고 문장은 4000자 이하여야 합니다.")
    if isinstance(data, SaveNotebookInput):
        if any(not 0 <= value <= 100 for value in data.entries.values()):
            raise SchemaError("$.entries", "수첩 값은 0부터 100까지입니다.")
        if data.vote_confidence is not None and not 0 <= data.vote_confidence <= 100:
            raise SchemaError("$.vote_confidence", "투표 확신도는 0부터 100까지입니다.")
    elif isinstance(data, SubmitSurveyInput):
        _validate_survey(data)
    elif isinstance(data, ResolveUtteranceInput):
        _validate_reaction(data)
    return data


def _validate_survey(data):
    scores = (data.fun, data.unfair, data.npc_plausible)
    if data.skipped:
        if any(score is not None for score in scores) or data.comment:
            raise SchemaError("$", "설문을 건너뛰면 점수와 의견을 비워야 합니다.")
        return
    for field, score in zip(("fun", "unfair", "npc_plausible"), scores):
        if score is None or not 1 <= score <= 5:
            raise SchemaError(f"$.{field}", "설문 점수는 1부터 5까지입니다.")
    if len(data.comment) > 4000:
        raise SchemaError("$.comment", "설문 의견은 4000자 이하여야 합니다.")


def _validate_reaction(data):
    if data.reaction in (UtteranceReaction.CONFIRM, UtteranceReaction.EDIT):
        if not data.command_id:
            raise SchemaError("$.command_id", "실행할 명령 ID가 필요합니다.")
        try:
            UUID(data.command_id)
        except ValueError:
            raise SchemaError("$.command_id", "명령 ID는 UUID여야 합니다.") from None
        if data.expected_state_version is None or data.expected_state_version < 0:
            raise SchemaError("$.expected_state_version", "실행할 상태 버전이 필요합니다.")
    if data.reaction == UtteranceReaction.CONFIRM and (data.chosen_index is None or data.chosen_index < 0):
        raise SchemaError("$.chosen_index", "실행할 카드 번호가 필요합니다.")
    if data.reaction == UtteranceReaction.EDIT and data.edited_command is None:
        raise SchemaError("$.edited_command", "수정한 명령이 필요합니다.")
    if data.reaction == UtteranceReaction.RETRY and data.retry_text is None:
        raise SchemaError("$.retry_text", "다시 해석할 문장이 필요합니다.")
