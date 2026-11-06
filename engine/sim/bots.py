"""플레이어(무당) 대역 4종. 입력은 PlayerView뿐이며 진실·NPC 상태는 받지 않는다."""
from __future__ import annotations

import random
from itertools import combinations

from engine.domain.model import (
    PLAYER_ID, ClaimType, Command, CommandKind, DivinationPayload, DivinationResult,
    EvidenceRef, EvidenceType, InterrogationKind, Polarity, Slot,
)
from engine.ports import PlayerView


def _conflicts(view):
    corrected = {c.corrects for c in view.public.claims if c.corrects}
    claims = sorted((c for c in view.public.claims if c.claim_id not in corrected), key=lambda c: c.claim_id)
    pairs = []
    for a, b in combinations(claims, 2):
        if (a.night, a.slot) != (b.night, b.slot) or a.slot is None or not a.place or not b.place:
            continue
        if a.polarity is not Polarity.AFFIRM or b.polarity is not Polarity.AFFIRM or a.place is b.place:
            continue
        def subjects(c):
            if c.claim_type is ClaimType.WHEREABOUTS:
                return {c.subject}
            return set(c.seen) if c.claim_type is ClaimType.SIGHTING else set()
        for target in sorted(subjects(a) & subjects(b) & set(view.active) - {PLAYER_ID}):
            pairs.append((target, EvidenceRef(EvidenceType.CONTRADICTION, a.claim_id + "|" + b.claim_id)))
    return pairs


def legal_commands(view: PlayerView) -> list[Command]:
    """공개 정보로 안전한 후보를 만든다. 모든 슬롯 조합을 망라하는 목록은 아니다."""
    if view.awaiting != PLAYER_ID or not view.legal_kinds:
        return []
    npcs = sorted(set(view.active) - {PLAYER_ID})
    conflicts = _conflicts(view)
    out = []
    for kind in sorted(view.legal_kinds, key=lambda k: k.value):
        if kind in (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.DIVINE, CommandKind.VOTE):
            targets = sorted(set(view.runoff_candidates) - {PLAYER_ID}) if (
                kind is CommandKind.VOTE and view.runoff_candidates) else npcs
            out.extend(Command(kind, PLAYER_ID, target=target) for target in targets if target in npcs)
        elif kind in (CommandKind.ADVANCE, CommandKind.EXPLAIN, CommandKind.SILENCE):
            out.append(Command(kind, PLAYER_ID))
        elif kind is CommandKind.REVEAL_DIVINATION:
            for night, results in sorted(view.player.divinations.items()):
                for target, result in sorted(results.items()):
                    out.append(Command(kind, PLAYER_ID, divination=DivinationPayload(target, night, result)))
        elif kind is CommandKind.POINT_CONTRADICTION:
            out.extend(Command(kind, PLAYER_ID, target=target, evidence=[ref]) for target, ref in conflicts)
        elif kind is CommandKind.INTERROGATE:
            for npc in npcs:
                out.append(Command(kind, PLAYER_ID, addressee=npc,
                                   interrogation_kind=InterrogationKind.SUSPICION_REASON))
                for night in range(view.day):
                    for slot in Slot:
                        out.append(Command(kind, PLAYER_ID, addressee=npc,
                            interrogation_kind=InterrogationKind.WHEREABOUTS, night=night, slot=slot))
            out.extend(Command(kind, PLAYER_ID, addressee=target,
                interrogation_kind=InterrogationKind.CONTRADICTION, evidence=[ref]) for target, ref in conflicts)
    return out


class RandomBot:
    """구성한 합법 후보에서 균등하게 선택하는 플레이어 대역이다."""
    version = "player-random-v0"

    def choose(self, view: PlayerView, rng: random.Random) -> Command:
        """시드로 후보를 선택하며 행동할 수 없으면 명시적으로 실패한다."""
        choices = legal_commands(view)
        if not choices:
            raise ValueError("no legal player command")
        return rng.choice(choices)


class LogicBot(RandomBot):
    """공개 모순·기색·자신의 점괘로 우선순위를 정한다. 점수는 학습 확률이 아니다."""
    version = "player-logic-v0"

    def _scores(self, view):
        scores = {npc: 0 for npc in sorted(set(view.active) - {PLAYER_ID})}
        for target, _ in _conflicts(view):
            scores[target] += 2
        claims = {c.claim_id: c for c in view.public.claims}
        for tell in view.public.tells:
            claim = claims.get(tell.claim_id)
            if tell.shown and claim and claim.speaker in scores:
                scores[claim.speaker] += 1
        for results in view.player.divinations.values():
            for target, result in results.items():
                if target in scores:
                    scores[target] += 1 if result is DivinationResult.MURKY else -100
        for confession in view.public.confessions:
            if confession.npc_id in scores:
                scores[confession.npc_id] = -100
        return scores

    def choose(self, view: PlayerView, rng: random.Random) -> Command:
        """자신의 점괘를 공개하고 근거가 많은 대상에 대한 질문·의심·투표를 우선한다."""
        choices = legal_commands(view)
        if not choices:
            raise ValueError("no legal player command")
        scores = self._scores(view)
        for kind in (CommandKind.REVEAL_DIVINATION, CommandKind.INTERROGATE, CommandKind.SUSPECT,
                     CommandKind.VOTE, CommandKind.DIVINE, CommandKind.ADVANCE, CommandKind.SILENCE):
            group = [c for c in choices if c.kind is kind]
            if not group:
                continue
            if kind is CommandKind.REVEAL_DIVINATION:
                latest = max(c.divination.night for c in group)
                return rng.choice([c for c in group if c.divination.night == latest])
            if kind is CommandKind.INTERROGATE:
                group = [c for c in group if c.interrogation_kind is InterrogationKind.WHEREABOUTS
                         and c.night == view.day - 1 and c.slot is Slot.MIDNIGHT] or group
            top = max(scores.get(c.target or c.addressee, 0) for c in group)
            return rng.choice([c for c in group if scores.get(c.target or c.addressee, 0) == top])
        return rng.choice(choices)


class RepeatSuspectBot(RandomBot):
    """같은 활동 인물에 대한 의심을 반복한다. 대상이 퇴장하면 다시 선택한다."""
    version = "player-repeat-v0"

    def __init__(self):
        self.target = None

    def choose(self, view: PlayerView, rng: random.Random) -> Command:
        """반복 대상에게 의심·투표·질문·점을 향하고 강제 단계는 합법적으로 진행한다."""
        choices = legal_commands(view)
        if not choices:
            raise ValueError("no legal player command")
        npcs = sorted(set(view.active) - {PLAYER_ID})
        if self.target not in npcs and npcs:
            self.target = rng.choice(npcs)
        for kind in (CommandKind.SUSPECT, CommandKind.VOTE, CommandKind.INTERROGATE, CommandKind.DIVINE):
            group = [c for c in choices if c.kind is kind]
            preferred = [c for c in group if (c.target or c.addressee) == self.target]
            if preferred or group:
                return rng.choice(preferred or group)
        return rng.choice(choices)


class LiePressureBot(LogicBot):
    """공개된 모순을 먼저, 기색이 나온 주장을 다음으로 캐묻는다. 진실 라벨은 읽지 않는다."""
    version = "player-lie-pressure-v0"

    def choose(self, view: PlayerView, rng: random.Random) -> Command:
        """모순 질문·지적을 우선하고 그런 근거가 없으면 논리 우선 규칙을 쓴다."""
        choices = legal_commands(view)
        for kind in (CommandKind.INTERROGATE, CommandKind.POINT_CONTRADICTION):
            group = [c for c in choices if c.kind is kind and c.evidence
                     and c.evidence[0].type is EvidenceType.CONTRADICTION]
            if group:
                return rng.choice(group)
        claims = {c.claim_id: c for c in view.public.claims}
        for tell in sorted(view.public.tells, key=lambda t: (t.day, t.claim_id), reverse=True):
            claim = claims.get(tell.claim_id)
            if tell.shown and claim:
                group = [c for c in choices if c.kind is CommandKind.INTERROGATE
                    and c.addressee == claim.speaker and c.interrogation_kind is InterrogationKind.WHEREABOUTS
                    and c.night == claim.night and c.slot is claim.slot]
                if group:
                    return rng.choice(group)
        return super().choose(view, rng)


BOT_TYPES = {"random": RandomBot, "logic": LogicBot, "repeat": RepeatSuspectBot, "lie_pressure": LiePressureBot}
