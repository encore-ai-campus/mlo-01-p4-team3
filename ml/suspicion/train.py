"""D-07-02 로컬 첫 학습·평가. Delta export도 같은 TrainingRow+manifest 경계로 받는다.

python -m ml.suspicion.train --rows out/suspicion/data/rows.jsonl --out out/suspicion/run1 --seed 7 --record D-07-02
모델 승격·엔진 연결·VM 검증은 수행하지 않는다.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import pickle
import re
import shlex
import sys
import time
from pathlib import Path

from ml.suspicion.features import FEATURE_NAMES, FEATURE_VERSION, load_rows, sha256
from ml.suspicion.evaluate import evaluate_scores, split_games
from tools.evidence_result import write_result
from tools.gitrepo import GitRepo
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[2]


def load_dataset(rows_path: Path, manifest_path: Path) -> tuple[list, dict]:
    """C 경계: 행 해시·버전·완료 마을 필터 선언이 없거나 다르면 거부한다."""
    m = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (m.get('rows_sha256') != sha256(rows_path) or m.get('feature_version') != FEATURE_VERSION
            or not isinstance(m.get('npc_policy_version'), str) or not m['npc_policy_version']
            or not isinstance(m.get('data_version'), str)
            or not m['data_version'].startswith(('engine-jsonl:', 'delta:'))
            or not isinstance(m.get('input_sha256'), str)
            or not re.fullmatch('[a-f0-9]{64}', m['input_sha256'])
            or m.get('observer_filter') != 'completed_truth_villager'
            or m.get('candidate_filter') != 'snapshot_excludes_mudang'
            or not isinstance(m.get('seeds'), list) or not m['seeds']):
        raise ValueError('manifest 해시/버전/필터/정책/seed 불일치')
    return load_rows(rows_path), m


def _matrix(rows):
    import numpy as np
    return np.asarray([[r.features[n] for n in FEATURE_NAMES] for r in rows], dtype=float)


def validate_heldout(rows: list, manifest: dict, heldout_rows: list, heldout_manifest: dict) -> None:
    """판·거짓말 전략을 분리하고 정책·규칙·콘텐츠는 동일하게 유지한다."""
    if {r.game_id for r in rows} & {r.game_id for r in heldout_rows}:
        raise ValueError('학습 입력과 별도 평가의 게임 중복')
    training_seeds = manifest.get('seeds', [])
    heldout_seeds = heldout_manifest.get('seeds', [])
    if not training_seeds or not heldout_seeds or set(training_seeds) & set(heldout_seeds):
        raise ValueError('학습 입력과 별도 평가의 seed 누락 또는 중복')
    for key in ('npc_policy_version', 'rule_versions', 'content_versions'):
        if not manifest.get(key) or manifest[key] != heldout_manifest.get(key):
            raise ValueError('별도 평가의 정책/규칙/콘텐츠 버전 불일치')
    train_strategy = manifest.get('strategy_lie_type')
    test_strategy = heldout_manifest.get('strategy_lie_type')
    if {train_strategy, test_strategy} != {'unrefuted', 'routine'}:
        raise ValueError('학습에 쓰지 않은 거짓말 전략 필요')
    for metadata, strategy in ((manifest, train_strategy), (heldout_manifest, test_strategy)):
        counts = metadata.get('strategy_reason_counts', {})
        other = 'routine' if strategy == 'unrefuted' else 'unrefuted'
        if counts.get('lie_' + strategy, 0) <= 0 or counts.get('lie_' + other, 0) != 0:
            raise ValueError('고정 전략의 실제 선택 증거가 없거나 다른 전략이 섞임')


def fit_models(rows: list, split: dict, seed: int) -> dict:
    """train 게임 행으로만 로지스틱 회귀와 LightGBM을 학습한다(CPU 1개)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from lightgbm import LGBMClassifier
    from threadpoolctl import threadpool_limits
    train = [r for r in rows if split[r.game_id] == 'train']
    if {r.label for r in train} != {0, 1}:
        raise ValueError('train에 양쪽 라벨 필요')
    x, y = _matrix(train), [r.label for r in train]
    models = {
        'logistic': make_pipeline(StandardScaler(), LogisticRegression(max_iter=500, random_state=seed)),
        'lightgbm': LGBMClassifier(n_estimators=80, num_leaves=7, max_depth=3,
                                  learning_rate=.05, min_child_samples=30, reg_lambda=1,
                                  n_jobs=1, random_state=seed, deterministic=True,
                                  force_col_wise=True, verbosity=-1),
    }
    with threadpool_limits(limits=1):
        for model in models.values():
            model.fit(x, y)
    return models


def model_scores(model, rows: list) -> list[float]:
    """softmax 이전 원시 로짓을 반환한다. LGBM은 binary raw_score를 쓴다."""
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        x = _matrix(rows)
        score = model.decision_function(x) if hasattr(model, 'decision_function') else model.booster_.predict(x, raw_score=True, num_threads=1)
    return [float(v) for v in score]


def run(rows_path: Path, manifest_path: Path, out: Path, seed: int, record: str | None = None,
        heldout_path: Path | None = None, heldout_manifest_path: Path | None = None,
        result_name: str = 'suspicion') -> dict:
    """학습→dev 보정→고정 test 비교, 모델·분할·메타데이터를 로컬 MLflow에 보관한다."""
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'
    import mlflow
    if out.exists():
        raise ValueError('실행 산출물 보존을 위해 새 --out 경로를 사용하세요')
    rows, manifest = load_dataset(rows_path, manifest_path)
    heldout_rows, heldout_manifest = [], None
    if heldout_path is not None:
        heldout_manifest_path = heldout_manifest_path or heldout_path.parent / 'manifest.json'
        heldout_rows, heldout_manifest = load_dataset(heldout_path, heldout_manifest_path)
        validate_heldout(rows, manifest, heldout_rows, heldout_manifest)
    split = split_games(rows, seed)
    parts = {s: [r for r in rows if split[r.game_id] == s] for s in ('train', 'dev', 'test')}
    commit = GitRepo(ROOT).head()
    # mlruns 안에 SQLite 추적 DB와 아티팩트를 함께 둔다(외부 서버 불필요).
    tracking = ROOT / 'mlruns'
    tracking.mkdir(exist_ok=True)
    mlflow.set_tracking_uri('sqlite:///' + (tracking / 'suspicion.db').as_posix())
    client = mlflow.MlflowClient()
    experiment_name = 'D-07-02-local-suspicion'
    exp = client.get_experiment_by_name(experiment_name)
    exp_id = exp.experiment_id if exp else client.create_experiment(experiment_name, artifact_location=(tracking / 'suspicion-artifacts').as_uri())
    out.mkdir(parents=True)
    command = 'python -m ml.suspicion.train --rows ' + shlex.quote(str(rows_path)) + ' --manifest ' + shlex.quote(str(manifest_path)) + ' --out ' + shlex.quote(str(out)) + f' --seed {seed}'
    if record:
        command += ' --record ' + record
    if heldout_path is not None:
        command += ' --heldout-rows ' + shlex.quote(str(heldout_path)) + ' --heldout-manifest ' + shlex.quote(str(heldout_manifest_path))
    if result_name != 'suspicion':
        command += ' --result-name ' + result_name
    tags = {k: str(manifest[k]) for k in ('data_version', 'input_sha256', 'rows_sha256', 'npc_policy_version')}
    tags.update(feature_version=FEATURE_VERSION, code_commit=commit, seed=str(seed),
                status='local_predevelopment', delta_final_evaluation='pending', vm_validation='pending')
    if heldout_manifest is not None:
        tags.update(heldout_strategy=heldout_manifest['strategy_lie_type'],
                    training_strategy=manifest['strategy_lie_type'],
                    heldout_input_sha256=heldout_manifest['input_sha256'],
                    heldout_rows_sha256=heldout_manifest['rows_sha256'])
    log('suspicion_train', 'info', '학습 시작', games=len(split), rows=len(rows), **tags)
    t0 = time.perf_counter()
    with mlflow.start_run(experiment_id=exp_id, tags=tags) as active:
        models = fit_models(rows, split, seed)
        elapsed = time.perf_counter() - t0
        reports = {}
        temperatures = {}
        from ml.suspicion.evaluate import grouped_predictions, metrics
        for name, model in models.items():
            dev_scores = model_scores(model, parts['dev'])
            def dev_loss(t):
                pairs = list(grouped_predictions(parts['dev'], [v/t for v in dev_scores]).values())
                return metrics([p[0] for p in pairs], [p[1] for p in pairs])['log_loss']
            temp = min((.5, 1., 2., 4.), key=dev_loss)
            temperatures[name] = temp
            reports[name] = evaluate_scores(parts['test'], [s/temp for s in model_scores(model, parts['test'])], seed)
            reports[name]['dev_log_loss'] = dev_loss(temp)
            if heldout_rows:
                reports['heldout_' + name] = evaluate_scores(heldout_rows,
                    [s/temp for s in model_scores(model, heldout_rows)], seed)
                after_night = [row for row in heldout_rows if row.day > 1]
                if after_night:
                    reports['heldout_post_night_' + name] = evaluate_scores(after_night,
                        [s/temp for s in model_scores(model, after_night)], seed)
        reports['rule_v0'] = evaluate_scores(parts['test'], [r.features['rule_score'] for r in parts['test']], seed)
        if heldout_rows:
            reports['heldout_rule_v0'] = evaluate_scores(heldout_rows, [r.features['rule_score'] for r in heldout_rows], seed)
            after_night = [row for row in heldout_rows if row.day > 1]
            if after_night:
                reports['heldout_post_night_rule_v0'] = evaluate_scores(after_night,
                    [r.features['rule_score'] for r in after_night], seed)
        numeric = {f'{name}_{key}': dict(value=value, unit='')
                   for name, report in reports.items() for key, value in report['metrics'].items()}
        numeric.update(games=dict(value=len(split), unit='판'), rows=dict(value=len(rows), unit='행'),
                       train_seconds=dict(value=elapsed, unit='s'))
        for part, rs in parts.items():
            numeric[part + '_games'] = dict(value=len({r.game_id for r in rs}), unit='판')
        for key, value in manifest.get('audit', {}).items():
            numeric['input_' + key] = dict(value=value, unit='건')
        if heldout_manifest is not None:
            for key, value in heldout_manifest.get('audit', {}).items():
                numeric['heldout_input_' + key] = dict(value=value, unit='건')
        for name, report in reports.items():
            for key, bounds in report.get('bootstrap_95', {}).items():
                for suffix, value in zip(('ci_low', 'ci_high'), bounds):
                    numeric[f'{name}_{key}_{suffix}'] = dict(value=value, unit='')
        result = dict(status='local_predevelopment', run_id=active.info.run_id,
                      command=command, code_commit=commit, feature_version=FEATURE_VERSION,
                      manifest=manifest, heldout_manifest=heldout_manifest,
                      strategy_evaluation_verified=bool(heldout_rows),
                      split=split, temperatures=temperatures, reports=reports,
                      train_seconds=elapsed, python=sys.version,
                      versions={n: importlib.metadata.version(n) for n in ('numpy', 'scikit-learn', 'lightgbm', 'mlflow')},
                      limitations=['C Delta 최종 평가 대기', 'VM Python 3.14 네 패키지 import 대기',
                                   '다른 프리셋/사람 데이터 검증 및 엔진 모델 행동 평가는 미실행'],
                      improvement_claim=False)
        if not heldout_rows:
            result['limitations'].append('미사용 씐 자 전략 평가 없음')
        (out / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with (out / 'models.pkl').open('wb') as f:
            pickle.dump(dict(models=models, temperatures=temperatures, feature_version=FEATURE_VERSION,
                             feature_names=FEATURE_NAMES, code_commit=commit), f)
        mlflow.log_params(dict(seed=seed, cpu_threads=1, feature_version=FEATURE_VERSION,
                              logistic_max_iter=500, lgbm_estimators=80, lgbm_leaves=7,
                              temperature_grid='.5,1,2,4', **{k+'_temperature': v for k, v in temperatures.items()}))
        mlflow.log_metrics({k: v['value'] for k, v in numeric.items()})
        mlflow.log_artifacts(str(out), artifact_path='local_predevelopment')
        if record:
            path = write_result(record, result_name, numeric, command)
            mlflow.log_artifact(str(path))
    log('suspicion_train', 'info', '학습 평가 완료', run_id=result['run_id'], duration_ms=round((time.perf_counter()-t0)*1000, 3),
        train_seconds=elapsed, reports={k: v['metrics'] for k, v in reports.items()})
    return result


def main(argv=None) -> int:
    """학습 CLI. 실패를 구조화 로그에 기록하고 예외를 보존한다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--rows', type=Path, required=True)
    ap.add_argument('--manifest', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--record')
    ap.add_argument('--heldout-rows', type=Path)
    ap.add_argument('--heldout-manifest', type=Path)
    ap.add_argument('--result-name', default='suspicion')
    a = ap.parse_args(argv)
    try:
        if a.heldout_manifest is not None and a.heldout_rows is None:
            raise ValueError('--heldout-manifest는 --heldout-rows와 함께 사용')
        run(a.rows, a.manifest or a.rows.parent / 'manifest.json', a.out, a.seed, a.record,
            a.heldout_rows, a.heldout_manifest, a.result_name)
    except Exception as exc:
        log('suspicion_train', 'error', '학습 실패', error_type=type(exc).__name__, error=str(exc))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
