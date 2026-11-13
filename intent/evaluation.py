"""Held-out seed evaluation with a fixed, explicitly documented public reference context."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
from importlib.metadata import version
from pathlib import Path

from tools.evidence_result import write_result
from tools.jsonlog import log
from tools.perf.engine_bench import percentile

from sklearn.metrics import f1_score, recall_score

from contracts.datasets import IntentExample
from engine.domain.model import (PLAYER_ID, Claim, ClaimType, Command, CommandKind, DivinationPayload,
                                 DivinationResult, EvidenceRef, EvidenceType, InterrogationKind, Phase,
                                 Place, PublicRecord, Slot, Speech)
from engine.domain.serde import to_jsonable
from engine.ports import IntentParser, ParseContext, ParseFailure
from intent.parser import BaselineIntentParser
from tools.intent_examples import note_info

_PEOPLE = {"<P1>": "hunjang", "<P2>": "jumo"}
_NAMES = {"<P1>": "훈장", "<P2>": "주모"}
_INTERROGATIONS = {"interrogate_whereabouts": InterrogationKind.WHEREABOUTS,
                   "interrogate_contradiction": InterrogationKind.CONTRADICTION,
                   "interrogate_suspicion_reason": InterrogationKind.SUSPICION_REASON}


def reference_case(row: IntentExample) -> tuple[str, ParseContext, Command | None]:
    """Expand seed gold slots under one public scenario, independent of the predicted label.

    P1=hunjang, P2=jumo; default selected=P1, previous speaker=P2, latest public night=0,
    two selected public claims. Authored notes.ctx overrides only the supplied public context,
    identically for all classifiers. Gold slots are never used to choose that context.
    Seed omissions remain missing except the last-night divination default and literal '어젯밤'.
    """
    aliases = {"hunjang": ["훈장"], "jumo": ["주모"], "meosum": ["머슴"]}
    public = PublicRecord(claims=[
        Claim("eval_c1", "hunjang", ClaimType.WHEREABOUTS, "hunjang", 0, Slot.MIDNIGHT, Place.SHRINE),
        Claim("eval_c2", "jumo", ClaimType.NON_SIGHTING, "jumo", 0, Slot.MIDNIGHT, Place.SHRINE),
    ], speeches=[Speech("eval_s1", 1, Phase.ROUND1, "jumo", CommandKind.SUSPECT, target="hunjang")])
    cards = [EvidenceRef(EvidenceType.CLAIM, "eval_c1"), EvidenceRef(EvidenceType.CLAIM, "eval_c2")]
    ctx = ParseContext("intent_eval", 1, Phase.ROUND1, public, aliases, "hunjang", cards, "jumo")
    hint = note_info(row.notes).get("ctx", "")
    if hint:
        kind, _, who = hint.partition(":")
        if who not in _PEOPLE:
            raise ValueError("unknown evaluation context person")
        person = _PEOPLE[who]
        if kind == "selected_npc":
            ctx.selected_npc, ctx.last_addressee = person, None
        elif kind == "last_addressee":
            ctx.selected_npc, ctx.last_addressee = None, person
        elif kind == "quoted_speaker":
            ctx.last_addressee = person
            public.speeches[0].speaker = person
        else:
            raise ValueError("unknown evaluation context hint")
    text = row.text
    for key, name in _NAMES.items():
        text = text.replace(key, name)
    if row.label == "unsupported":
        return text, ctx, None
    cmd = Command(CommandKind.INTERROGATE if row.label in _INTERROGATIONS else CommandKind(row.label), PLAYER_ID)
    slots = row.slots
    if row.label in ("suspect", "defend"):
        cmd.target = _PEOPLE[slots["target"]]
    elif row.label in _INTERROGATIONS:
        cmd.addressee = _PEOPLE[slots["addressee"]]
        cmd.interrogation_kind = _INTERROGATIONS[row.label]
        if "mentioned" in slots:
            cmd.target = _PEOPLE[slots["mentioned"]]
        if row.label == "interrogate_whereabouts":
            cmd.slot = Slot(slots["slot"]) if "slot" in slots else None
            cmd.night = 0 if slots.get("night") == -1 or "어젯밤" in text else slots.get("night")
    elif row.label == "reveal_divination":
        cmd.divination = DivinationPayload(_PEOPLE[slots["target"]], 0, DivinationResult(slots["result"]))
    if slots.get("claims") == 2:
        cmd.evidence = cards
    return text, ctx, cmd


def command_equal(actual: Command, expected: Command) -> bool:
    """Compare all command fields, treating evidence ordering as immaterial."""
    left, right = to_jsonable(actual), to_jsonable(expected)
    for command in (left, right):
        command["evidence"] = sorted(command["evidence"], key=lambda ref: (ref["type"], ref["record_id"]))
    return left == right


def candidate_label(command: Command) -> str:
    """Map one resolved command back to the ten-label dataset vocabulary."""
    return f"interrogate_{command.interrogation_kind.value}" if command.kind is CommandKind.INTERROGATE else command.kind.value


def commands_equal(actual: list[Command], expected: list[Command]) -> bool:
    """Compare unordered commands one to one, counting repeated commands separately."""
    unmatched = list(actual)
    for command in expected:
        index = next((i for i, candidate in enumerate(unmatched) if command_equal(candidate, command)), None)
        if index is None:
            return False
        unmatched.pop(index)
    return not unmatched


def evaluate(parser: IntentParser, rows: list[IntentExample], *, case=reference_case) -> dict:
    """Measure macro F1, exact whole-command match and unsupported recall on supplied holdout rows."""
    if not rows:
        raise ValueError("evaluation rows are empty")
    gold, predictions, cases = [], [], []
    for row in rows:
        text, ctx, expected = case(row)
        result = parser.parse(text, ctx)
        predicted = (candidate_label(result.candidates[0].command) if result.candidates else
                     "unsupported" if result.failure is ParseFailure.UNSUPPORTED_INTENT else "unparseable")
        commands = [c.command for c in result.candidates]
        expected_commands = expected if isinstance(expected, list) else [expected] if expected else []
        exact = (commands_equal(commands, expected_commands) if expected_commands else
                 not commands and result.failure is ParseFailure.UNSUPPORTED_INTENT)
        gold.append(row.label)
        predictions.append(predicted)
        cases.append({"example_id": row.example_id, "gold_label": row.label, "predicted_label": predicted,
                      "exact": exact, "expected": to_jsonable(expected), "result": to_jsonable(result)})
    labels = sorted(set(gold))
    metrics = {
        "macro_f1": {"value": float(f1_score(gold, predictions, labels=labels, average="macro", zero_division=0)), "unit": ""},
        "command_exact_match": {"value": sum(case["exact"] for case in cases) / len(cases), "unit": ""},
        "unsupported_recall": {"value": float(recall_score([g == "unsupported" for g in gold],
                                                           [p == "unsupported" for p in predictions], zero_division=0)), "unit": ""},
        "evaluation_rows": {"value": len(rows), "unit": "문장"},
        "unsupported_rows": {"value": gold.count("unsupported"), "unit": "문장"},
    }
    return {"metrics": metrics, "cases": cases}


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    """Train on seed train groups, evaluate dev, and save model/run data outside Git."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    if args.n < 1:
        ap.error("--n must be positive")
    # External ML libraries belong to intent/, not the standard-library tools layer.
    from intent.baseline import IntentBaseline, SEED_PATH, TOKENIZER_VERSION, load_examples, split_examples
    from intent.parser import BaselineIntentParser

    rows = load_examples()
    train, dev = split_examples(rows, args.seed)
    log("intent_bench", "info", "학습 시작", train_rows=len(train), dev_rows=len(dev), seed=args.seed)
    model = IntentBaseline().fit(train)
    parser = BaselineIntentParser(model)
    report = evaluate(parser, dev)
    cases = [reference_case(row)[:2] for row in dev]
    for text, ctx in cases:
        parser.parse(text, ctx)
    samples = []
    for index in range(args.n):
        text, ctx = cases[index % len(cases)]
        start = time.perf_counter()
        parser.parse(text, ctx)
        samples.append((time.perf_counter() - start) * 1000)
    metrics = {**report["metrics"],
               "parse_p50": {"value": round(percentile(samples, 50), 6), "unit": "ms"},
               "parse_p95": {"value": round(percentile(samples, 95), 6), "unit": "ms"},
               "parse_calls": {"value": len(samples), "unit": "회"},
               "train_rows": {"value": len(train), "unit": "문장"},
               "lineage_overlap": {"value": len({r.split_key.lineage_root for r in train} &
                                                {r.split_key.lineage_root for r in dev}), "unit": "건"}}
    log("intent_bench", "info", "평가 완료", model_version=model.model_version,
        **{key: value["value"] for key, value in metrics.items()})
    if not args.no_write:
        command = f"python -m tools.perf.intent_bench --n {args.n} --seed {args.seed}"
        result = write_result("D-05-01", "intent", metrics, command)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        run = {"task": "D-05-01", "commit": commit, "data_version": hashlib.sha256(SEED_PATH.read_bytes()).hexdigest(),
               "feature_version": TOKENIZER_VERSION, "model_version": model.model_version,
               "python": platform.python_version(),
               "packages": {name: version(name) for name in ("scikit-learn", "numpy", "scipy", "joblib")}, "seed": args.seed,
               "train_ids": [r.example_id for r in train], "dev_ids": [r.example_id for r in dev],
               "train_groups": sorted({r.split_key.lineage_root for r in train}),
               "dev_groups": sorted({r.split_key.lineage_root for r in dev}), **report}
        run_dir = ROOT / "mlruns" / "intent-v0" / model.model_version
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        model.save(run_dir / "model.joblib")
        log("intent_bench", "info", "결과 JSON 기록", result=str(result.relative_to(ROOT)),
            run=str(run_dir.relative_to(ROOT)), reference_context="intent.evaluation.reference_case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
