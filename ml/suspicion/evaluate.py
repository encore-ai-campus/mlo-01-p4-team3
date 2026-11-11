"""창귀 1명 관측별 softmax와 게임 단위 평가(행동 실행 평가는 D-08-01)."""
from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict

from contracts.datasets import TrainingRow


def softmax(scores: list[float]) -> list[float]:
    """창귀 1명 확률을 합 1로 정규화한다(큰 로짓에도 안정적)."""
    if not scores or not all(math.isfinite(s) for s in scores):
        raise ValueError('비어 있거나 유한하지 않은 점수')
    peak = max(scores)
    weights = [math.exp(s - peak) for s in scores]
    total = sum(weights)
    return [w / total for w in weights]


def split_games(rows: list[TrainingRow], seed: int) -> dict[str, str]:
    """묶음을 seed로 섞어 70/15/15 분할하고 게임 ID별 분할을 돌려준다. 행 순서에는 불변.

    묶음은 봇 판이면 게임 하나, 사람 판이면 그 사람(가명)의 판 전부다(사람 단위 분할, events.md 3절).
    봇 판만 있으면 묶음 = 게임이라 예전 게임 단위 분할과 결과가 같다.
    """
    groups = defaultdict(set)
    for r in rows:
        groups[('user:' + r.split_key.user) if r.split_key.user else r.game_id].add(r.game_id)
    keys = sorted(groups)
    if len(keys) < 7:
        raise ValueError('게임 분할에 최소 7묶음 필요')
    random.Random(seed).shuffle(keys)
    n_test = max(1, int(len(keys) * .15))
    n_dev = max(1, int(len(keys) * .15))
    result = {}
    for i, key in enumerate(keys):
        part = 'test' if i < n_test else 'dev' if i < n_test + n_dev else 'train'
        for game in groups[key]:
            if result.setdefault(game, part) != part:
                raise ValueError('한 게임이 두 묶음에 속함')
    return result


# 사람 판 분할(D-10-01, docs/contracts/events.md 3절 사람 단위 분할). 고정 문자열이라 실행 seed와 무관하게
# 한 번 배정된 사람은 다음 밤에도 같은 쪽에 남는다. 비율을 바꾸면 배정이 바뀌므로 바꿀 때는 버전을 올린다.
HUMAN_SPLIT_SALT = 'suspicion-human-split-v1'
HUMAN_TEST_RATIO = 0.3


def human_side(user: str, salt: str = HUMAN_SPLIT_SALT, test_ratio: float = HUMAN_TEST_RATIO) -> str:
    """가명 하나를 sha256(salt||가명) 앞 8바이트로 'test' 또는 'train'에 배정한다(사람 단위, 결정적)."""
    if not isinstance(user, str) or not user or not 0 < test_ratio < 1:
        raise ValueError('가명 또는 시험 비율 오류')
    value = int.from_bytes(hashlib.sha256(f'{salt}||{user}'.encode('utf-8')).digest()[:8], 'big') / 2 ** 64
    return 'test' if value < test_ratio else 'train'


def split_human_rows(rows: list[TrainingRow]) -> tuple[list[TrainingRow], list[TrainingRow]]:
    """사람 행을 (학습, 시험)으로 나눈다. 한 사람의 판은 모두 한쪽에만 들어간다."""
    train, test = [], []
    for row in rows:
        if row.source != 'human':
            raise ValueError('사람 행만 분할')
        (test if human_side(row.split_key.user) == 'test' else train).append(row)
    return train, test


def metrics(labels: list[list[int]], probs: list[list[float]]) -> dict[str, float]:
    """관측별 다중분류 로그 손실·top1와 후보별 10-bin ECE를 계산한다."""
    if not labels or len(labels) != len(probs):
        raise ValueError('평가 입력 크기 불일치')
    loss = hits = 0.0
    bins = [[] for _ in range(10)]
    for y, ps in zip(labels, probs):
        if (len(y) != len(ps) or not y or sum(y) != 1 or any(v not in (0, 1) for v in y)
                or not all(math.isfinite(p) and 0 <= p <= 1 for p in ps)
                or not math.isclose(sum(ps), 1.0, abs_tol=1e-9)):
            raise ValueError('라벨 또는 확률이 창귀 1명 계약 위반')
        loss -= math.log(max(ps[y.index(1)], 1e-15))
        hits += y[max(range(len(ps)), key=ps.__getitem__)]
        for label, p in zip(y, ps):
            bins[min(9, int(p * 10))].append((label, p))
    count = sum(map(len, bins))
    ece = sum(abs(sum(y - p for y, p in b)) for b in bins) / count
    return dict(log_loss=loss / len(labels), top1_accuracy=hits / len(labels), ece=ece)


def grouped_predictions(rows: list[TrainingRow], scores: list[float]) -> dict:
    """후보 점수를 스냅샷별 정규화한다. 동일 확률은 후보 ID 순으로 동률 해소."""
    if len(rows) != len(scores):
        raise ValueError('행과 점수 길이 불일치')
    groups = defaultdict(list)
    for row, score in zip(rows, scores):
        groups[(row.game_id, row.snapshot_id)].append((row, float(score)))
    result = {}
    for key, group in sorted(groups.items()):
        group.sort(key=lambda x: x[0].candidate)
        result[key] = ([r.label for r, _ in group], softmax([s for _, s in group]))
    return result


def evaluate_scores(rows: list[TrainingRow], scores: list[float], seed: int) -> dict:
    """평가 지표와 50판 미만일 때 게임 클러스터 bootstrap 95% 구간을 반환한다."""
    predictions = grouped_predictions(rows, scores)
    pairs = list(predictions.values())
    report = dict(metrics=metrics([p[0] for p in pairs], [p[1] for p in pairs]),
                  games=len({r.game_id for r in rows}), snapshots=len(pairs), rows=len(rows))
    if report['games'] < 50:
        games = defaultdict(list)
        for (gid, _), pair in predictions.items():
            games[gid].append(pair)
        gids = sorted(games)
        rng = random.Random(seed)
        samples = defaultdict(list)
        for _ in range(300):
            draw = [p for gid in rng.choices(gids, k=len(gids)) for p in games[gid]]
            for k, v in metrics([p[0] for p in draw], [p[1] for p in draw]).items():
                samples[k].append(v)
        report['bootstrap_95'] = {k: [sorted(v)[7], sorted(v)[292]] for k, v in samples.items()}
    return report
