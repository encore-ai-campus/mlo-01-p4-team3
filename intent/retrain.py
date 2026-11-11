"""D-10-01 private replay of the frozen human intent split and lifecycle gate."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import random
import re
from time import perf_counter

from contracts.datasets import LabelSource, Split
from engine.domain.model import PLAYER_ID, Command, missing_slots
from engine.domain.serde import dumps, from_jsonable
from engine.ports import ParseContext, ParseFailure
from ml.gate import INTENT_SLOTS, intent_gate
from pipeline.spark.privacy_filter import load_tombstones
from tools.intent_examples import LABELS
from tools.perf.engine_bench import percentile
from tools.ut_freeze import (DEFAULT_CUTOFF, DEFAULT_OPEN, FREEZE_DATA_VERSION, KST, freeze_data_version,
                             freeze_manifest, normalize)


def training_rows(rows, deleted, eval_users, eval_texts):
    """Filter tombstones and low-trust labels, then fail on holdout leakage."""
    from intent.matcher import normalize_people

    priority = {LabelSource.PLAYER_EDITED: 1, LabelSource.HUMAN_ANNOTATION: 2}
    kept = {}
    for row in rows:
        if row.split is not Split.TRAIN or not row.split_key.user or row.label not in LABELS:
            raise ValueError("invalid human training annotation")
        if row.split_key.user in deleted or row.label_source not in priority:
            continue
        if row.split_key.user in eval_users or normalize_people(row.text, {}) in eval_texts:
            raise ValueError("evaluation overlap in training")
        prior = kept.get(row.example_id)
        if prior is None or priority[row.label_source] > priority[prior.label_source]:
            kept[row.example_id] = row
        elif prior.label_source == row.label_source and prior != row:
            raise ValueError("conflicting human training annotations")
    return list(kept.values())


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _freeze(folder):
    names = ("test_human.jsonl", "train_candidates.jsonl", "assignment.json", "report.json")
    if _json(folder / "manifest.json") != freeze_manifest(folder, names):
        raise ValueError("freeze manifest mismatch")
    report, assignment = _json(folder / "report.json"), _json(folder / "assignment.json")
    train_users, eval_users = set(assignment["train_users"]), set(assignment["eval_users"])
    if (report["ok"] is not True or report["cutoff"] != DEFAULT_CUTOFF or report["since"] != DEFAULT_OPEN
            or report["user_overlap"] != 0 or report["sentence_conflicts"] != report["conflicts_dropped"]
            or not eval_users or train_users & eval_users):
        raise ValueError("invalid or overlapping freeze assignment")
    parts = []
    for filename, users in ((names[0], eval_users), (names[1], train_users)):
        raw = _rows(folder / filename)
        by_id = {r["attempt_id"]: r for r in raw}
        if (len(raw) != len(by_id) or not all(by_id)
                or any(not r["player_pseudonym"] or not normalize(r["text"])
                       or (r["player_pseudonym"] not in users and not (filename == names[0] and r["pm"]))
                       or (filename == names[0] and r["player_pseudonym"] in train_users)
                       for r in raw)):
            raise ValueError("freeze rows do not match assignment")
        parts.append(by_id)
    if (parts[0].keys() & parts[1].keys()
            or {normalize(r["text"]) for r in parts[0].values()} & {normalize(r["text"]) for r in parts[1].values()}):
        raise ValueError("freeze attempt or sentence overlap")
    return (*parts, eval_users | {r["player_pseudonym"] for r in parts[0].values()})


def _verify_annotations(rows, originals):
    for row in rows:
        raw = originals.get(row.example_id)
        if (raw is None or row.split_key.user != raw["player_pseudonym"]
                or row.split_key.game_id != raw["game_id"] or row.text != raw["text"]):
            raise ValueError("annotation does not match frozen attempt")


def _human_cases(rows, contexts):
    from intent.evaluation import candidate_label

    cases = {}
    for item in contexts:
        key = item["example_id"]
        ctx = from_jsonable(ParseContext, item["context"])
        gold = from_jsonable(list[Command], item["commands"])
        if key in cases or any(c.actor != PLAYER_ID or missing_slots(c) for c in gold):
            raise ValueError("duplicate or invalid reviewed context")
        cases[key] = ctx, gold
    if (set(cases) != {row.example_id for row in rows}
            or any(row.split is not Split.TEST_HUMAN or row.label_source is not LabelSource.HUMAN_ANNOTATION
                   or cases[row.example_id][0].game_id != row.split_key.game_id
                   or row.label != (candidate_label(cases[row.example_id][1][0])
                                    if cases[row.example_id][1] else "unsupported") for row in rows)):
        raise ValueError("reviewed evaluation cases do not match frozen labels")
    return cases


def _evaluate(parser, rows, cases, seed):
    from sklearn.metrics import f1_score
    from intent.evaluation import candidate_label, command_equal

    correct, predicted, gold_labels = [], [], []
    users, contrasts, errors = defaultdict(list), defaultdict(list), Counter()
    singles = unsupported = false_positive = 0
    for row in rows:
        ctx, gold = cases[row.example_id]
        result = parser.parse(row.text, ctx)
        actual = [candidate.command for candidate in result.candidates]
        remaining = list(actual)
        for command in gold:
            index = next((i for i, other in enumerate(remaining) if command_equal(command, other)), None)
            if index is None:
                break
            remaining.pop(index)
        else:
            index = -1
        exact = bool(index == -1 and not remaining and (gold or result.failure is ParseFailure.UNSUPPORTED_INTENT))
        correct.append(exact)
        users[row.split_key.user].append(exact)
        gold_labels.append(row.label)
        predicted.append(candidate_label(actual[0]) if actual else "unsupported")
        if row.contrast_group:
            contrasts[row.contrast_group].append((row.label, exact))
        if not gold:
            unsupported += 1
            false_positive += bool(actual)
        if len(gold) == 1:
            singles += 1
            for slot in INTENT_SLOTS:
                left = getattr(gold[0], slot)
                right = getattr(actual[0], slot) if len(actual) == 1 else None
                if slot == "evidence" and right is not None:
                    left, right = sorted(map(dumps, left)), sorted(map(dumps, right))
                errors[slot] += len(actual) != 1 or left != right
    pairs = [group for group in contrasts.values() if len({label for label, _ in group}) > 1]
    exact_rate = sum(correct) / len(correct)
    report = dict(dataset="test_human", rows=len(rows), command_exact_match=exact_rate,
                  macro_f1=float(f1_score(gold_labels, predicted, labels=sorted(set(gold_labels)),
                                          average="macro", zero_division=0)),
                  contrast_accuracy=sum(all(hit for _, hit in group) for group in pairs) / len(pairs) if pairs else None,
                  unsupported_false_positive_rate=false_positive / unsupported if unsupported else None,
                  slot_errors={slot: errors[slot] / singles for slot in INTENT_SLOTS} if singles else {},
                  contrast_groups=len(pairs), unsupported_rows=unsupported, single_command_rows=singles)
    if len(rows) < 200:
        clusters = [(sum(hits), len(hits)) for hits in users.values()]
        rng = random.Random(seed)
        draws = []
        for _ in range(2000):
            hits, sizes = zip(*rng.choices(clusters, k=len(clusters)))
            draws.append(sum(hits) / sum(sizes))
        report["command_exact_match_ci95"] = [min(exact_rate, percentile(draws, 2.5)),
                                             max(exact_rate, percentile(draws, 97.5))]
    return report


def _local_registry(folder, artifact, report):
    import mlflow
    from ops.mlflow.gate import check_run, register_candidate

    folder.mkdir(parents=True, exist_ok=True)
    uri = "sqlite:///" + (folder / "tracking.db").as_posix()
    mlflow.set_tracking_uri(uri)
    mlflow.set_registry_uri(uri)
    client = mlflow.MlflowClient()
    name = "D-10-01-isolated-intent"
    experiment = client.get_experiment_by_name(name)
    exp_id = experiment.experiment_id if experiment else client.create_experiment(
        name, artifact_location=(folder / "artifacts").as_uri())
    with mlflow.start_run(experiment_id=exp_id, tags=report["versions"]) as run:
        run_id = run.info.run_id
        source_uri = run.info.artifact_uri.rstrip("/") + "/model"
        mlflow.log_artifact(str(artifact.parent / "decision.json"))
        if report["accepted"] and not report["fixture"]:
            mlflow.log_artifacts(str(artifact), "model")
    client.set_tag(run_id, "paldo.gate.reasons", json.dumps(report["reasons"]))
    passed = check_run(client, run_id, lambda _: (report["accepted"], "accepted" if report["accepted"] else "intent_gate"),
                       allow_freeze=True)
    version = register_candidate(client, run_id, "intent", "model", source_uri=source_uri) if passed else None
    return {"run_id": run_id, "candidate": version, "tracking": "isolated_sqlite"}


def main(argv=None):
    """Replay reviewed human data through KoELECTRA, the D gate and isolated MLflow."""
    from intent.baseline import load_examples
    from intent.bert import BertIntentParser
    from intent.finetune import TOKENIZER_VERSION, _sha, main as finetune
    from intent.matcher import normalize_people
    from intent.parser import BaselineIntentParser
    from tools.gitrepo import GitRepo

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "train-labels", "eval-labels", "contexts", "tombstones", "cache", "out"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--registry-dir", type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--fixture", action="store_true")
    refs = parser.add_mutually_exclusive_group(required=True)
    refs.add_argument("--first-model", action="store_true")
    refs.add_argument("--reference-bert", type=Path)
    args = parser.parse_args(argv)
    if datetime.fromisoformat(DEFAULT_OPEN) <= datetime.now(KST) < datetime.fromisoformat(DEFAULT_CUTOFF):
        parser.error("training is paused during the formal user test")
    if args.fixture:
        if not args.data_version.startswith("fixture:"):
            parser.error("fixture mode requires fixture provenance")
    elif not args.registry_dir or not (re.fullmatch(r"delta:[A-Za-z0-9_.-]+@\d+", args.data_version)
                                       or FREEZE_DATA_VERSION.fullmatch(args.data_version)):
        parser.error("real runs require a Delta snapshot or freeze manifest version and a private isolated registry directory")
    elif (FREEZE_DATA_VERSION.fullmatch(args.data_version)
          and ((not (args.freeze / "manifest.json").is_file()) or args.data_version != freeze_data_version(args.freeze))):
        parser.error("freeze data version must equal the sha256 of --freeze manifest.json")
    if args.out.exists() or args.epochs < 1 or args.threads < 1:
        parser.error("use a new private output directory and positive epochs/threads")

    started = perf_counter()
    commit = GitRepo().head()
    test, pool, eval_users = _freeze(args.freeze)
    evaluation = load_examples(args.eval_labels)
    _verify_annotations(evaluation, test)
    if {r.example_id for r in evaluation} != set(test):
        raise ValueError("evaluation labels must cover the frozen attempts")
    annotations = _rows(args.train_labels)
    from contracts.datasets import IntentExample
    training = [from_jsonable(IntentExample, row) for row in annotations]
    _verify_annotations(training, pool)
    deleted = load_tombstones(args.tombstones)["players"]
    evaluation_cases = _human_cases(evaluation, _rows(args.contexts))
    evaluation = [r for r in evaluation if r.split_key.user not in deleted]
    eval_texts = {normalize_people(r["text"], {}) for r in test.values()}
    kept = training_rows(training, deleted, eval_users, eval_texts)
    seed_file = Path("content/intent/examples_v1.jsonl")
    if (not kept or not evaluation or eval_texts & {normalize_people(r.text, {}) for r in load_examples(seed_file)}
            or eval_texts & {normalize_people(r.text, {}) for r in load_examples()}):
        raise ValueError("empty usable split or seed/evaluation sentence overlap")
    model_dir = args.out / "checkpoint"
    eval_groups = {r.contrast_group for r in evaluation if r.contrast_group}
    if eval_groups & {r.contrast_group for r in kept}:
        raise ValueError("training and evaluation contrast overlap")
    finetune(["--cache", str(args.cache), "--epochs", str(args.epochs), "--threads", str(args.threads)],
             human_train=kept, run_dir=model_dir, data_version=args.data_version)
    candidate = BertIntentParser(model_dir)
    reference = BertIntentParser(args.reference_bert) if args.reference_bert else BaselineIntentParser()
    candidate_metrics = _evaluate(candidate, evaluation, evaluation_cases, 7)
    reference_metrics = _evaluate(reference, evaluation, evaluation_cases, 7)
    gate = intent_gate(candidate_metrics, reference_metrics,
                       current_version=reference.model.model_version if args.reference_bert else None)
    if load_tombstones(args.tombstones)["players"] != deleted:
        raise ValueError("tombstones changed during training; restart with current inputs")
    report = dict(accepted=gate.accepted, reasons=gate.reasons, serving_version=gate.serving_version,
                  reference_artifact_version=reference.model.model_version, candidate_version=candidate.model.model_version,
                  candidate=candidate_metrics, reference=reference_metrics, fixture=args.fixture,
                  training_rows=len(kept), human_labels=dict(Counter(r.label for r in kept)),
                  deleted_training_rows=sum(r.split_key.user in deleted for r in training),
                  deleted_training_rows_remaining=sum(r.split_key.user in deleted for r in kept),
                  auto_executed_remaining=sum(r.label_source is LabelSource.AUTO_EXECUTED for r in kept),
                  freeze_sha256=_sha(args.freeze / "manifest.json"),
                  input_sha256={name: _sha(path) for name, path in
                                (("train", args.train_labels), ("eval", args.eval_labels), ("contexts", args.contexts))},
                  versions=dict(data_version=args.data_version, feature_version=TOKENIZER_VERSION,
                                code_commit=commit, replay_source_sha256=_sha(__file__)),
                  seconds=round(perf_counter() - started, 3))
    if FREEZE_DATA_VERSION.fullmatch(args.data_version) and args.data_version != "freeze:" + report["freeze_sha256"]:
        raise ValueError("freeze manifest changed during training; restart with current inputs")
    decision_path = args.out / "decision.json"
    decision_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.registry_dir:
        report["registry"] = _local_registry(args.registry_dir.resolve(), model_dir, report)
        report["seconds"] = round(perf_counter() - started, 3)
        decision_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
