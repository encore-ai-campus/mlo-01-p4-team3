"""실제 엔진의 거짓말 전략을 고정해 학습 미사용 전략 평가 입력을 만든다."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from engine.domain.engine import PaldoEngine
from engine.domain.npc import RuleV0Policy, StrategyMix
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from ml.suspicion import features
from tools.jsonlog import log
from tools.run_engine_games import JsonlEventSink, run_game


class FixedLieMix:
    """거짓 행적 유형만 고정하고 나머지 전략 축은 기본 혼합을 유지한다."""

    def __init__(self, lie_type: str):
        if lie_type not in ('unrefuted', 'routine'):
            raise ValueError('거짓말 전략은 unrefuted 또는 routine')
        self.lie_type = lie_type

    def mix_for(self, game_id: str) -> StrategyMix:
        """모든 판에 동일한 전략 비율을 주입한다."""
        return StrategyMix(lie_type={self.lie_type: 1.0}, mix_id='fixed-lie-' + self.lie_type)


def generate(out: Path, games: int, seed: int, lie_type: str) -> dict:
    """실제 bot 판과 TrainingRow·전략 선택 횟수를 새 경로에 보존한다."""
    mix = FixedLieMix(lie_type)
    if out.exists() or not 0 < games < 1_000_000 or seed < 0:
        raise ValueError('새 출력 경로·양수 판 수·음수가 아닌 seed 필요')
    content = FixtureContentSource().load('*')
    policy = RuleV0Policy(content, mix)
    engine = PaldoEngine(policy)
    deps = EngineDeps(content, FakeSuspicionModel())
    out.mkdir(parents=True)
    events_path = out / 'events.jsonl'
    log('suspicion_strategies', 'info', '전략 고정 판 생성 시작', games=games, seed=seed, strategy=lie_type)
    sink = JsonlEventSink(events_path)
    try:
        for i in range(games):
            run_game(engine, deps, 'base6', seed, i, policy.version, sink)
    finally:
        sink.close()
    features.main(['--events', str(events_path), '--out', str(out / 'data')])
    counts = Counter()
    with events_path.open(encoding='utf-8') as f:
        for line in f:
            event = json.loads(line)
            if event['event_type'] == 'decision':
                counts.update(event['payload']['reason_codes'])
    manifest_path = out / 'data' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest.update(strategy_lie_type=lie_type, strategy_mix=asdict(mix.mix_for('')),
                    strategy_reason_counts=dict(sorted(counts.items())))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    log('suspicion_strategies', 'info', '전략 고정 판 생성 완료', games=games, seed=seed,
        strategy=lie_type, lie_unrefuted=counts['lie_unrefuted'], lie_routine=counts['lie_routine'])
    return manifest


def main(argv=None) -> int:
    """전략 고정 데이터 생성 CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--games', type=int, default=300)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--lie-type', choices=('unrefuted', 'routine'), required=True)
    args = parser.parse_args(argv)
    try:
        generate(args.out, args.games, args.seed, args.lie_type)
    except Exception as exc:
        log('suspicion_strategies', 'error', '전략 데이터 생성 실패', error_type=type(exc).__name__, error=str(exc))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
