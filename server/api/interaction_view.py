"""자유 입력 카드와 렌더 결과를 공개 화면 값으로 변환한다."""
from dataclasses import dataclass, replace

from .meeting_view import MeetingClaim, meeting_context


@dataclass(frozen=True)
class SpeechCard:
    """한 후보의 편집 폼 값. 엔진 객체를 템플릿에 넘기지 않는다."""

    index: int
    kind: str
    target: str
    addressee: str
    target_name: str
    addressee_name: str
    night: int
    slot: str
    interrogation_kind: str
    evidence: list[str]
    divination: str
    missing: list[str]
    evidence_options: list[MeetingClaim]
    ambiguous_evidence: bool
    evidence_count: int = 0


def cards_context(output, view):
    """카드·실패 상태와 공개 대상/근거 선택지만 만든다."""
    context = meeting_context(view)
    claims_by_id = {claim.card.claim_id: claim for claim in context["claims"]}
    cards = []
    for index, item in enumerate(output.cards):
        command = item.command
        divination = command.divination
        evidence_options = context["claims"]
        if item.evidence_alternatives:
            evidence_options = [
                replace(claims_by_id[ref.record_id], evidence_value=f"{ref.type.value}:{ref.record_id}")
                for ref in item.evidence_alternatives if ref.record_id in claims_by_id
            ]
        cards.append(SpeechCard(index, command.kind.value, command.target or "", command.addressee or "",
            view.names.get(command.target, command.target or ""), view.names.get(command.addressee, command.addressee or ""),
            command.night or 0, command.slot.value if command.slot else "",
            command.interrogation_kind.value if command.interrogation_kind else "",
            [f"{e.type.value}:{e.record_id}" for e in command.evidence],
            f"{divination.night}:{divination.target}:{divination.result.value}" if divination else "",
            list(item.missing_slots), evidence_options, bool(item.evidence_alternatives),
            sum(f"{e.type.value}:{e.record_id}" in {c.evidence_value for c in evidence_options} for e in command.evidence)))
    return {**context, "attempt_id": output.attempt_id, "attempt_version": output.state_version,
            "cards": cards, "failure": output.failure.value if output.failure else "",
            "suggestions": list(output.suggestions), "needs_confirmation": output.needs_confirmation}


def line_context(game_id, output, speaker="", is_interrogation_answer=False):
    """공개 문장과 식별자·완료 여부·추궁 답변 표시 여부만 전달한다."""
    return {"line": {"game_id": game_id, "speech_id": output.speech_id, "job_id": output.job_id,
                     "text": output.text, "final": output.final, "speaker": speaker,
                     "is_interrogation_answer": is_interrogation_answer}}
