"""Pure lifecycle §2 decisions. Rejection preserves the current/rule version.

Small samples compare primary quality bootstrap 95% intervals (loss/exact match);
touching counts as overlap. Reports must use the same frozen evaluation set.
Registration/canary promotion belongs to the lifecycle executor, not this module.
"""
from dataclasses import dataclass
import math

INTENT_SLOTS = ('kind', 'addressee', 'target', 'evidence', 'night', 'slot',
                'divination', 'interrogation_kind')


@dataclass(frozen=True)
class GateDecision:
    """Acceptance plus registry-ready reasons and the unchanged serving version."""
    accepted: bool
    reasons: tuple[str, ...]
    reference_version: str
    serving_version: str


def _number(report, name, upper=1):
    value = report[name]
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
        raise ValueError(name)
    return value


def _count(report, name):
    value = report[name]
    if type(value) is not int or value < 1:
        raise ValueError(name)
    return value


def _interval(value, point, upper=1):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('missing bootstrap 95% interval')
    lo, hi = (_number({'x': v}, 'x', upper) for v in value)
    if not lo <= point <= hi:
        raise ValueError('invalid bootstrap 95% interval')
    return lo, hi


def _decision(reasons, current, fallback):
    version = current or fallback
    return GateDecision(not reasons, tuple(reasons), version, version)


def suspicion_gate(candidate, reference, *, current_version=None):
    """Require benchmark AND human: loss +.01, ECE <=.05, win rate ±.05.

    Each split supplies games/log_loss/ece/village_win_rate. Below 50 games,
    both bootstrap_95.log_loss intervals must show strictly lower loss.
    With no production version the caller supplies rule-v0 measurements.
    """
    reasons = []
    for split in ('benchmark', 'human'):
        try:
            c, r = candidate[split], reference[split]
            n, rn = _count(c, 'games'), _count(r, 'games')
            if n != rn:
                raise ValueError('different evaluation sizes')
            loss, previous = _number(c, 'log_loss', math.inf), _number(r, 'log_loss', math.inf)
            if loss > previous + .01 + 1e-12:
                reasons.append(f'{split}:log_loss')
            if _number(c, 'ece') > .05:
                reasons.append(f'{split}:ece')
            _number(r, 'ece')
            if abs(_number(c, 'village_win_rate') - _number(r, 'village_win_rate')) > .05 + 1e-12:
                reasons.append(f'{split}:village_win_rate')
            if n < 50:
                ci = _interval(c['bootstrap_95']['log_loss'], loss, math.inf)
                ri = _interval(r['bootstrap_95']['log_loss'], previous, math.inf)
                if ci[1] >= ri[0]:
                    reasons.append(f'{split}:not_improved_ci95')
        except (KeyError, TypeError, ValueError):
            reasons.append(f'{split}:invalid_or_missing_measurement')
    return _decision(reasons, current_version, 'rule-v0')


def intent_gate(candidate, reference, *, current_version=None):
    """Require test_human exact/F1/contrast nonregression, unsupported <= and slots +.02.

    Reports supply dataset, rows, the four metrics and slot_errors (all eight
    Command fields). Below 200 sentences, command_exact_match_ci95 is required
    for both reports and candidate.lower must exceed reference.upper.
    """
    reasons = []
    try:
        if candidate['dataset'] != 'test_human' or reference['dataset'] != 'test_human':
            raise ValueError('test_human required')
        n = _count(candidate, 'rows')
        if n != _count(reference, 'rows'):
            raise ValueError('different evaluation sizes')
        for metric in ('command_exact_match', 'macro_f1', 'contrast_accuracy'):
            if _number(candidate, metric) < _number(reference, metric):
                reasons.append(metric)
        if _number(candidate, 'unsupported_false_positive_rate') > _number(reference, 'unsupported_false_positive_rate'):
            reasons.append('unsupported_false_positive_rate')
        for slot in INTENT_SLOTS:
            if _number(candidate['slot_errors'], slot) > _number(reference['slot_errors'], slot) + .02 + 1e-12:
                reasons.append('slot_' + slot)
        if n < 200:
            ci = _interval(candidate['command_exact_match_ci95'], candidate['command_exact_match'])
            ri = _interval(reference['command_exact_match_ci95'], reference['command_exact_match'])
            if ci[0] <= ri[1]:
                reasons.append('not_improved_ci95')
    except (KeyError, TypeError, ValueError):
        reasons.append('invalid_or_missing_measurement')
    return _decision(reasons, current_version, 'rule-baseline')
