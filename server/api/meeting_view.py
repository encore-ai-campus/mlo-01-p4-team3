"""공통 MeetingVM에 회의 템플릿의 이미지 ID와 폼 선택지를 더한다."""
from dataclasses import dataclass

from engine.domain.model import PLAYER_ID, ClaimType, CommandKind, DivinationResult, Phase
from engine.ports import PlayerView
from .viewmodels import DIVINATION_KO, PLACE_KO, ClaimCardVM, DisappearanceVM, PersonVM, build_meeting_vm


@dataclass(frozen=True)
class MeetingClaim:
    """공개 진술 카드, 장소 이미지 ID, 명령에 보내는 근거 값."""

    card: ClaimCardVM
    place_asset_id: str
    evidence_value: str


@dataclass(frozen=True)
class MeetingDisappearance:
    """공개 실종 기록과 장소 이미지 ID."""

    record: DisappearanceVM
    place_asset_id: str


@dataclass(frozen=True)
class DivinationOption:
    """플레이어가 선택한 공개 점괘의 폼 값과 이름."""

    value: str
    label: str


@dataclass(frozen=True)
class PublishedVote:
    """엔진이 공개한 한 표와 해당 투표 회차."""

    day: int
    round: str
    voter: str
    target: str


@dataclass(frozen=True)
class PublishedLock:
    """투표 종료 후 공개된 감금 대상과 금줄 반응."""

    day: int
    name: str | None
    revealed_possessed: bool | None


@dataclass(frozen=True)
class MeetingAction:
    """행동 폼 한 개. 투표와 점치기는 대상마다 별도 명령 ID를 가진다."""

    kind: str
    target: PersonVM | None = None


def _place_asset(place):
    return f"place:{place.value}" if place and place.value in PLACE_KO else ""


def meeting_context(view: PlayerView):
    """공개 진술·투표와 허용 행동, 본인의 점괘를 화면 모델로 전달한다."""
    vm = build_meeting_vm(view)
    claims = []
    for card, record in zip(vm.claims, view.public.claims, strict=True):
        kind = "divination" if record.claim_type is ClaimType.DIVINATION_REVEAL else "claim"
        claims.append(MeetingClaim(card, _place_asset(record.place), f"{kind}:{card.claim_id}"))
    disappearances = [MeetingDisappearance(item, _place_asset(record.place))
                      for item, record in zip(vm.disappearances, view.public.disappearances, strict=True)]
    own_divinations = []
    divination_options = []
    for night, items in view.player.divinations.items():
        for target, result in items.items():
            label = f"{night}일째 밤 · {view.names.get(target, target)}"
            own_divinations.append(f"{label} · {DIVINATION_KO[result]}")
            divination_options.extend(
                DivinationOption(f"{night}:{target}:{declared.value}", f"{label} · {DIVINATION_KO[declared]}")
                for declared in DivinationResult
            )
    published_votes = [
        PublishedVote(vote.day, vote.round, view.names.get(vote.voter, vote.voter),
                      view.names.get(vote.target, vote.target))
        for vote in view.public.votes
    ]
    published_locks = [
        PublishedLock(lock.day, view.names.get(lock.locked, lock.locked) if lock.locked is not None else None,
                      lock.revealed_possessed)
        for lock in view.public.locks
    ]
    active_targets = [person for person in vm.people if person.status == "active" and person.npc_id != PLAYER_ID]
    vote_targets = active_targets
    if view.phase is Phase.RUNOFF:
        vote_targets = [person for person in active_targets if person.npc_id in vm.runoff_candidates]
    actions = []
    for kind in vm.allowed_actions:
        if kind == CommandKind.VOTE.value:
            actions.extend(MeetingAction(kind, target) for target in vote_targets)
        elif kind == CommandKind.DIVINE.value:
            actions.extend(MeetingAction(kind, target) for target in active_targets)
        else:
            actions.append(MeetingAction(kind))
    return {"vm": vm, "claims": claims, "disappearances": disappearances,
            "own_divinations": own_divinations, "divination_options": divination_options,
            "published_votes": published_votes, "published_locks": published_locks, "active_targets": active_targets,
            "actions": actions}
