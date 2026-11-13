"""공개 기록과 표시 줄만으로 사건별 표정을 정한다(B-10-07 1-3).

표현 계층의 순수 계산이다. 진실·역할·현재 생존 상태·내 점괘·읽은 위치를
입력받지 않으며 같은 공개 줄 ID에는 새로고침·역방향 재생 때도 같은 표정을 준다.
"""
from collections import defaultdict

from engine.domain.model import ClaimType, CommandKind, DivinationResult, ReactionIntent
from .vn_script import reaction_target

PHASE_ORDER = ("morning", "round1", "interrogation", "round2", "vote", "runoff", "night")
TELL_NOTICE = "기색이 나타났다."


def expression_cues(public, lines):
    """DOM과 같은 단계·줄 순서로 공개 사건을 적용해 (표정 사전, 기색 줄 ID)를 반환한다.

    대상 반응은 사건 뒤 그 인물의 대사 하나에서 소모한다. 마스코트·해설·희생자
    한 박자 컷·감금 결과는 '다음 자기 대사'나 아침 첫 대사로 세지 않는다.
    투표 생존 기쁨과 아침 첫 대사 슬픔이 겹치면 그 한 줄은 기쁨이다(11/9 승인).
    """
    speeches = {s.speech_id: s for s in public.speeches}
    claims = {c.claim_id: c for c in public.claims}
    shown = {t.claim_id for t in public.tells if t.shown is True}
    missing = {f"announcement-{d.night}": d.night + 1 for d in public.disappearances}
    locks = {f"lock-{lock.day}": lock for lock in public.locks}
    announced = set()
    first = defaultdict(set)
    pending = defaultdict(set)
    moods, tells = {}, set()
    order = {phase: i for i, phase in enumerate(PHASE_ORDER)}
    ordered = sorted(enumerate(lines), key=lambda item: (
        item[1].day, order.get(item[1].phase, len(order)), item[0]))
    for _, line in ordered:
        key, actor = line.line_id, line.speaker_id
        if key in missing:
            announced.add(missing[key])
        speech = speeches.get(key)
        if speech:
            own_claims = [claims[cid] for cid in speech.claim_ids if cid in claims]
        else:
            claim = claims.get(key.removeprefix("statement-")) if key.startswith("statement-") else None
            own_claims = [claim] if claim else []
        tell = any(c.claim_id in shown and c.speaker == actor for c in own_claims)
        if tell:
            tells.add(key)
        if actor and line.sprite_id and not (line.guide or line.mascot):
            # 피해자 한 박자 컷은 자기 대사가 아니다. 규격표가 정한 감금 결과 컷만 슬픔이다.
            if line.beat:
                moods[key] = ""
            elif key in locks:
                moods[key] = "sad"
            else:
                cues = pending.pop(actor, set())
                sad_first = line.day in announced and actor not in first[line.day]
                first[line.day].add(actor)
                if sad_first:
                    cues.add("sad")
                if speech and speech.reaction is ReactionIntent.CONFESS:
                    cues.add("sad")
                if tell:
                    cues.add("flustered")
                if sad_first and "vote_joy" in cues:
                    moods[key] = "joy"
                else:
                    moods[key] = next((m for m in ("sad", "flustered", "joy")
                                       if m in cues or (m == "joy" and "vote_joy" in cues)), "")
        # 사건을 말한 바로 그 줄부터 대상 표정을 앞당기지 않는다.
        if speech:
            if speech.kind is CommandKind.DEFEND and speech.target:
                pending[speech.target].add("joy")
            elif speech.kind in (CommandKind.SUSPECT, CommandKind.POINT_CONTRADICTION):
                target = reaction_target(public, speech)
                if target:
                    pending[target].add("flustered")
            elif speech.kind is CommandKind.REVEAL_DIVINATION:
                for claim in own_claims:
                    if claim.claim_type is ClaimType.DIVINATION_REVEAL and claim.divination_result is DivinationResult.MURKY:
                        pending[claim.subject].add("flustered")
        if key in locks:
            lock = locks[key]
            for vote in public.votes:
                if vote.day == lock.day and vote.target is not None and vote.target != lock.locked:
                    pending[vote.target].add("vote_joy")
    return moods, frozenset(tells)
