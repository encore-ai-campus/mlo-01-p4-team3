"""Train LightGBM and compare against Beta on finalized C-08-02 impression counts."""
import argparse
import json
import math
import os
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

from ads.app.ctr_update import finalized_rows
from ads.domain.model import AdSlot, GameOutcome, SlotPolicy
from ads.domain.rules import ctr_posterior
from ml.gate import ad_ctr_gate

FEATURES = ("slot", "creative_id", "outcome", "preset_id", "hour")
FEATURE_VERSION = "ad-context-v1"


def split_rows(snapshot, eval_from, source):
    """Split finalized source-isolated cells at an attribution day, rejecting duplicates."""
    train, evaluation, seen = [], [], set()
    cutoff = date.fromisoformat(eval_from)
    if source not in ("human", "synthetic"):
        raise ValueError("invalid source")
    for row in finalized_rows(snapshot):
        if row["source"] != source:
            continue
        slot, creative, outcome, preset, hour = (row[name] for name in FEATURES)
        day = date.fromisoformat(row["attribution_day"])
        n, clicks = row["impressions"], row["clicks"]
        AdSlot(slot)
        if outcome is not None:
            GameOutcome(outcome)
        if (not isinstance(creative, str) or not creative or creative.startswith("syn:") != (source == "synthetic")
                or (preset is not None and (not isinstance(preset, str) or not preset))
                or type(hour) is not int or not 0 <= hour < 24
                or type(n) is not int or type(clicks) is not int or not 0 <= clicks <= n
                or day.isoformat() != row["attribution_day"]):
            raise ValueError("invalid CTR training cell")
        key = day, slot, creative, outcome, preset, hour
        if key in seen:
            raise ValueError("duplicate CTR training cell")
        seen.add(key)
        if n:
            (train if day < cutoff else evaluation).append(row)
    if not train or not evaluation:
        raise ValueError("both chronological splits need impressions")
    return train, evaluation


def weighted_metrics(rows, probabilities):
    """Compute impression-level binary log loss and ten-bin ECE from aggregate cells."""
    total = sum(row["impressions"] for row in rows)
    if not total:
        raise ValueError("no evaluation impressions")
    bins = defaultdict(float)
    loss = 0.
    for row, p in zip(rows, probabilities, strict=True):
        n, clicks = row["impressions"], row["clicks"]
        q = max(1e-12, min(1 - 1e-12, p))
        loss -= clicks * math.log(q) + (n - clicks) * math.log1p(-q)
        bins[min(9, int(p * 10))] += n * p - clicks
    return dict(impressions=total, log_loss=loss / total, ece=sum(map(abs, bins.values())) / total)


def compare(train, evaluation, policies):
    """Fit weighted binary LightGBM and compare both estimators on the same held-out cells."""
    import numpy as np
    from lightgbm import LGBMClassifier

    train_keys = [tuple(row[name] for name in FEATURES) for row in train]
    vocab = [{value: i for i, value in enumerate(sorted({key[j] for key in train_keys}, key=str))}
             for j in range(4)]

    def encode(keys):
        return np.asarray([[codes[key[j]] for j, codes in enumerate(vocab)] + [key[-1]] for key in keys])

    totals = defaultdict(lambda: [0, 0])
    samples, labels, weights = [], [], []
    for key, row in zip(train_keys, train, strict=True):
        clicks, n = row["clicks"], row["impressions"]
        totals[key[:2]][0] += n
        totals[key[:2]][1] += clicks
        for label, weight in ((1, clicks), (0, n - clicks)):
            if weight:
                samples.append(key)
                labels.append(label)
                weights.append(weight)
    if len(set(labels)) < 2:
        raise ValueError("training needs clicked and nonclicked impressions")
    model = LGBMClassifier(n_estimators=100, num_leaves=15, min_child_samples=12,
                           learning_rate=.05, deterministic=True, force_col_wise=True,
                           n_jobs=1, random_state=17, verbosity=-1)
    model.fit(encode(samples), labels, sample_weight=weights, categorical_feature=[0, 1, 2, 3],
              feature_name=list(FEATURES))
    keys = sorted(set(train_keys), key=str)
    lookup = dict(zip(keys, model.booster_.predict(encode(keys), num_threads=1).tolist(), strict=True))
    baseline, candidate = [], []
    for row in evaluation:
        key = tuple(row[name] for name in FEATURES)
        beta = ctr_posterior(policies[AdSlot(key[0])], *totals[key[:2]]).mean
        baseline.append(beta)
        candidate.append(lookup.get(key, beta))
    return model.booster_, lookup, baseline, candidate, [list(codes) for codes in vocab]


def register(booster, report):
    """Record the decision in MLflow and register only a passing LightGBM candidate."""
    import mlflow
    import mlflow.lightgbm

    from ops.mlflow.gate import check_run

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise ValueError("set MLFLOW_TRACKING_URI explicitly")
    tags = dict(data_version=report["data_version"], feature_version=FEATURE_VERSION,
                code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip())
    decision = report["gate"]
    with mlflow.start_run(tags=tags) as run:
        mlflow.log_metrics({f"{name}_{key}": value for name in ("beta", "lightgbm")
                            for key, value in report[name].items()})
        model = (mlflow.lightgbm.log_model(booster, name="ad_ctr", pip_requirements=["lightgbm==4.7.0"])
                 if decision["accepted"] else None)
        mlflow.log_dict(report, "ad_ctr.json")
    client = mlflow.MlflowClient()
    reason = ".".join(decision["reasons"]) or "ctr_gate_passed"
    if not check_run(client, run.info.run_id, lambda _: (decision["accepted"], reason)):
        return None
    name = "paldo-ad-ctr-" + report["source"]
    version = mlflow.register_model(model.model_uri, name).version
    client.set_registered_model_alias(name, "candidate", version)
    return f"{name}:{version}"


def registered_report(reference):
    """Read the canonical passed report for an exact MLflow model version."""
    import mlflow

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise ValueError("set MLFLOW_TRACKING_URI explicitly")
    name, version = reference.split(":")
    try:
        client = mlflow.MlflowClient()
        model = client.get_model_version(name, version)
        run = client.get_run(model.run_id)
        if (model.status != "READY" or run.info.status != "FINISHED"
                or run.data.tags.get("paldo.gate.status") != "passed"):
            raise ValueError("CTR registry gate did not pass")
        with tempfile.TemporaryDirectory() as directory:
            path = mlflow.artifacts.download_artifacts(run_id=model.run_id, artifact_path="ad_ctr.json",
                                                       dst_path=directory)
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except mlflow.exceptions.MlflowException:
        raise ValueError("could not verify CTR registry version") from None


def main(argv=None):
    """Write held-out metrics and a serving lookup from a finalized contextual gold export."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--eval-from", required=True)
    parser.add_argument("--source", choices=["human", "synthetic"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--register", action="store_true")
    args = parser.parse_args(argv)
    snapshot = json.loads(args.input.read_text(encoding="utf-8"))
    train, evaluation = split_rows(snapshot, args.eval_from, args.source)
    fixtures = json.loads((Path(__file__).resolve().parents[1] / "fixtures/ads/campaigns.json").read_text(encoding="utf-8"))
    policies = {AdSlot(row["slot"]): SlotPolicy(**(row | {"slot": AdSlot(row["slot"])}))
                for row in fixtures["slot_policies"]}
    booster, lookup, beta, lgb, category_values = compare(train, evaluation, policies)
    baseline, candidate = weighted_metrics(evaluation, beta), weighted_metrics(evaluation, lgb)
    candidate["model_impressions"] = sum(row["impressions"] for row in evaluation
                                        if tuple(row[name] for name in FEATURES) in lookup)
    report = dict(source=args.source, data_version=snapshot["source_version"], feature_version=FEATURE_VERSION,
                  eval_from=args.eval_from, beta=baseline, lightgbm=candidate,
                  gate=asdict(ad_ctr_gate(candidate, baseline)), category_values=category_values,
                  predictions=[[*key, p] for key, p in lookup.items()])
    report["evaluation"] = [[row["attribution_day"], *(row[name] for name in FEATURES), row["impressions"], b, p]
                            for row, b, p in zip(evaluation, beta, lgb, strict=True)]
    report["registry_version"] = register(booster, report) if args.register else None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("predictions", "evaluation")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
