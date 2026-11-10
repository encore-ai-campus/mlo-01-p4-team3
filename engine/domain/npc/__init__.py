"""도메인(NPC 결정 규칙) · A. beliefs.py, policy_villager.py, policy_possessed.py [A-07-01], vote.py [A-07-02].
표준 라이브러리만 쓰고 다른 계층을 import하지 않는다.

`RuleV0Policy`(정책 버전 `rule-v0`)는 관찰자 역할(자기 개인 지식 ③)로 마을 주민 규칙과 씐 자 전략을 나눠 부른다.
엔진(`PaldoEngine(RuleV0Policy(content))`)과 실행 도구(`--policy rule-v0`)가 이 클래스를 끼운다.
"""
from __future__ import annotations

import random
from typing import Optional

from ..content import ContentBundle
from ..model import Beliefs, Command, ObservationSnapshot, Role
from ..observation import Decision
from .policy_possessed import MixSource, PossessedPolicy, StrategyMix, UniformMix
from .policy_villager import VillagerPolicy
from .vote import VoteOutcome

__all__ = ["RuleV0Policy", "VillagerPolicy", "PossessedPolicy", "StrategyMix", "MixSource", "UniformMix", "VoteOutcome"]


class RuleV0Policy:
    """NPC 규칙 v0. 씐 자 관찰자는 `PossessedPolicy`, 그 밖(마을 주민)은 `VillagerPolicy`로 보낸다.

    content는 인물 성향·동선(공개 콘텐츠)을 찾는 데만 쓴다. mix_source는 씐 자 전략 혼합 비율 공급자(D-09-02 밴딧).
    """

    version = "rule-v0"

    def __init__(self, content: Optional[ContentBundle] = None, mix_source: Optional[MixSource] = None,
                 q_max: Optional[float] = None):
        self.villager = VillagerPolicy(content, q_max)
        self.possessed = PossessedPolicy(content, mix_source)

    def _for(self, obs: ObservationSnapshot):
        return self.possessed if obs.private.role is Role.POSSESSED else self.villager

    def update_beliefs(self, obs: ObservationSnapshot) -> Beliefs:
        """믿음 갱신(④). 엔진이 결정 직전 관측 뒤에 부른다."""
        return self._for(obs).update_beliefs(obs)

    def talk(self, obs: ObservationSnapshot, rng: random.Random) -> Decision:
        """회의 말 행동."""
        return self._for(obs).talk(obs, rng)

    def vote(self, obs: ObservationSnapshot, candidates: list[str], rng: random.Random) -> Decision:
        """투표·결선 대상."""
        return self._for(obs).vote(obs, candidates, rng)

    def vote_with_model(self, obs: ObservationSnapshot, candidates: list[str], rng: random.Random,
                        model, clock=None) -> VoteOutcome:
        """의심 모델을 쓰는 투표(A-07-02). 마을 주민만 모델을 부르고, 씐 자는 자기 전략 투표를 그대로 쓴다."""
        if obs.private.role is Role.POSSESSED:
            return VoteOutcome(self.possessed.vote(obs, candidates, rng))
        return self.villager.vote_with_model(obs, candidates, rng, model, clock)

    def night_plan(self, obs: ObservationSnapshot, executors: list[str], victims: list[str],
                   rng: random.Random) -> Decision:
        """씐 자 공동 밤 명령(관찰자는 씐 자)."""
        return self.possessed.night_plan(obs, executors, victims, rng)

    def react(self, obs: ObservationSnapshot, cmd: Command, rng: random.Random) -> Decision:
        """캐묻기 답변 의도."""
        return self._for(obs).react(obs, cmd, rng)
