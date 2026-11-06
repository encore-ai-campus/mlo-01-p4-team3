"""공통 MeetingVM에 회의 템플릿의 이미지 ID와 폼 선택지를 더한다."""
from dataclasses import dataclass

from engine.domain.model import ClaimType
from engine.ports import PlayerView
from .viewmodels import ClaimCardVM, DisappearanceVM, PLACE_KO, build_meeting_vm


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
    """플레이어가 공개할 수 있는 자신의 점괘 한 건."""

    value: str
    label: str


def _place_asset(place):
    return f"place:{place.value}" if place and place.value in PLACE_KO else ""


def meeting_context(view: PlayerView):
    """공개 화면 모델과 본인 점괘 선택지만 템플릿에 전달한다."""
    vm = build_meeting_vm(view)
    claims = []
    for card, record in zip(vm.claims, view.public.claims, strict=True):
        kind = "divination" if record.claim_type is ClaimType.DIVINATION_REVEAL else "claim"
        claims.append(MeetingClaim(card, _place_asset(record.place), f"{kind}:{card.claim_id}"))
    disappearances = [MeetingDisappearance(item, _place_asset(record.place))
                      for item, record in zip(vm.disappearances, view.public.disappearances, strict=True)]
    divinations = [DivinationOption(f"{night}:{target}:{result.value}",
                                    f"{night}일째 밤 · {view.names.get(target, target)} · "
                                    f"{'맑음' if result.value == 'clear' else '탁함'}")
                   for night, items in view.player.divinations.items() for target, result in items.items()]
    return {"vm": vm, "claims": claims, "disappearances": disappearances, "divination_options": divinations}
