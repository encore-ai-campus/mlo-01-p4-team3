"""D-10-01 calibrated bot generation, D-07-02 training and suspicion gate replay."""
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
from ml.suspicion.blend import ALPHA_GRID, blend, effectiveness, select_alpha
from ml.suspicion.evaluate import evaluate_scores, split_games
from ml.suspicion.player_calibration import CalibratedPlayerBot
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


def _human_split(rows_path, base, alpha, seed):
    """사람 판 관측 행으로 관문 human 지표를 만든다(후보 = 선택한 α, 기준 = 규칙 점수).

    사람 판은 이미 끝난 실제 판이라 다른 모델로 다시 둘 수 없다. 그래서 human의 village_win_rate는
    양쪽 모두 그 판들의 실제 마을 승률(manifest `village_win_rate`)이고, 균형 조건은 benchmark가 맡는다.
    human에서 실제로 가르는 것은 예측 오차(log_loss)와 확률 보정(ECE)이다.
    """
    manifest_path = rows_path.parent / "manifest.json"
    rows, manifest = train.load_dataset(rows_path, manifest_path)
    wins = manifest.get("village_win_rate")
    if isinstance(wins, bool) or not isinstance(wins, (int, float)) or not 0 <= wins <= 1:
        raise ValueError("human manifest needs the observed village_win_rate")
    logits = [s / base["temperatures"]["logistic"] for s in train.model_scores(base["models"]["logistic"], rows)]
    candidate = evaluate_scores(rows, [blend(m, r.features["rule_score"], alpha) for m, r in zip(logits, rows)], seed)
    reference = evaluate_scores(rows, [r.features["rule_score"] for r in rows], seed)
    return _gate_metrics(candidate, wins), _gate_metrics(reference, wins)


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
    parser.add_argument("--human-rows", type=Path,
                        help="사람 판 관측 행 rows.jsonl(같은 폴더 manifest.json에 실제 village_win_rate). 없으면 관문이 거부")
    args = parser.parse_args(argv)
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
    return _fit_and_evaluate(args.out, presets, args.seed, args.benchmark_games, calibration, started, args.human_rows)


def _fit_and_evaluate(out, presets, seed, games_per_preset, calibration, started, human_dataset=None):
    weight = calibration["calibrated_weight"]
    bot_version = CalibratedPlayerBot(weight).version
    reference = FakeSuspicionModel()
    content = FixtureContentSource().load("*")
    rows_path, manifest_path = out / "dataset/rows.jsonl", out / "dataset/manifest.json"
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
    reference_metrics = evaluate_scores(benchmark, [r.features["rule_score"] for r in benchmark], seed)
    baseline = {"benchmark": _gate_metrics(reference_metrics, _village_rate(runs, benchmark_games))}
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
    if human_dataset:
        candidate["human"], baseline["human"] = _human_split(human_dataset, base, pick, seed)
    # 실제 사용자 판 시험 세트(human)가 없으면 관문은 봇 판 시험 세트(benchmark)만으로 판단한다(11/11 사용자 결정).
    decision = suspicion_gate(candidate, baseline)
    # 관문 함수는 그대로 두고, 재학습 쪽 선택 규칙으로 '유효 후보'만 승격 대상(accepted)으로 둔다.
    rule_log_loss = baseline["benchmark"]["log_loss"]
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
                  candidate=candidate, reference=baseline,
                  human_evaluation="provided" if human_dataset else "skipped_no_human_games",
                  seconds=round(perf_counter() - started, 3))
    result = json.dumps(report, ensure_ascii=False)
    (out / "decision.json").write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
