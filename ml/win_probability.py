"""D-10-03 offline village win probability from public bot-game observations."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path

from engine.domain.review import public_win_features as public_features
from tools.evidence_result import write_result
from tools.jsonlog import log

FEATURE_VERSION = "win-public-v1"


def daily_rows(events):
    """Join the first observation of each day to a subsequent completed result."""
    first, results = {}, {}
    for event in events:
        if event["source"] != "bot" or event.get("load_test", False):
            continue
        kind = event["event_type"]
        game, seq = event["game_id"], event["seq"]
        if kind == "game_ended":
            payload = event["payload"]
            winner = payload.get("winner")
            if (payload.get("completed", True) and payload.get("reason") != "aborted"
                    and winner in ("village", "changgwi")):
                results[game] = seq, int(winner == "village")
        elif kind == "observation":
            day = event["day"]
            key = game, day
            if key not in first or seq < first[key][0]:
                public = event["payload"]["snapshot"]["public"]
                first[key] = seq, public_features(day, event["preset_id"], public)
    return {key: (features, results[key[0]][1])
            for key, (seq, features) in sorted(first.items())
            if key[0] in results and seq < results[key[0]][0]}


def game_split(game_ids, seed):
    """Hold out whole games with a fixed seed."""
    games = sorted(set(game_ids))
    if len(games) < 5:
        raise ValueError("need at least five completed games")
    random.Random(seed).shuffle(games)
    n_test = len(games) // 5
    return set(games[n_test:]), set(games[:n_test])


def win_metrics(labels, probabilities):
    """Score binary log loss and 10-bin expected calibration error."""
    if not labels or len(labels) != len(probabilities):
        raise ValueError("empty or mismatched evaluation")
    errors = [0.0] * 10
    loss = 0.0
    for label, prob in zip(labels, probabilities):
        if label not in (0, 1) or not 0 <= prob <= 1:
            raise ValueError("invalid outcome or probability")
        loss -= math.log(max(prob if label else 1 - prob, 1e-15))
        errors[min(9, int(prob * 10))] += label - prob
    return dict(log_loss=loss / len(labels), ece=sum(map(abs, errors)) / len(labels),
                predicted_mean=sum(probabilities) / len(labels),
                actual_rate=sum(labels) / len(labels))


def main(argv=None):
    """Fit a CPU logistic baseline and export held-out daily predictions."""
    import joblib
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--record", action="store_true", help="write D-10-03 evidence metrics")
    args = parser.parse_args(argv)

    digest = hashlib.sha256()
    versions = {key: set() for key in ("rule_version", "content_version", "preset_id")}

    def events(source):
        for line in source:
            digest.update(line)
            if line.strip():
                event = json.loads(line)
                if event["source"] == "bot" and not event.get("load_test", False):
                    for key in versions:
                        versions[key].add(event[key])
                yield event

    with args.events.open("rb") as source:
        rows = daily_rows(events(source))
    train_games, test_games = game_split((game for game, _ in rows), args.seed)
    log("win_probability", "info", "game_split", train_games=len(train_games), test_games=len(test_games))
    train = [row for (game, _), row in rows.items() if game in train_games]
    test = [(key, *row) for key, row in rows.items() if key[0] in test_games]
    train_y = [label for _, label in train]
    if set(train_y) != {0, 1}:
        raise ValueError("training requires both outcomes")
    model = make_pipeline(DictVectorizer(), LogisticRegression(max_iter=500))
    model.fit([features for features, _ in train], train_y)
    labels = [label for _, _, label in test]
    probs = model.predict_proba([features for _, features, _ in test])[:, 1].tolist()
    report = dict(feature_version=FEATURE_VERSION, source_sha256=digest.hexdigest(),
                  seed=args.seed, train_games=len(train_games), test_games=len(test_games),
                  train_rows=len(train), test_rows=len(test),
                  model=win_metrics(labels, probs),
                  baseline=win_metrics(labels, [sum(train_y) / len(train_y)] * len(labels)))
    traces = {}
    for ((game, day), _, _), prob in zip(test, probs):
        traces.setdefault(game, {"win_prob": {}})["win_prob"][str(day)] = prob
    vectorizer, logistic = model.steps[0][1], model.steps[1][1]
    if list(logistic.classes_) != [0, 1]:
        raise ValueError("unexpected logistic class order")
    portable = dict(format="win-logistic-v1", feature_version=FEATURE_VERSION,
                    versions={key: sorted(value) for key, value in versions.items()},
                    coefficients=dict(zip(vectorizer.get_feature_names_out(), logistic.coef_[0].tolist())),
                    intercept=float(logistic.intercept_[0]), evaluation=report)
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "model.json").write_text(json.dumps(portable, indent=2) + "\n", encoding="utf-8")
    joblib.dump(model, args.out / "model.joblib")
    for name, value in (("report", report), ("review_traces", traces)):
        (args.out / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    log("win_probability", "info", "held_out_evaluation", **report)
    if args.record:
        metrics = {key: {"value": report[key], "unit": "판"}
                   for key in ("train_games", "test_games")}
        metrics.update({f"{kind}_{metric}": {"value": value, "unit": "ratio"}
                        for kind in ("model", "baseline")
                        for metric, value in report[kind].items()})
        write_result("D-10-03", "win_probability", metrics,
                     f"python -m ml.win_probability --events <completed bot events.jsonl> "
                     f"--out <new directory> --seed {args.seed} --record; sha256={digest.hexdigest()}")
    log("win_probability", "info", "artifacts_written", traces=len(traces))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
