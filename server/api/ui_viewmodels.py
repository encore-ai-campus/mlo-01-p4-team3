"""공통 화면 계약을 바꾸지 않고 PM 시안의 공개 표시 정보만 조립한다."""
from dataclasses import asdict, dataclass
from .viewmodels import ClaimCardVM, DisappearanceVM, SpeechVM, PLACE_KO, build_meeting_vm


@dataclass
class UIClaimCard(ClaimCardVM):
    """공개 진술의 필터·장소·정정 표시 정보. 숨은 진위를 포함하지 않는다."""
    speaker_id: str = ''
    night: int | None = None
    slot: str = ''
    place: str = ''
    corrects: str | None = None


@dataclass
class UISpeech(SpeechVM):
    """읽는 공개 발언의 화자·날짜·단계만 추가한다."""
    speaker_id: str = ''
    day: int = 0
    phase: str = ''


@dataclass
class UIDisappearance(DisappearanceVM):
    """이미 발표된 피해자의 공개 ID로 그림을 고른다."""
    npc_id: str = ''


def build_ui_vm(view):
    """PlayerView의 공개 기록만 UI 전용 모델로 바꾸며 공통 DTO는 수정하지 않는다."""
    vm = build_meeting_vm(view)
    vm.claims = [UIClaimCard(**asdict(card), speaker_id=record.speaker, night=record.night,
        slot=record.slot.value if record.slot else '',
        place=PLACE_KO.get(record.place.value, '') if record.place else '', corrects=record.corrects)
        for card, record in zip(vm.claims, view.public.claims, strict=True)]
    vm.speeches = [UISpeech(**asdict(speech), speaker_id=record.speaker, day=record.day, phase=record.phase.value)
                   for speech, record in zip(vm.speeches, view.public.speeches, strict=True)]
    vm.disappearances = [UIDisappearance(**asdict(item), npc_id=record.victim)
        for item, record in zip(vm.disappearances, view.public.disappearances, strict=True)]
    return vm
