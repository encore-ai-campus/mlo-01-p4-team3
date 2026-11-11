"""공통 MeetingVM에 회의 템플릿의 이미지 ID와 폼 선택지를 더한다."""
from dataclasses import dataclass, replace

from engine.domain.model import PLAYER_ID, ClaimType, CommandKind, DivinationResult, Phase, ReactionIntent
from engine.ports import PlayerView
from .alibi_grid import build_alibi_grid
from .viewmodels import PLACE_KO, ClaimCardVM, DisappearanceVM, PersonVM, build_meeting_vm
from .ui_viewmodels import build_ui_vm
from .tutorial_script import CLUE_DUPLICATE, CLUE_NEW, CLUE_UNLOCK, FIRST_GAME_END, GUIDE_SPEAKER, guide_lines
from .vn_script import VNLine, announcement_lines, build_vn_lines, claim_card_text, reaction_lines
from .vn_expressions import TELL_NOTICE, expression_cues

DIVINATION_KO = {DivinationResult.CLEAR: "맑음", DivinationResult.MURKY: "탁함"}


@dataclass(frozen=True)
class MeetingClaim:
    """공개 진술 카드, 장소 이미지 ID, 명령에 보내는 근거 값."""

    card: ClaimCardVM
    place_asset_id: str
    evidence_value: str
    night: int | None = None   # 행적 질문 시간 칸을 맞출 진술의 밤(점괘 공개는 없음)
    slot: str = ""             # 진술의 시간 값(evening|midnight)
    corrected_text: str = ""   # 정정 진술이면 고치기 전 진술 문장(화면 기록에 그 진술이 있을 때만)


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


GUIDE_MASCOT = "mascot:jokjebi"


def _guide_vn_lines(view: PlayerView, k: int):
    """그날의 안내 줄을 (실종 발표 앞 줄, 진술 앞 줄, 나머지 줄)로 나눈다. 밤 화면에는 족제비 그림을 세우지 않는다."""
    before, lead, rest = [], [], []
    for line in guide_lines(k, day=view.day):
        sprite = "" if line.phase == Phase.NIGHT.value else GUIDE_MASCOT
        item = VNLine(line.line_id, line.day, line.phase, GUIDE_SPEAKER, line.text, sprite_id=sprite, guide=True)
        (before if line.step == "start" else lead if line.step == "morning1" else rest).append(item)
    return before, lead, rest


# 고백 뒤 단서 알림 종류(server/app/clue_notice.py의 값) → 족제비 줄. 새 단서·한풀이 열림은 단서 문장 해설 줄을 먼저 둔다.
CLUE_LINES = {"new": CLUE_NEW, "duplicate": CLUE_DUPLICATE, "unlock": CLUE_UNLOCK}


def _clue_vn_lines(view: PlayerView, notices) -> dict[str, list[VNLine]]:
    """고백 발언 ID → 그 뒤에 붙일 단서 알림 줄. 안내 스위치와 무관하게 늘 나온다(guide 아님, 마스코트 줄)."""
    confess = {(s.day, s.speaker): s for s in view.public.speeches if s.reaction is ReactionIntent.CONFESS}
    out: dict[str, list[VNLine]] = {}
    for notice in notices or ():
        speech = confess.get((notice.day, notice.npc_id))
        if speech is None:
            continue
        phase, base = speech.phase.value, f"clue-{speech.speech_id}"
        lines = [] if notice.kind == "duplicate" else [VNLine(f"{base}-text", speech.day, phase, "", notice.clue_text)]
        sprite = "" if phase == Phase.NIGHT.value else GUIDE_MASCOT
        lines += [VNLine(f"{base}-{i}", speech.day, phase, GUIDE_SPEAKER, text, sprite_id=sprite, mascot=bool(sprite))
                  for i, text in enumerate(CLUE_LINES[notice.kind])]
        out[speech.speech_id] = lines
    return out


def meeting_context(view: PlayerView, guide_k: int | None = None, first_game: bool = False, clue_notices=(),
                    temperaments=None):
    """공개 진술·투표와 허용 행동, 본인의 점괘를 화면 모델로 전달한다.

    guide_k(이번 판 씐 자 수)가 있으면 도움말에 "이번 판에서 안내 보기" 스위치를 두고, first_game이면 대사창 안내 줄을
    서버가 넣는다(기본 켬). 첫 판이 아니면 같은 안내 문장을 숨은 목록으로 내려 주어 스위치를 켰을 때만 보인다.
    clue_notices(server/app/clue_notice.ClueNotice 목록)가 있으면 고백 발언 바로 뒤에 단서 알림 줄을 붙인다.
    플레이어의 의심 제기·모순 지적 발언 바로 뒤에는 지목된 주민의 반응 줄을 붙인다(temperaments: 주민 ID → 성향 ID).
    """
    vm = build_ui_vm(view)
    claims = []
    for card, record in zip(vm.claims, view.public.claims, strict=True):
        kind = "divination" if record.claim_type is ClaimType.DIVINATION_REVEAL else "claim"
        if kind == "divination":
            subject = view.names.get(record.subject, record.subject)
            result = DIVINATION_KO.get(record.divination_result, "")
            card = replace(card, text=f"점괘 공개 · {subject} · {result}")
        else:
            card = replace(card, text=claim_card_text(view, record))   # 대사창 진술 줄과 같은 문장 틀(11/10 사용자 승인)
        claims.append(MeetingClaim(card, _place_asset(record.place), f"{kind}:{card.claim_id}",
                                   record.night if record.slot else None, record.slot.value if record.slot else ""))
    # 진술표 정정 표시(11/10 사용자 승인): 내부 진술 번호는 글로 보이지 않고, 정정 진술 배지의 설명에 고치기 전 문장을 둔다.
    texts = {item.card.claim_id: item.card.text for item in claims}
    claims = [replace(item, corrected_text=texts.get(getattr(item.card, "corrects", None) or "", "")) for item in claims]
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
    before, lead, rest = _guide_vn_lines(view, guide_k) if first_game and guide_k is not None else ([], [], [])
    actions = []
    for kind in vm.allowed_actions:
        if kind == CommandKind.VOTE.value:
            actions.extend(MeetingAction(kind, target) for target in vote_targets)
        elif kind == CommandKind.DIVINE.value:
            actions.extend(MeetingAction(kind, target) for target in active_targets)
        else:
            actions.append(MeetingAction(kind))
    announcements = announcement_lines(view)
    vn_lines = lead + build_vn_lines(view, DIVINATION_KO) + rest
    reactions = reaction_lines(view, temperaments)
    # records.html의 DOM 순서를 그대로 조립한 뒤 단계 순으로 계산한다(ui-vn.js collect와 동일).
    # 같은 기록을 다시 그려도 뒤 사건이 앞 대사의 표정을 바꾸지 않는다.
    expression_lines = announcements + vn_lines
    for speech in vm.speeches:
        expression_lines.append(VNLine(speech.speech_id, speech.day, speech.phase, speech.speaker_name,
                                       speech.text, speech.speaker_id, f"sprite:{speech.speaker_id}"))
        expression_lines.extend(reactions.get(speech.speech_id, ()))
    expressions, tell_lines = expression_cues(view.public, expression_lines)
    return {"vm": vm, "claims": claims, "claim_nights": sorted({c.card.night for c in claims if c.card.night is not None}), "disappearances": disappearances,
            "own_divinations": own_divinations, "divination_options": divination_options,
            "published_votes": published_votes, "published_locks": published_locks, "active_targets": active_targets,
            "vote_targets": vote_targets, "vn_lines": vn_lines,
            "vn_guide_before": before, "vn_announcements": announcements,
            "vn_expressions": expressions, "vn_tell_lines": tell_lines, "vn_tell_notice": TELL_NOTICE,
            "clue_lines": _clue_vn_lines(view, clue_notices),
            "reaction_lines": reactions,
            # 안내 켜기/끄기(11/10 사용자 결정): 첫 판은 서버가 안내 줄을 넣어 기본 켬. 다른 판은 같은 문장을 숨은 목록(guide_pool)으로
            # 내려 주고, 플레이어가 켜면 클라이언트가 켠 시점 기준으로 대사 줄을 만든다. 종료 화면 말풍선도 같은 스위치를 따른다.
            # 첫 판도 숨은 목록을 함께 내려(data-guide-first) 1일째가 지난 뒤 다시 켜면 판 시작 안내부터 다시 만든다.
            "first_game": first_game, "guide_pool": guide_lines(guide_k, replay=True) if guide_k is not None else [],
            "guide_end": FIRST_GAME_END if guide_k is not None else (), "guide_available": guide_k is not None,
            "guide_speaker": GUIDE_SPEAKER, "guide_mascot": GUIDE_MASCOT,
            # 행적표(11/10 사용자 결정): 공개 진술을 밤·시간대·장소 칸에 모은 읽기 전용 요약. 안내 줄 3은 창귀 2명 판에만.
            "alibi_grid": build_alibi_grid(view), "alibi_grid_duo": guide_k == 2,
            "actions": actions, "past_nights": list(range(max(0, view.day))),
            "contradiction_ready": sum(item.card.selectable and item.evidence_value.startswith('claim:') for item in claims) >= 2}
