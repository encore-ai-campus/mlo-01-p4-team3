"""D-10-01 calibrated bot generation, D-07-02 training and suspicion gate replay.

사람 판(D-10-01, 11/11 사용자 결정): 학습용 내보내기(`ut_export training`)를 받으면 동의·삭제 목록을 적용해
사람 단위로 학습/시험으로 나누고(`evaluate.human_side`), 학습 쪽은 봇 판과 섞어 학습하고 시험 쪽은 관문의 human
지표로 쓴다. 기준(reference)은 지금 운영 모델(`--production-model`), 없을 때만 규칙 점수(rule-v0)다.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import pickle
from time import perf_counter

from engine.domain.engine import PaldoEngine
from engine.domain.model import PRESETS
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource, ListEventSink
from ml.adapters.suspicion_model import load_model
from ml.gate import suspicion_gate
from ml.suspicion import features, train
from engine.domain import serde
from ml.suspicion.blend import ALPHA_GRID, blend, check_alpha, effectiveness, select_alpha
from ml.suspicion.evaluate import evaluate_scores, split_games, split_human_rows
from ml.suspicion.player_calibration import CalibratedPlayerBot, _tombstones_sha256
from pipeline.spark.privacy_filter import load_tombstones
from tools import run_engine_games as runner
from tools.gitrepo import GitRepo
from tools.ut_freeze import DEFAULT_CUTOFF, DEFAULT_OPEN, KST


def _play(preset, games, seed, weight, model, sink, content):
    policy = runner.POLICIES["rule-v0"]()
    engine = PaldoEngine(policy)
    deps = EngineDeps(content, model)
    return [runner.run_game(engine, deps, preset, seed, i, policy.version, sink,
                            model_version=model.model_version, player_bot=CalibratedPlayerBot(weight))
            for i in range(games)]


def _gate_metrics(report, wins):
    return {**report, **report["metrics"], "village_win_rate": wins}


def _village_rate(cohorts, total):
    return sum(g.winner == "village" for cohort in cohorts for g in cohort) / total


def blend_dir(out):
    """α 후보 산출물을 두는 폴더(인프라: 파일)."""
    path = out / "trained" / "blend"
    path.mkdir(exist_ok=True)
    return path


def artifact_scores(artifact, rows, alpha=None):
    """학습 산출물(dict)로 행마다 섞은 로짓을 낸다. alpha가 없으면 산출물의 blend_alpha(없으면 1)."""
    if artifact["feature_version"] != features.FEATURE_VERSION or tuple(artifact["feature_names"]) != features.FEATURE_NAMES:
        raise ValueError("artifact feature contract mismatch")
    temperature = artifact["temperatures"]["logistic"]
    a = check_alpha(artifact.get("blend_alpha", 1.0) if alpha is None else alpha)
    logits = [s / temperature for s in train.model_scores(artifact["models"]["logistic"], rows)]
    return [blend(m, r.features["rule_score"], a) for m, r in zip(logits, rows)]


def load_human(games_path, consent_path, tombstones_dir):
    """학습용 내보내기 + 동의 목록 + 삭제 요청 폴더 → (학습 행, 시험 행, 기록). 입력 처리(응용)."""
    raw = Path(games_path).read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if document.get("kind") != "training" or not isinstance(document.get("games"), list):
        raise ValueError("human games must be a training export")
    consent_raw = Path(consent_path).read_bytes()
    consent = json.loads(consent_raw.decode("utf-8"))
    if not isinstance(consent, list) or any(not isinstance(u, str) or not u for u in consent):
        raise ValueError("invalid consent allowlist")
    before = _tombstones_sha256(tombstones_dir)
    deleted = load_tombstones(tombstones_dir)["players"]
    rows, audit = features.human_rows(document["games"], set(consent), set(deleted))
    if _tombstones_sha256(tombstones_dir) != before:
        raise ValueError("tombstones changed while reading human games")
    fit, test = split_human_rows(rows)
    side = lambda part: {"games": len({r.game_id for r in part}), "users": len({r.split_key.user for r in part}),
                         "rows": len(part)}
    record = dict(export_games=len(document["games"]), export_excluded=document.get("excluded", {}),
                  used_games=audit["used_games"], excluded=audit["excluded"], policy_versions=audit["policy_versions"],
                  train=side(fit), test=side(test), input_sha256=hashlib.sha256(raw).hexdigest(),
                  consent_sha256=hashlib.sha256(consent_raw).hexdigest(), tombstones_sha256=before)
    return dict(train=fit, test=test, results=audit["results"], record=record)


def _with_human_rows(out, rows_path, manifest_path, human_rows):
    """봇 학습 행에 사람 학습 행을 붙인 새 데이터 폴더를 만든다(manifest 해시 갱신, 원본은 그대로)."""
    target = out / "dataset_train"
    target.mkdir()
    combined = target / "rows.jsonl"
    with combined.open("w", encoding="utf-8", newline="\n") as f:
        f.write(rows_path.read_text(encoding="utf-8"))
        for row in human_rows:
            f.write(serde.dumps(row) + "\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(rows_sha256=features.sha256(combined), human_train_rows=len(human_rows),
                    human_train_games=len({r.game_id for r in human_rows}))
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return combined, target / "manifest.json"


def main(argv=None):
    """Train on generated bot games and evaluate both policies on identical seeds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--preset", action="append",
                        choices=[key for key, p in PRESETS.items() if p.n_possessed == 1])
    parser.add_argument("--games-per-preset", type=int, default=450)
    parser.add_argument("--benchmark-games", type=int, default=50)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--human-games", type=Path, help="학습용 내보내기(ut_export training) JSON")
    parser.add_argument("--consented-users", type=Path, help="학습 동의 가명 목록 JSON(--human-games와 함께)")
    parser.add_argument("--tombstones", type=Path, help="삭제 요청 폴더(--human-games와 함께)")
    parser.add_argument("--production-model", type=Path,
                        help="지금 운영 모델 models.pkl(관문 기준). 없으면 규칙 점수(rule-v0)가 기준")
    args = parser.parse_args(argv)
    human_args = (args.human_games, args.consented_users, args.tombstones)
    if any(human_args) and not all(human_args):
        parser.error("--human-games, --consented-users and --tombstones go together")
    if datetime.fromisoformat(DEFAULT_OPEN) <= datetime.now(KST) < datetime.fromisoformat(DEFAULT_CUTOFF):
        parser.error("training and simulation are paused during the formal user test")
    if not 0 < args.games_per_preset < runner.SEED_STRIDE or not 0 < args.benchmark_games < runner.SEED_STRIDE or args.seed < 0:
        parser.error("invalid game counts or seed")
    started = perf_counter()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    weight = calibration["calibrated_weight"]
    bot_version = CalibratedPlayerBot(weight).version
    if calibration["bot_version"] != bot_version or calibration["cutoff"] != DEFAULT_CUTOFF:
        raise ValueError("invalid calibration version or cutoff")
    args.out.mkdir(parents=True, exist_ok=False)
    presets = list(dict.fromkeys(args.preset or ["base6"]))
    content = FixtureContentSource().load("*")
    reference = FakeSuspicionModel()
    sink = runner.JsonlEventSink(args.out / "events.jsonl")
    try:
        for preset in presets:
            _play(preset, args.games_per_preset, args.seed, weight, reference, sink, content)
    finally:
        sink.close()
    features.main(["--events", str(sink.path), "--out", str(args.out / "dataset")])
    human = load_human(*human_args) if args.human_games else None
    return _fit_and_evaluate(args.out, presets, args.seed, args.benchmark_games, calibration, started, human,
                             args.production_model)


def _fit_and_evaluate(out, presets, seed, games_per_preset, calibration, started, human=None, production_path=None):
    weight = calibration["calibrated_weight"]
    bot_version = CalibratedPlayerBot(weight).version
    reference = FakeSuspicionModel()
    content = FixtureContentSource().load("*")
    rows_path, manifest_path = out / "dataset/rows.jsonl", out / "dataset/manifest.json"
    if human and human["train"]:
        rows_path, manifest_path = _with_human_rows(out, rows_path, manifest_path, human["train"])
    rows, _ = train.load_dataset(rows_path, manifest_path)
    split = split_games(rows, seed)
    fit = defaultdict(set)
    for row in rows:
        if split[row.game_id] == "train":
            fit[row.preset_id].add(row.game_id)
    if sum(map(len, fit.values())) < 300 or any(len(fit[preset]) < 50 for preset in presets):
        raise ValueError("suspicion training requires 300 games and at least 50 per selected preset")
    trained = out / "trained"
    training = train.run(rows_path, manifest_path, trained, seed)
    base_path, artifact_path = trained / "models.base.pkl", trained / "models.pkl"
    artifact_path.replace(base_path)  # 학습 원본은 보존하고, 선택한 α 산출물을 models.pkl로 둔다
    base = pickle.loads(base_path.read_bytes())  # only the artifact created in this run
    reference_events = ListEventSink()
    run_seed = seed + 1
    runs = [_play(p, games_per_preset, run_seed, weight, reference, reference_events, content) for p in presets]
    benchmark, _ = features.engine_rows([runner.serde.to_jsonable(item) for item in reference_events.items])
    if {r.game_id for r in benchmark} & set(split):
        raise ValueError("benchmark overlaps training games")
    benchmark_games = games_per_preset * len(presets)
    rule_metrics = evaluate_scores(benchmark, [r.features["rule_score"] for r in benchmark], seed)
    # 관문 기준 = 지금 운영 모델(같은 benchmark 행·같은 seed로 채점, 같은 seed 대전으로 마을 승률). 없으면 규칙 점수.
    production, production_version = None, None
    if production_path is not None:
        production_bytes = Path(production_path).read_bytes()
        production = pickle.loads(production_bytes)  # 재학습 스크립트가 운영 모델 폴더에서 복사한 파일만 받는다
        production_version = "suspicion-logistic-" + hashlib.sha256(production_bytes).hexdigest()[:12]
        production_model = load_model(Path(production_path))
        try:
            production_runs = [_play(p, games_per_preset, run_seed, weight, production_model, ListEventSink(), content)
                               for p in presets]
            if not production_model.successes or production_model.successes != production_model.calls:
                raise ValueError("production benchmark inference failed")
        finally:
            production_model.close()
        reference_metrics = evaluate_scores(benchmark, artifact_scores(production, benchmark), seed)
        baseline = {"benchmark": _gate_metrics(reference_metrics, _village_rate(production_runs, benchmark_games))}
    else:
        baseline = {"benchmark": _gate_metrics(rule_metrics, _village_rate(runs, benchmark_games))}
    logits = [s / base["temperatures"]["logistic"] for s in train.model_scores(base["models"]["logistic"], benchmark)]
    rules = [r.features["rule_score"] for r in benchmark]
    trials, candidates = [], {}
    for alpha in ALPHA_GRID:
        # 응용: α마다 같은 시드로 봇 대전을 다시 돌려 마을 승률을 재고, 같은 기준선 판의 관측으로 예측 지표를 잰다.
        variant = blend_dir(out) / f"alpha_{alpha:.1f}.pkl"
        variant.write_bytes(pickle.dumps({**base, "blend_alpha": alpha}))
        model = load_model(variant)
        try:
            events = ListEventSink()
            replay = [_play(p, games_per_preset, run_seed, weight, model, events, content) for p in presets]
            if not model.successes or model.successes != model.calls:
                raise ValueError("candidate benchmark inference failed")
            version = model.model_version
        finally:
            model.close()
        metrics = evaluate_scores(benchmark, [blend(m, r, alpha) for m, r in zip(logits, rules)], seed)
        split_metrics = _gate_metrics(metrics, _village_rate(replay, benchmark_games))
        reasons = [x for x in suspicion_gate({"benchmark": split_metrics}, baseline).reasons if x.startswith("benchmark:")]
        trials.append(dict(alpha=alpha, model_version=version, reasons=reasons, log_loss=split_metrics["log_loss"],
                           ece=split_metrics["ece"], village_win_rate=split_metrics["village_win_rate"]))
        candidates[alpha] = (variant, split_metrics, version)
    chosen = select_alpha(trials)
    pick = chosen if chosen is not None else 1.0
    variant, chosen_metrics, model_version = candidates[pick]
    artifact_path.write_bytes(variant.read_bytes())
    candidate = {"benchmark": chosen_metrics}
    human_evaluation = "no_human_input"
    if human is not None:
        human_evaluation = "no_human_test_games"
        test = human["test"]
        if test:
            # 사람 판은 이미 끝난 실제 판이라 다른 모델로 다시 둘 수 없다. 마을 승률은 양쪽 모두 그 판들의 실제 결과다.
            games = sorted({r.game_id for r in test})
            wins = sum(human["results"][g] == "village" for g in games) / len(games)
            reference_scores = (artifact_scores(production, test) if production is not None
                                else [r.features["rule_score"] for r in test])
            candidate["human"] = _gate_metrics(evaluate_scores(test, artifact_scores(base, test, pick), seed), wins)
            baseline["human"] = _gate_metrics(evaluate_scores(test, reference_scores, seed), wins)
            human_evaluation = "provided"
    # 사람 판 시험 세트(human)가 없으면 관문은 그대로 거부한다(관문 규칙은 바꾸지 않음).
    decision = suspicion_gate(candidate, baseline)
    # 관문 함수는 그대로 두고, 재학습 쪽 선택 규칙으로 '유효 후보'만 승격 대상(accepted)으로 둔다.
    rule_log_loss = rule_metrics["metrics"]["log_loss"]
    gain = rule_log_loss - chosen_metrics["log_loss"] if chosen is not None else None
    effective_reasons = effectiveness(chosen, chosen_metrics["log_loss"], rule_log_loss)
    accepted = decision.accepted and not effective_reasons
    import mlflow

    client = mlflow.MlflowClient()
    run_id = training["run_id"]
    client.set_tag(run_id, "paldo.gate.status", "passed" if decision.accepted else "rejected")
    client.set_tag(run_id, "paldo.promotable", "true" if accepted else "false")
    client.set_tag(run_id, "paldo.effective_reasons", json.dumps(effective_reasons))
    client.set_tag(run_id, "paldo.gate.reasons", json.dumps(decision.reasons))
    client.set_tag(run_id, "player_bot_version", bot_version)
    client.set_tag(run_id, "calibration_data_version", calibration["data_version"])
    client.set_tag(run_id, "paldo.blend_alpha", "none" if chosen is None else f"{chosen:.1f}")
    client.set_tag(run_id, "paldo.blend_search", json.dumps(trials))
    client.set_tag(run_id, "paldo.reference_model", production_version or "rule-v0")
    client.set_tag(run_id, "paldo.human_evaluation", human_evaluation)
    # train.run이 올린 산출물은 α 적용 전 원본이므로, 고른 α 산출물을 같은 실행에 따로 올린다.
    client.log_artifact(run_id, str(artifact_path), "selected")
    report = dict(accepted=accepted, gate_accepted=decision.accepted, effective=not effective_reasons,
                  effective_reasons=effective_reasons, reasons=[*decision.reasons, *effective_reasons],
                  rule_log_loss=rule_log_loss, log_loss_gain_vs_rule=gain, serving_version=decision.serving_version,
                  run_id=run_id, model_version=model_version, base_model_version="suspicion-logistic-" + hashlib.sha256(
                      base_path.read_bytes()).hexdigest()[:12], blend_alpha=chosen, blend_search=trials,
                  calibration_data_version=calibration["data_version"],
                  data_version=training["manifest"]["data_version"], feature_version=features.FEATURE_VERSION,
                  code_commit=GitRepo().head(), player_bot_version=bot_version, training_games=sum(map(len, fit.values())),
                  candidate=candidate, reference=baseline, reference_model=production_version or "rule-v0",
                  human_evaluation=human_evaluation, human=human["record"] if human else None,
                  seconds=round(perf_counter() - started, 3))
    result = json.dumps(report, ensure_ascii=False)
    (out / "decision.json").write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
