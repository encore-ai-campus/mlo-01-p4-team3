"""D-09-01 frozen-human evaluation; development results never approve a model."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import re
import shlex
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from contracts.datasets import LabelSource, Split
from engine.domain.model import PLAYER_ID, Command, missing_slots
from engine.domain.serde import from_jsonable, to_jsonable
from engine.ports import ParseContext
from intent.baseline import IntentBaseline, load_examples
from intent.bert import BertIntentParser, checkpoint_record
from intent.evaluation import candidate_label, commands_equal, evaluate, reference_case
from intent.finetune import _sha
from intent.matcher import normalize_people
from intent.parser import BaselineIntentParser
from tools.evidence_result import write_result
from tools.gitrepo import GitRepo
from tools.intent_examples import note_info
from tools.jsonlog import log
from tools.perf.engine_bench import percentile
from tools.ut_freeze import FREEZE_VERSION, normalize, sentences_of

ROOT = Path(__file__).resolve().parents[1]


def validate_human(rows, training):
    """Reject non-annotated/nonhuman data and sentence/user/game/lineage/contrast leakage."""
    if not rows or any(r.split is not Split.TEST_HUMAN or r.label_source is not LabelSource.HUMAN_ANNOTATION
                       or not r.split_key.user for r in rows):
        raise ValueError("test_human requires human_annotation and pseudonymous users")
    if any(r.split is Split.TEST_HUMAN for r in training):
        raise ValueError("training provenance contains test_human")
    def sentence(row):
        text = unicodedata.normalize("NFC", normalize_people(row.text, {}))
        return "".join(text.casefold().split())
    for key in (lambda r: r.example_id, sentence, lambda r: r.split_key.user,
                lambda r: r.split_key.game_id, lambda r: r.split_key.lineage_root or r.example_id,
                lambda r: r.contrast_group):
        if {key(r) for r in rows if key(r)} & {key(r) for r in training if key(r)}:
            raise ValueError("human evaluation overlaps training/development provenance")


def bootstrap_interval(exact, rows, seed=7, repeats=2000):
    """Resample connected user/lineage/contrast groups, preserving correlated examples."""
    if not exact or len(exact) != len(rows) or repeats < 1:
        raise ValueError("nonempty aligned evaluation results required")
    parent = list(range(len(rows)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owners = {}
    for i, row in enumerate(rows):
        for key in (("user", row.split_key.user), ("game", row.split_key.game_id),
                    ("lineage", row.split_key.lineage_root or row.example_id), ("contrast", row.contrast_group)):
            if key[1] is not None:
                if key in owners:
                    parent[root(i)] = root(owners[key])
                else:
                    owners[key] = i
    groups = defaultdict(list)
    for i, correct in enumerate(exact):
        groups[root(i)].append(correct)
    clusters = list(groups.values())
    rng, samples = random.Random(seed), []
    for _ in range(repeats):
        sampled = [rng.choice(clusters) for _ in clusters]
        samples.append(sum(sum(group) for group in sampled) / sum(map(len, sampled)))
    return [percentile(samples, 2.5), percentile(samples, 97.5)]


def decision(bert, baseline, bert_ci, baseline_ci, n, *, human):
    """D-09-01 acceptance only; registry promotion and the full D-09-02 gate are separate."""
    reasons = []
    if not human:
        reasons.append("development_only; awaiting_E-09-02")
    if bert < .80:
        reasons.append("command_exact_match_below_80_percent")
    if bert <= baseline:
        reasons.append("not_above_baseline")
    if n < 200 and (bert_ci is None or baseline_ci is None or bert_ci[0] <= baseline_ci[1]):
        reasons.append("not_improved_95_percent_intervals_overlap_or_lower")
    return {"passed": not reasons, "reasons": reasons, "production_action": "keep_baseline"}


def evaluate_counterexamples(parser):
    """Compare every expected command, including multi-intent and utterance form variants."""
    fixtures = json.loads((ROOT / "fixtures/intent/counterexamples.json").read_text(encoding="utf-8"))
    seed = load_examples()[0]
    groups, cases = defaultdict(list), []
    violations = 0
    for case in fixtures:
        _, ctx, _ = reference_case(seed)
        for key, value in case.get("context", {}).items():
            if key not in {"selected_npc", "last_addressee", "aliases"}:
                raise ValueError("unknown counterexample context")
            setattr(ctx, key, value)
        expected = [from_jsonable(Command, item) for item in case["commands"]]
        result = parser.parse(case["text"], ctx)
        exact = commands_equal([c.command for c in result.candidates], expected)
        if "forms" in case:
            exact &= sorted(c.form.value for c in result.candidates) == sorted(case["forms"])
        violations += not result.needs_confirmation
        groups[case["category"]].append(exact)
        cases.append({"example_id": case["id"], "category": case["category"], "exact": exact,
                      "expected": to_jsonable(expected), "result": to_jsonable(result)})
    return {"categories": {k: {"rows": len(v), "exact_match": sum(v) / len(v)} for k, v in groups.items()},
            "confirmation_violations": violations, "cases": cases}


def category_scores(rows, cases):
    """Score evaluation rows by annotated type, form contrast and gold command count."""
    groups = {key: [] for key in ("negation", "pronoun", "ellipsis", "multi_intent", "question_assertion", "alias")}
    forms = defaultdict(set)
    for row in rows:
        if row.contrast_group:
            forms[row.contrast_group].add(row.form)
    for row, result in zip(rows, cases, strict=True):
        kind = note_info(row.notes).get("type")
        if kind in {"alias", "unregistered_alias", "josa", "pronoun", "ellipsis"}:
            groups[kind if kind in {"pronoun", "ellipsis"} else "alias"].append(result["exact"])
        # shortcut: lexical negation until the evaluation rows carry explicit negation tags.
        if kind == "negation" or re.search(r"않|아니|없|못|지 말|(?:^|\s)안\s", row.text):
            groups["negation"].append(result["exact"])
        if kind == "question_assertion" or (
                "assertion" in forms[row.contrast_group] and forms[row.contrast_group] & {"question", "quote"}):
            groups["question_assertion"].append(result["exact"])
        gold = result["expected"]
        if isinstance(gold, list) and len(gold) > 1:
            groups["multi_intent"].append(result["exact"])
    return {key: {"rows": len(values), "exact_match": sum(values) / len(values) if values else None}
            for key, values in groups.items()}


def summarize(parser, rows, calls, seed, *, case=reference_case):
    """Add label/slot errors, unsupported false positives, confirmation violations and warm latency."""
    report = evaluate(parser, rows, case=case)
    cases = report["cases"]
    report["command_exact_match_ci95"] = (bootstrap_interval([c["exact"] for c in cases], rows, seed)
                                          if len(rows) < 200 else None)
    by_label = defaultdict(list)
    for result in cases:
        by_label[result["gold_label"]].append(result)
    report["labels"] = {label: {"rows": len(group), "label_errors": sum(c["gold_label"] != c["predicted_label"]
                              for c in group), "command_errors": sum(not c["exact"] for c in group)}
                        for label, group in by_label.items()}
    metrics = report["metrics"]
    unsupported = [c for c in cases if c["gold_label"] == "unsupported"]
    if unsupported:
        metrics["unsupported_false_positive_rate"] = {"value": sum(bool(c["result"]["candidates"])
            for c in unsupported) / len(unsupported), "unit": ""}
    violations = sum(not c["result"]["needs_confirmation"] for c in cases)
    metrics["confirmation_violations"] = {"value": violations, "unit": "건"}
    metrics["wrong_immediate_execution_rate"] = {"value": sum(not c["result"]["needs_confirmation"] and
        not c["exact"] for c in cases) / len(cases), "unit": ""}
    supported = []
    for item in cases:
        expected = item["expected"]
        if isinstance(expected, list) and len(expected) == 1:
            expected = expected[0]
        if isinstance(expected, dict):
            supported.append((item, expected))
    metrics["single_command_rows"] = {"value": len(supported), "unit": "문장"}
    for slot in ("kind", "addressee", "target", "evidence", "night", "slot", "divination", "interrogation_kind"):
        errors = 0
        for item, expected in supported:
            candidates = item["result"]["candidates"]
            actual = candidates[0]["command"] if len(candidates) == 1 else None
            left, right = actual.get(slot) if actual else None, expected[slot]
            if slot == "evidence":
                left = sorted((r["type"], r["record_id"]) for r in left) if left is not None else None
                right = sorted((r["type"], r["record_id"]) for r in right)
            errors += actual is None or left != right
        if supported:
            metrics[f"slot_{slot}_error_rate"] = {"value": errors / len(supported), "unit": ""}
    contrast = defaultdict(list)
    for row, result in zip(rows, cases):
        if row.contrast_group:
            contrast[row.contrast_group].append(result)
    pairs = [group for group in contrast.values() if len({c["gold_label"] for c in group}) > 1]
    metrics["contrast_groups"] = {"value": len(pairs), "unit": "개"}
    if pairs:
        metrics["contrast_accuracy"] = {"value": sum(all(c["exact"] for c in group) for group in pairs) / len(pairs), "unit": ""}
    samples = []
    inputs = [case(row)[:2] for row in rows]
    # evaluate() above warms the parser; construction/loading time is excluded.
    for i in range(calls):
        text, ctx = inputs[i % len(inputs)]
        start = time.perf_counter()
        parser.parse(text, ctx)
        samples.append((time.perf_counter() - start) * 1000)
    for p in (50, 95):
        metrics[f"parse_p{p}"] = {"value": percentile(samples, p), "unit": "ms"}
    metrics["parse_calls"] = {"value": calls, "unit": "회"}
    report["counterexamples"] = evaluate_counterexamples(parser)
    report["evaluation_categories"] = category_scores(rows, cases)
    return report


def load_human_cases(path, rows):
    """Join frozen annotations to their public parse context and independent full gold commands."""
    cases = {}
    rows_by_id = {row.example_id: row for row in rows}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        key = item["example_id"]
        if key not in rows_by_id or key in cases:
            raise ValueError("unexpected or duplicate context ID")
        ctx = from_jsonable(ParseContext, item["context"])
        row = rows_by_id[key]
        commands = from_jsonable(list[Command], item["commands"])
        if (ctx.game_id != row.split_key.game_id or
                (not commands and row.label != "unsupported") or
                (commands and candidate_label(commands[0]) != row.label) or
                any(c.actor != PLAYER_ID or missing_slots(c) for c in commands)):
            raise ValueError("context or command annotations do not match the human row")
        cases[key] = row.text, ctx, commands
    if cases.keys() != rows_by_id.keys():
        raise ValueError("missing human evaluation contexts")
    return cases


def _match_attempts(rows, raw):
    by_id = {r["attempt_id"]: r for r in raw}
    if len(by_id) != len(raw) or set(by_id) != {r.example_id for r in rows}:
        raise ValueError("human annotations must cover exactly the exported attempt IDs")
    for row in rows:
        original = by_id[row.example_id]
        if (normalize(row.text) != normalize(original["text"]) or
                row.split_key.user != original["player_pseudonym"] or
                row.split_key.game_id != original["game_id"]):
            raise ValueError("human annotation differs from the export")


def validate_pm_smoke(path, rows):
    """Check five PM-only annotations against the original E-08-10 games export."""
    users = {row.split_key.user for row in rows}
    games = json.loads(Path(path).read_text(encoding="utf-8"))["games"]
    raw = sentences_of(games, users)
    if len(rows) != 5 or len(raw) != 5 or len(users) != 1 or not all(r["pm"] is True for r in raw):
        raise ValueError("PM smoke requires exactly five attempts from the one pretest PM")
    _match_attempts(rows, raw)


def validate_manifest(path, rows):
    """Verify the official ut_freeze bundle and join immutable sentences to human annotations."""
    path = Path(path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    names = {"test_human.jsonl", "train_candidates.jsonl", "assignment.json", "report.json"}
    entries = manifest["files"]
    if (manifest["freeze_version"] != FREEZE_VERSION or len(entries) != len(names) or
            {entry["name"] for entry in entries} != names):
        raise ValueError("invalid ut_freeze manifest")
    for entry in entries:
        file = path.parent / entry["name"]
        if file.stat().st_size != entry["bytes"] or _sha(file) != entry["sha256"]:
            raise ValueError("frozen file changed")
    report = json.loads((path.parent / "report.json").read_text(encoding="utf-8"))
    assignment = json.loads((path.parent / "assignment.json").read_text(encoding="utf-8"))
    if (report["ok"] is not True or report["user_overlap"] != 0 or
            report["sentence_conflicts"] != report["conflicts_dropped"] or
            set(assignment["eval_users"]) & set(assignment["train_users"])):
        raise ValueError("freeze overlap checks failed")
    def jsonl(name):
        return [json.loads(line) for line in (path.parent / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    raw = jsonl("test_human.jsonl")
    _match_attempts(rows, raw)
    train_texts = {normalize(r["text"]) for r in jsonl("train_candidates.jsonl")}
    if any(row.split_key.user in assignment["train_users"] or normalize(row.text) in train_texts for row in rows):
        raise ValueError("human annotation overlaps training")
    return {"freeze_version": FREEZE_VERSION, "manifest_sha256": _sha(path), "split_seed": report["seed"],
            "excluded_privacy_rows": report["privacy_excluded"],
            "pm_users": sorted({r["player_pseudonym"] for r in raw if r["pm"]})}


def main(argv=None) -> int:
    """Evaluate an explicit local run; write only aggregate metrics to tracked evidence."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True)
    ap.add_argument("--training-data", default="content/intent/examples_v1.jsonl")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--dev", action="store_true")
    group.add_argument("--human", metavar="JSONL")
    ap.add_argument("--manifest", help="E-09-02 freeze manifest for final human evaluation")
    ap.add_argument("--pm-smoke", metavar="GAMES_JSON", help="E-08-10 PM pretest export for a non-final smoke run")
    ap.add_argument("--contexts", help="frozen public ParseContext + gold commands JSONL, required with --human")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args(argv)
    if args.n < 1 or args.seed < 0 or args.threads < 1:
        ap.error("positive n/threads and nonnegative seed required")
    if (args.manifest and args.pm_smoke or bool(args.human) != bool(args.manifest or args.pm_smoke) or
            bool(args.human) != bool(args.contexts)):
        ap.error("--human requires --contexts and exactly one of --manifest or --pm-smoke")
    mode = "test_human" if args.manifest else "pm_smoke" if args.pm_smoke else "dev"
    source = "python -m tools.perf.intent_human_eval " + shlex.join(sys.argv[1:] if argv is None else argv)
    record, _, tokenizer_version = checkpoint_record(args.run)
    training = load_examples(args.training_data)
    if record["data_version"] != _sha(args.training_data):
        raise ValueError("training dataset differs from checkpoint provenance")
    ids = set(record["train_ids"])
    train = [r for r in training if r.example_id in ids]
    if len(train) != len(ids) or ids & set(record["dev_ids"]):
        raise ValueError("invalid checkpoint training IDs")
    manifest = None
    case = reference_case
    if args.human:
        rows = load_examples(args.human)
        validate_human(rows, [*training, *load_examples()])
        if args.manifest:
            manifest = validate_manifest(args.manifest, rows)
        else:
            validate_pm_smoke(args.pm_smoke, rows)
        human_cases = load_human_cases(args.contexts, rows)
        def case(row):
            return human_cases[row.example_id]
    else:
        dev_ids = set(record["dev_ids"])
        rows = [r for r in training if r.example_id in dev_ids]
        if len(rows) != len(dev_ids):
            raise ValueError("checkpoint dev IDs absent")
    log("intent_human_eval", "info", "evaluation inputs verified", mode=mode,
        rows=len(rows), data_version=_sha(args.human or args.training_data))
    baseline = BaselineIntentParser() if args.human else BaselineIntentParser(IntentBaseline().fit(train))
    reports = {"baseline": summarize(baseline, rows, args.n, args.seed, case=case)}
    unavailable = None
    try:
        import torch
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        bert = BertIntentParser(args.run)
    except (OSError, ImportError, RuntimeError, ValueError) as exc:
        unavailable = type(exc).__name__
        log("intent_human_eval", "error", "BERT runtime unavailable", error_type=unavailable)
    else:
        reports["bert"] = summarize(bert, rows, args.n, args.seed, case=case)
    accepted = {"passed": False, "reasons": ["BERT_runtime_unavailable"], "production_action": "keep_baseline"}
    if "bert" in reports:
        b, base = reports["bert"], reports["baseline"]
        accepted = decision(b["metrics"]["command_exact_match"]["value"], base["metrics"]["command_exact_match"]["value"],
            b["command_exact_match_ci95"], base["command_exact_match_ci95"], len(rows), human=bool(args.manifest))
        if args.pm_smoke:
            accepted["reasons"][0] = "pm_smoke_only"
        if b["metrics"]["confirmation_violations"]["value"] or b["counterexamples"]["confirmation_violations"]:
            accepted["passed"] = False
            accepted["reasons"].append("confirmation_required")
    pm_excluded = {}
    if manifest and manifest["pm_users"]:
        keep = [r for r in rows if r.split_key.user not in manifest["pm_users"]]
        for name, parser in (("baseline", baseline), ("bert", bert if "bert" in reports else None)):
            if parser and keep:
                subset = evaluate(parser, keep, case=case)
                if len(keep) < 200:
                    subset["command_exact_match_ci95"] = bootstrap_interval([c["exact"] for c in subset["cases"]], keep, args.seed)
                pm_excluded[name] = subset
        if not keep:
            pm_excluded["status"] = "no_non_pm_rows"
    metrics = {f"{tag}_{key}": value for tag, report in reports.items() for key, value in report["metrics"].items()}
    metrics["bert_available"] = {"value": int("bert" in reports), "unit": ""}
    metrics["checkpoint_weights_verified"] = {"value": 1, "unit": ""}
    metrics["human_evaluation"] = {"value": int(bool(args.manifest)), "unit": ""}
    metrics["accepted"] = {"value": int(accepted["passed"]), "unit": ""}
    for tag, report in reports.items():
        for category, group_report in report["counterexamples"]["categories"].items():
            metrics[f"{tag}_{category}_accuracy"] = {"value": group_report["exact_match"], "unit": ""}
            metrics[f"{tag}_{category}_rows"] = {"value": group_report["rows"], "unit": "문장"}
        for category, score in report["evaluation_categories"].items():
            metrics[f"{tag}_split_{category}_rows"] = {"value": score["rows"], "unit": "문장"}
            if score["rows"]:
                metrics[f"{tag}_split_{category}_accuracy"] = {"value": score["exact_match"], "unit": ""}
        ci = report["command_exact_match_ci95"]
        if ci:
            for bound, value in zip(("low", "high"), ci):
                metrics[f"{tag}_exact_ci95_{bound}"] = {"value": value, "unit": ""}
    run_dir = ROOT / "mlruns/intent_human_eval" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir.mkdir(parents=True)
    provenance = {"task": "D-09-01", "commit": GitRepo(ROOT).head(), "command": source,
        "source_sha256": hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "intent").glob("*.py")))).hexdigest(),
        "data_version": _sha(args.human or args.training_data), "training_data_version": _sha(args.training_data),
        "feature_version": record["feature_version"], "tokenizer_version": tokenizer_version,
        "checkpoint_model_version": record["model_version"],
        "weights_sha256": record["weights_sha256"], "checkpoint_training_commit": record["commit"],
        "mode": mode, "python": platform.python_version(),
        "seed": args.seed, "threads": args.threads, "bootstrap_repeats": 2000,
        "bootstrap_unit": "connected user/game/lineage/contrast group", "unavailable": unavailable,
        "manifest": {k: v for k, v in manifest.items() if k != "pm_users"} if manifest else None,
        "contexts_sha256": _sha(args.contexts) if args.contexts else None,
        "pm_export_sha256": _sha(args.pm_smoke) if args.pm_smoke else None,
        "baseline_model_version": baseline.model.model_version,
        "decision": accepted, "reports": reports, "pm_excluded": pm_excluded}
    (run_dir / "run.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_name = {"test_human": "human", "pm_smoke": "pm_smoke", "dev": "development"}[mode]
    result = write_result("D-09-01", result_name, metrics, source)
    log("intent_human_eval", "info", "evaluation recorded", model_version=record["model_version"],
        passed=accepted["passed"], reasons=accepted["reasons"], result=str(result.relative_to(ROOT)),
        run=str(run_dir.relative_to(ROOT)))
    return 2 if unavailable else 0


if __name__ == "__main__":
    raise SystemExit(main())
