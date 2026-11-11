"""D-10-01 calibrated bot generation, D-07-02 training and suspicion gate replay."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
from time import perf_counter

from engine.domain.engine import PaldoEngine
from engine.domain.model import PRESETS
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource, ListEventSink
from ml.adapters.suspicion_model import load_model
from ml.gate import suspicion_gate
from ml.suspicion import features, train
from ml.suspicion.evaluate import split_games
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
    return _fit_and_evaluate(args.out, presets, args.seed, args.benchmark_games, calibration, started)


def _fit_and_evaluate(out, presets, seed, games_per_preset, calibration, started):
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
    model = load_model(trained / "models.pkl")
    try:
        reference_events = ListEventSink()
        run_seed = seed + 1
        runs = [_play(p, games_per_preset, run_seed, weight, reference, reference_events, content) for p in presets]
        benchmark, _ = features.engine_rows([runner.serde.to_jsonable(item) for item in reference_events.items])
        if {r.game_id for r in benchmark} & set(split):
            raise ValueError("benchmark overlaps training games")
        candidate_events = ListEventSink()
        replay = [_play(p, games_per_preset, run_seed, weight, model, candidate_events, content) for p in presets]
        if not model.successes or model.successes != model.calls:
            raise ValueError("candidate benchmark inference failed")
        from ml.suspicion.evaluate import evaluate_scores
        import pickle

        with (trained / "models.pkl").open("rb") as stream:
            artifact = pickle.load(stream)  # only the artifact created in this run
        scores = train.model_scores(artifact["models"]["logistic"], benchmark)
        scores = [score / artifact["temperatures"]["logistic"] for score in scores]
        candidate_metrics = evaluate_scores(benchmark, scores, seed)
        reference_metrics = evaluate_scores(benchmark, [r.features["rule_score"] for r in benchmark], seed)
        benchmark_games = games_per_preset * len(presets)
        candidate = {"benchmark": _gate_metrics(candidate_metrics, sum(
            g.winner == "village" for cohort in replay for g in cohort) / benchmark_games)}
        baseline = {"benchmark": _gate_metrics(reference_metrics, sum(
            g.winner == "village" for cohort in runs for g in cohort) / benchmark_games)}
    finally:
        model.close()
    # shortcut: the current D-07-02 export only accepts bot rows, add the C-reviewed human holdout/replay when handed off.
    decision = suspicion_gate(candidate, baseline)
    import mlflow

    client = mlflow.MlflowClient()
    run_id = training["run_id"]
    client.set_tag(run_id, "paldo.gate.status", "passed" if decision.accepted else "rejected")
    client.set_tag(run_id, "paldo.gate.reasons", json.dumps(decision.reasons))
    client.set_tag(run_id, "player_bot_version", bot_version)
    client.set_tag(run_id, "calibration_data_version", calibration["data_version"])
    report = dict(accepted=decision.accepted, reasons=decision.reasons, serving_version=decision.serving_version,
                  run_id=run_id, model_version=model.model_version, calibration_data_version=calibration["data_version"],
                  data_version=training["manifest"]["data_version"], feature_version=features.FEATURE_VERSION,
                  code_commit=GitRepo().head(), player_bot_version=bot_version, training_games=sum(map(len, fit.values())),
                  candidate=candidate, reference=baseline, human_evaluation="awaiting_reviewed_input",
                  seconds=round(perf_counter() - started, 3))
    result = json.dumps(report, ensure_ascii=False)
    (out / "decision.json").write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
