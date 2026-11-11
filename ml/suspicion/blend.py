"""의심 점수 섞기와 섞는 비율 선택 규칙(도메인, 표준 라이브러리만).

최종 로짓 = α × (모델 로짓 / 온도) + (1 − α) × 규칙 점수(rule_score = 규칙 의심도 / 25).
α=1은 기존 모델 그대로, α=0은 규칙 의심도(FakeSuspicionModel·rule-v0 기준선)와 같은 확률이다.
관문(ml/gate.py)은 바꾸지 않고, 재학습이 관문 benchmark 조건을 만족하는 α 중 가장 나은 것을 고른다.
"""
from __future__ import annotations

import math

ALPHA_GRID = tuple(i / 10 for i in range(11))
# 유효 후보 기준(11/11 PM 결정): α가 작으면 사실상 규칙 점수라 관문을 저절로 통과하므로 승격 대상에서 뺀다.
MIN_EFFECTIVE_ALPHA = 0.3
MIN_LOG_LOSS_GAIN = 0.01  # 규칙 점수(α=0) 대비 benchmark log_loss 개선폭 하한
_EPS = 1e-9


def check_alpha(alpha) -> float:
    """α는 0~1 유한 실수다(모델 파일에 들어가는 값이라 엄격히 검사)."""
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError('blend_alpha must be a finite number in [0, 1]')
    return float(alpha)


def blend(model_logit: float, rule_score: float, alpha: float) -> float:
    """후보 하나의 최종 로짓. α=1이면 모델 로짓을 그대로 돌려준다(기존 모델과 같은 값)."""
    alpha = check_alpha(alpha)
    if alpha == 1.0:
        return float(model_logit)
    return alpha * float(model_logit) + (1.0 - alpha) * float(rule_score)


def select_alpha(trials: list[dict]):
    """관문 benchmark 사유가 없는 α 중 log_loss가 가장 낮은 것을 고른다. 같으면 큰 α(모델 쪽).

    trials 항목: {'alpha': float, 'reasons': [관문 benchmark 사유], 'log_loss': float}.
    통과하는 α가 없으면 None. 입력 순서와 무관하게 같은 답(결정적).
    """
    passing = [t for t in trials if not t['reasons'] and math.isfinite(t['log_loss'])]
    if not passing:
        return None
    best = min(passing, key=lambda t: (t['log_loss'], -check_alpha(t['alpha'])))
    return best['alpha']


def effectiveness(alpha, log_loss, rule_log_loss) -> list[str]:
    """고른 α가 승격할 만한 '유효 후보'인지. 사유가 없으면 유효.

    alpha_too_small: α < 0.3. no_meaningful_gain: 규칙 점수 대비 log_loss 개선이 0.01 미만(또는 값이 유한하지 않음).
    no_passing_alpha: 관문 benchmark 조건을 만족하는 α가 없음.
    """
    if alpha is None:
        return ['no_passing_alpha']
    reasons = []
    if check_alpha(alpha) < MIN_EFFECTIVE_ALPHA - _EPS:
        reasons.append('alpha_too_small')
    gain = float(rule_log_loss) - float(log_loss)
    if not math.isfinite(gain) or gain < MIN_LOG_LOSS_GAIN - _EPS:
        reasons.append('no_meaningful_gain')
    return reasons
