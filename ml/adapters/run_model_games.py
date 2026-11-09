"""모델 v0 판 실행. D-07-02 통합 뒤 --model 또는 PALDO_SUSPICION_MODEL_PATH로 선택한다.

python -m ml.adapters.run_model_games --preset base6 --games 50 --seed 1
기존 tools.run_engine_games와 같은 실행 인자에 --model(신뢰된 로컬 pickle)을 더한다.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import time

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import CONTRACT_VERSION, PRESETS
from engine.ports import EngineDeps, ModelUnavailable
from engine.testing.fakes import FixtureContentSource
from ml.adapters.suspicion_model import load_model
from tools import run_engine_games as runner
from tools.evidence_result import write_result
from tools.gitrepo import GitRepo
from tools.jsonlog import log
from tools.perf.engine_bench import percentile
from tools.perf.npc_rule_check import check_events


def main(argv=None) -> int:
    """판·모델 버전을 고정하고 종료·합법성·폴백·추론 지연을 함께 기록한다."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--preset', choices=sorted(PRESETS), default='base6')
    parser.add_argument('--games', type=int, default=200)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--out', default='out/model_games')
    parser.add_argument('--policy', choices=sorted(runner.POLICIES), default='rule-v0')
    parser.add_argument('--record', metavar='TASK_ID')
    parser.add_argument('--model', default=os.environ.get('PALDO_SUSPICION_MODEL_PATH',
                                                       'out/suspicion/run1/models.pkl'))
    args = parser.parse_args(argv)
    if not 0 < args.games < runner.SEED_STRIDE or args.seed < 0:
        parser.error('games는 양수·SEED_STRIDE 미만, seed는 0 이상')
    command = shlex.join(['python', '-m', 'ml.adapters.run_model_games', '--preset', args.preset,
                          '--games', str(args.games), '--seed', str(args.seed), '--out', args.out,
                          '--policy', args.policy, '--model', args.model])
    model = None
    try:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / runner.MANIFEST_FILE).unlink(missing_ok=True)
        model = load_model(Path(args.model))
        if model.model_version == 'unavailable':
            raise ModelUnavailable('모델/특징 코드/패키지 인계 필요; 모델 실행을 시작하지 않음')
        content = FixtureContentSource().load('*')
        deps = EngineDeps(content, model)
        policy = runner.POLICIES[args.policy]()
        engine = PaldoEngine(policy)
        sink = runner.JsonlEventSink(out / runner.EVENTS_FILE)
        log('run_model_games', 'info', '모델 판 실행 시작', model_version=model.model_version,
            feature_version=model.feature_version, games=args.games, preset=args.preset)
        started = time.perf_counter()
        try:
            runs = [runner.run_game(engine, deps, args.preset, args.seed, i, policy.version, sink,
                                    model_version=model.model_version) for i in range(args.games)]
        finally:
            sink.close()
        elapsed = time.perf_counter() - started
        checks = check_events(out / runner.EVENTS_FILE)
        metrics = runner.summarize(runs, elapsed)
        metrics.update({key: {'value': checks[key], 'unit': '건'} for key in
                        ('legal_violations', 'inference_violations', 'suspicion_fallbacks')})
        metrics['model_calls'] = {'value': model.calls, 'unit': '건'}
        metrics['model_successes'] = {'value': model.successes, 'unit': '건'}
        metrics['inference_p95'] = {'value': percentile(model.latencies_ms, 95) if model.calls else 0,
                                    'unit': 'ms'}
        passed = (model.successes > 0 and model.successes == model.calls
                  and not checks['legal_violations'] and not checks['inference_violations']
                  and not checks['suspicion_fallbacks']
                  and metrics['inference_p95']['value'] <= 100)
        manifest = dict(command=command, status='passed' if passed else 'failed', games=len(runs),
                        preset_id=args.preset, seed=args.seed, policy=args.policy,
                        npc_policy_version=policy.version, events=sink.count, source='bot',
                        model_versions={'suspicion': model.model_version}, feature_version=model.feature_version,
                        rule_version=params.RULE_PARAMS_VERSION, contract_version=CONTRACT_VERSION,
                        engine_version=runner.ENGINE_VERSION, code_commit=GitRepo().head(),
                        game_ids=[run.game_id for run in runs], metrics=metrics)
        (out / runner.MANIFEST_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if args.record:
            write_result(args.record, 'model_games', metrics, command)
        log('run_model_games', 'info' if passed else 'warning', '모델 판 실행 결과',
            model_version=model.model_version, status=manifest['status'],
            **{key: value['value'] for key, value in metrics.items()})
        return 0 if passed else 1
    except Exception as exc:
        log('run_model_games', 'error', '모델 판 실행 실패', error_type=type(exc).__name__, error=str(exc))
        return 1
    finally:
        if model is not None:
            model.close()


if __name__ == '__main__':
    raise SystemExit(main())
