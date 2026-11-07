"""마을 주민 투표 3층과 의심 모델 포트 연결(A-07-02). 기획서 v1.1 5.2, ports.md 3절.

투표는 세 층으로 정한다.
- 추정: 의심 모델 포트(`SuspicionModel.estimate(obs)`)가 후보별 씐 자 확률과 근거 묶음별 로그 오즈 기여값을 낸다.
  무당 의심도 q = q_max × (100 − trust_in_mudang)/100, 주민 후보의 최종 의심도 = (1 − q) × probs[c]로 결합한다.
- 결정: 성향 가중 확률(기여값에 `Temperament.group_weights`를 곱해 로그 오즈를 고친 확률)
  + 동조(`Temperament.conformity`) × 예상 득표(오늘 그 후보를 공개 의심한 다른 투표자 비율) + 잡음. 최댓값을 고른다.
- 안전: 합법 후보(자기 아님)만 두고 확정 명제(`not_possessed`)로 무고가 확정된 사람과 고백자를 뺀다.
  전부 빠지면 합법 후보 중 추정 의심도 최댓값으로 돌아간다.

실패 처리(ports.md 3): 모델이 `ModelUnavailable`·`FeatureVersionMismatch`를 내거나, 호출 경과 시간이 100ms를 넘거나,
출력이 계약(후보 순서, 확률 범위·합, 기여값 유한)과 맞지 않으면 그 결정만 규칙 v0 투표(`VillagerPolicy.vote`, 믿음의 의심도)로 대신한다.
엔진은 이 결정의 `decision` 이벤트 `policy_version`에 `-fallback`을 붙인다. 시간은 호출 전후 `time.monotonic()`으로 재며,
느린 호출 자체를 끊지는 않는다(중단은 어댑터 D-08-01의 시간 제한 몫, 11/5 PM 결정).

폴백 횟수 지표(`paldo_engine_suspicion_fallback_total`): 엔진은 결과 이벤트에 `-fallback` 결정을 남기기만 한다.
응용 계층(B)은 `TransitionResult.events`를 `fallback_count`로 세어 카운터를 그만큼 올린다.

씐 자의 투표는 자기 역할을 알아 의심 추정이 필요 없으므로 모델을 부르지 않고 A-07-01 전략을 그대로 쓴다.

계층: 도메인. 표준 라이브러리와 engine 패키지(포트 타입)만 사용한다.
"""
from __future__ import annotations

import enum
import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .. import params
from ...ports import FeatureVersionMismatch, ModelUnavailable, SuspicionEstimate, SuspicionModel
from ..content import Temperament
from ..events import DomainEvent, EventType
from ..model import PLAYER_ID, Beliefs, CommandKind, ObservationSnapshot
from ..observation import Decision, ReasonCode
from . import beliefs as bl

SUSPICION_TIMEOUT_S = 0.1     # 엔진 측 시간 초과(ports.md 3, 100ms)
CONFORMITY_SCALE = 0.2        # 동조 항 크기: conformity 1.0·예상 득표 100%일 때 확률 0.2만큼(잠정, A-08-03 조정)
VOTE_NOISE_P = 0.02           # 결정 층 잡음 폭(확률 단위 ±, 잠정)
PROB_EPS = 1e-6               # 로그 오즈 계산에서 0·1을 피하는 경계
PROB_SUM_TOL = 1e-3           # 확률 합(= 남은 창귀 수) 검사 허용 오차
FALLBACK_SUFFIX = "-fallback"
FALLBACK_METRIC = "paldo_engine_suspicion_fallback_total"

Clock = Callable[[], float]


class FallbackReason(str, enum.Enum):
    """의심 모델 대신 규칙 v0로 정한 이유."""
    UNAVAILABLE = "unavailable"    # ModelUnavailable
    VERSION = "version"            # FeatureVersionMismatch 또는 출력의 feature_version이 모델 선언과 다름
    TIMEOUT = "timeout"            # 호출 경과 시간 > 100ms
    INVALID = "invalid"            # 출력이 계약(후보 순서·확률 범위와 합·기여값 유한)과 맞지 않음


FALLBACK_CODE: dict[FallbackReason, ReasonCode] = {
    FallbackReason.UNAVAILABLE: ReasonCode.MODEL_FALLBACK_UNAVAILABLE,
    FallbackReason.VERSION: ReasonCode.MODEL_FALLBACK_VERSION,
    FallbackReason.TIMEOUT: ReasonCode.MODEL_FALLBACK_TIMEOUT,
    FallbackReason.INVALID: ReasonCode.MODEL_FALLBACK_INVALID,
}


@dataclass
class VoteOutcome:
    """모델을 쓰는 투표 결정 결과. fallback이 있으면 엔진이 `policy_version`에 `-fallback`을 붙인다."""
    decision: Decision
    fallback: Optional[FallbackReason] = None


# ---------------------------------------------------------------- 1층: 추정
def estimate(model: SuspicionModel, obs: ObservationSnapshot, clock: Clock = time.monotonic,
             timeout_s: float = SUSPICION_TIMEOUT_S) -> tuple[Optional[SuspicionEstimate], Optional[FallbackReason]]:
    """의심 모델을 한 번 부른다. (추정, None) 또는 (None, 폴백 이유).

    시간은 호출 전후 clock으로 재고, timeout_s를 넘으면 결과를 버린다(호출을 끊지는 않는다).
    """
    t0 = clock()
    try:
        est = model.estimate(obs)
    except ModelUnavailable:
        return None, FallbackReason.UNAVAILABLE
    except FeatureVersionMismatch:
        return None, FallbackReason.VERSION
    if clock() - t0 > timeout_s:
        return None, FallbackReason.TIMEOUT
    if not valid_estimate(est, obs):
        return None, FallbackReason.INVALID
    declared = getattr(model, "feature_version", None)
    if declared is not None and est.feature_version != declared:
        return None, FallbackReason.VERSION
    return est, None


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def valid_estimate(est: SuspicionEstimate, obs: ObservationSnapshot) -> bool:
    """출력이 ports.md 3 계약을 지키는가: 후보가 스냅샷과 같은 순서, 확률은 0~1 유한값이고 합이 남은 창귀 수
    (허용 오차 PROB_SUM_TOL), 기여값은 후보 → 근거 묶음 → 유한값."""
    if not isinstance(est, SuspicionEstimate) or not isinstance(est.probs, dict)             or not isinstance(est.candidates, (list, tuple)):
        return False                                      # None·형식이 다른 출력도 예외 없이 폴백
    if list(est.candidates) != list(obs.candidates) or set(est.probs) != set(obs.candidates):
        return False
    if not all(_finite(p) and 0.0 <= p <= 1.0 for p in est.probs.values()):
        return False
    if abs(sum(est.probs.values()) - obs.n_possessed_remaining) > PROB_SUM_TOL:
        return False
    contrib = est.contributions if isinstance(est.contributions, dict) else None
    if contrib is None or not set(contrib) <= set(obs.candidates):
        return False
    return all(isinstance(g, dict) and all(_finite(v) for v in g.values()) for g in contrib.values())


def mudang_q(trust_in_mudang: float, q_max: float = params.Q_MAX) -> float:
    """무당 의심도 q = q_max × (100 − trust)/100(ports.md 3). trust는 0~100으로 자른다."""
    t = min(100.0, max(0.0, trust_in_mudang))
    return q_max * (100.0 - t) / 100.0


def _logit(p: float) -> float:
    p = min(1.0 - PROB_EPS, max(PROB_EPS, p))
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


# ---------------------------------------------------------------- 2층: 결정
def weighted_probs(est: SuspicionEstimate, temp: Optional[Temperament]) -> dict[str, float]:
    """성향 가중 확률. 후보 c의 로그 오즈에 Σ_g (w_g − 1) × 기여값_g를 더한다(가중치 1.0이면 모델 확률 그대로).

    성향이 없거나 기여값이 없는 묶음은 가중치 1.0으로 본다. 결과는 0~1(합이 남은 창귀 수와 같다는 보장은 없다).
    """
    weights = temp.group_weights if temp is not None else {}
    out = {}
    for c in est.candidates:
        shift = sum((weights.get(g, 1.0) - 1.0) * v for g, v in est.contributions.get(c, {}).items())
        out[c] = _sigmoid(_logit(est.probs[c]) + shift) if shift else est.probs[c]
    return out


def combined_suspicion(probs: dict[str, float], trust_in_mudang: float,
                       q_max: float = params.Q_MAX) -> dict[str, float]:
    """무당 결합: 주민 후보 (1 − q) × p, 무당(`PLAYER_ID`) q."""
    q = mudang_q(trust_in_mudang, q_max)
    out = {c: (1.0 - q) * p for c, p in probs.items()}
    out[PLAYER_ID] = q
    return out


def expected_votes(obs: ObservationSnapshot) -> dict[str, float]:
    """예상 득표: 오늘 회의에서 c를 의심한 다른 투표자 비율. 같은 화자는 한 번만 센다.

    다른 투표자는 오늘 회의에서 말 차례를 쓴 사람(침묵 포함, 활동 NPC는 모두 차례가 온다)과 무당에서 관찰자를 뺀 집합이다.
    """
    voters = ({sp.speaker for sp in obs.public.speeches if sp.day == obs.day} | {PLAYER_ID}) - {obs.observer}
    if not voters:
        return {}
    by_target: dict[str, set[str]] = {}
    for sp in obs.public.speeches:
        if sp.day == obs.day and sp.kind is CommandKind.SUSPECT and sp.target and sp.speaker in voters:
            by_target.setdefault(sp.target, set()).add(sp.speaker)
    return {t: len(s) / len(voters) for t, s in by_target.items()}


def decision_scores(susp: dict[str, float], expected: dict[str, float], conformity: float,
                    rng: random.Random, noise: float = VOTE_NOISE_P) -> dict[str, float]:
    """결정 점수 = 의심도 + CONFORMITY_SCALE × conformity × 예상 득표 + 잡음(±noise). 후보 ID 순으로 잡음을 뽑는다."""
    return {c: susp[c] + CONFORMITY_SCALE * conformity * expected.get(c, 0.0) + rng.uniform(-noise, noise)
            for c in sorted(susp)}


# ---------------------------------------------------------------- 3층: 안전
def safe_pool(obs: ObservationSnapshot, b: Beliefs, candidates: Iterable[str]) -> tuple[list[str], list[ReasonCode]]:
    """합법(자기 아님) 후보에서 확정 명제 무고와 고백자를 뺀다. 남는 사람이 없으면 빈 목록(호출자가 폴백)."""
    legal = [c for c in candidates if c != obs.observer]
    codes: list[ReasonCode] = []
    cleared = bl.cleared_by_proposition(b)
    pool = [c for c in legal if c not in cleared]
    if len(pool) < len(legal):
        codes.append(ReasonCode.EXCLUDE_PROPOSITION)
    allowed = set(obs.candidates) | {PLAYER_ID}
    kept = [c for c in pool if c in allowed]
    if len(kept) < len(pool):
        codes.append(ReasonCode.SKIP_CONFESSED)
    return kept, codes


def _argmax(scores: dict[str, float], rng: random.Random) -> tuple[str, bool]:
    best = max(scores.values())
    tied = sorted(c for c, v in scores.items() if v == best)
    return (rng.choice(tied) if len(tied) > 1 else tied[0]), len(tied) > 1


def choose_target(obs: ObservationSnapshot, b: Beliefs, candidates: list[str], est: SuspicionEstimate,
                  temp: Optional[Temperament], rng: random.Random) -> tuple[str, list[ReasonCode]]:
    """세 층을 차례로 적용해 (대상, 이유 코드)를 낸다. 의심 추정은 이미 성공한 상태(est)라고 본다."""
    legal = [c for c in candidates if c != obs.observer]
    susp = combined_suspicion(weighted_probs(est, temp), b.trust_in_mudang)
    susp = {c: susp.get(c, 0.0) for c in legal}          # 후보에 없는 합법 대상(결선 등)은 0
    expected = expected_votes(obs)
    scores = decision_scores(susp, expected, temp.conformity if temp else 0.0, rng)
    pool, codes = safe_pool(obs, b, legal)
    codes.insert(0, ReasonCode.MODEL_ESTIMATE)
    if not pool:                                          # 전부 빠짐: 합법 후보 중 의심도 최댓값(기획서 5.2 안전 층)
        target, tied = _argmax(susp, rng)
        codes.append(ReasonCode.FALLBACK_MAX_SUSPICION)
    else:
        target, tied = _argmax({c: scores[c] for c in pool}, rng)
        plain = max(sorted(pool), key=lambda c: susp[c])  # 동조·잡음 없이 고른 대상
        if plain != target and expected.get(target, 0.0) > 0:
            codes.append(ReasonCode.VOTE_CONFORM)
    if tied:
        codes.append(ReasonCode.TIE_SEEDED)
    return target, codes


# ---------------------------------------------------------------- 지표
def is_fallback_version(policy_version: str) -> bool:
    """`decision` 이벤트의 policy_version이 의심 모델 폴백 결정인가."""
    return policy_version.endswith(FALLBACK_SUFFIX)


def fallback_count(events: Iterable[DomainEvent]) -> int:
    """이벤트 목록에서 의심 모델 폴백 결정 수. B가 `paldo_engine_suspicion_fallback_total`을 이만큼 올린다."""
    return sum(1 for e in events if e.event_type is EventType.DECISION
               and is_fallback_version(str(e.payload.get("policy_version", ""))))
