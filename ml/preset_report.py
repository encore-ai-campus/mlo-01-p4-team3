"""D-10-03 current-rule preset data and unseen-secret-combination evaluation."""
from __future__ import annotations

import argparse
import hashlib
from itertools import groupby
import json
from pathlib import Path

from engine.domain import params, serde
from engine.domain.engine import PaldoEngine
from engine.domain.model import PRESETS, ObservationSnapshot, Role
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.testing.fakes import FakeSuspicionModel
from ml.suspicion.evaluate import metrics
from tools.check_content import content_problems, count_violations, load_bundle
from tools.evidence_result import write_result
from tools.jsonlog import log
from tools.perf.challenge_balance import wilson
from tools.run_engine_games import JsonlEventSink, game_seed, run_game

PRESET_IDS = ("short5", "base6", "base7", "large8")


def no_lock_day_limit(preset):
    """Every no-lock night removes one villager until G >= V + M, so the last day is N - 2K."""
    return preset.n_active - 2 * preset.n_possessed


def secret_combination(preset_id, secrets):
    """Distinguish secret IDs together with their holders and preset."""
    return preset_id, tuple(sorted(secrets.items()))


def read_events(path):
    """Read source events without retaining the whole multi-game file."""
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                yield json.loads(line)


def training_combinations(events, model_report):
    """Exclude both fitting and temperature-calibration combinations from the new evaluation."""
    report = json.loads(Path(model_report).read_text(encoding="utf-8"))
    with Path(events).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != report["manifest"]["input_sha256"]:
        raise ValueError("training source checksum mismatch")
    used = {game for game, split in report["split"].items() if split in ("train", "dev")}
    found, combinations = set(), set()
    for event in read_events(events):
        if event["game_id"] in used and event["event_type"] == "truth_revealed":
            found.add(event["game_id"])
            combinations.add(secret_combination(event["preset_id"], event["payload"]["secrets"]))
    if found != used:
        raise ValueError("missing training or calibration truth")
    return combinations, digest


def evaluate_unseen(events, model, excluded):
    """Use truth only for eligibility and labels after a completed game's predictions."""
    outcomes = {preset: dict(labels=[], probabilities=[], games=0, excluded_games=0) for preset in PRESET_IDS}
    for _, group in groupby(read_events(events), key=lambda event: event["game_id"]):
        game = list(group)
        preset = game[0]["preset_id"]
        end = next(event for event in game if event["event_type"] == "game_ended")
        truth = next(event for event in game if event["event_type"] == "truth_revealed")
        if not end["payload"].get("completed", True) or truth["seq"] <= end["seq"]:
            raise ValueError("completed truth must follow game end")
        outcome = outcomes[preset]
        if secret_combination(preset, truth["payload"]["secrets"]) in excluded:
            outcome["excluded_games"] += 1
            continue
        count = 0
        for event in game:
            if event["event_type"] != "observation" or event["seq"] >= end["seq"]:
                continue
            obs = serde.from_jsonable(ObservationSnapshot, event["payload"]["snapshot"])
            if obs.private.role is not Role.VILLAGER or not obs.candidates:
                continue
            estimate = model.estimate(obs)
            labels = [int(truth["payload"]["roles"][candidate] == Role.POSSESSED.value) for candidate in obs.candidates]
            outcome["labels"].append(labels)
            outcome["probabilities"].append([estimate.probs[candidate] for candidate in obs.candidates])
            count += 1
        outcome["games"] += bool(count)
    return {preset: dict(games=row["games"], excluded_games=row["excluded_games"],
                         snapshots=len(row["labels"]),
                         metrics=metrics(row["labels"], row["probabilities"]) if row["labels"] else None)
            for preset, row in outcomes.items()}


def main(argv=None):
    """Generate the four presets and evaluate the fixed existing suspicion model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=500, help="games per preset")
    parser.add_argument("--seed", type=int, default=1112)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--training-events", type=Path, required=True)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.games <= 100000 or args.seed < 0:
        parser.error("positive game count up to 100000 and nonnegative seed required")
    from ml.adapters.suspicion_model import _ArtifactModel

    excluded, training_sha = training_combinations(args.training_events, args.model.parent / "report.json")
    model = _ArtifactModel(args.model)
    content = load_bundle(Path("content/bundle_v1.json"))
    problems = content_problems(content)
    if problems:
        raise ValueError("invalid content")
    args.out.mkdir(parents=True, exist_ok=False)
    engine = PaldoEngine(RuleV0Policy(content))
    deps = EngineDeps(content, FakeSuspicionModel())
    rows = {}
    path = args.out / "events.jsonl"
    sink = JsonlEventSink(path)
    try:
        for index, preset in enumerate(PRESET_IDS):
            runs = []
            seed = args.seed + index
            first_seed = game_seed(seed, 0)
            violations = dict(count_violations(content, preset, range(first_seed, first_seed + args.games)))
            if violations:
                rows[preset] = dict(seed=seed, games=0, village_win_rate=None, win_rate_95=None,
                                    days_range=None, no_lock_day_limit=no_lock_day_limit(PRESETS[preset]),
                                    validation_errors=violations)
                continue
            for i in range(args.games):
                runs.append(run_game(engine, deps, preset, seed, i, "rule-v0", sink,
                                     player_bot=LogicBot()))
                if (i + 1) % 100 == 0:
                    log("preset_report", "info", "generation", preset=preset, games=i + 1)
            wins = sum(run.winner == "village" for run in runs)
            rows[preset] = dict(seed=seed, games=len(runs), village_win_rate=wins / len(runs),
                                win_rate_95=wilson(wins, len(runs)),
                                no_lock_day_limit=no_lock_day_limit(PRESETS[preset]),
                                validation_errors={})
    finally:
        sink.close()
    days = {preset: [] for preset in PRESET_IDS}
    for event in read_events(path):
        if event["event_type"] == "game_ended":
            days[event["preset_id"]].append(event["day"])
    for preset in rows:
        if not days[preset]:
            continue
        rows[preset]["days_range"] = [min(days[preset]), max(days[preset])]
        if max(days[preset]) > rows[preset]["no_lock_day_limit"]:
            raise AssertionError("day bound violated")
    unseen = evaluate_unseen(path, model, excluded)
    report = dict(rule_version=params.RULE_PARAMS_VERSION, content_version=content.content_version,
                  policy="rule-v0", player_bot=LogicBot.version, generation_model=FakeSuspicionModel.model_version,
                  evaluated_suspicion_model=model.model_version, training_sha256=training_sha,
                  excluded_secret_combinations=len(excluded), content_errors=len(problems), engine_errors=0,
                  presets={preset: dict(row, unseen=unseen[preset]) for preset, row in rows.items()})
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.record:
        values = {}
        for preset, row in report["presets"].items():
            for key in ("games", "village_win_rate", "no_lock_day_limit"):
                if row[key] is not None:
                    values[f"{preset}_{key}"] = {"value": row[key], "unit": "ratio" if key == "village_win_rate" else "count"}
            for key, value in (row["unseen"]["metrics"] or {}).items():
                values[f"{preset}_unseen_{key}"] = {"value": value, "unit": "ratio"}
        write_result("D-10-03", "presets", values,
                     f"python -m ml.preset_report --games {args.games} --seed {args.seed} --out <new directory> "
                     "--model <trusted models.pkl> --training-events <official D-08-01 events.jsonl> --record")
    log("preset_report", "info", "finished", **report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
