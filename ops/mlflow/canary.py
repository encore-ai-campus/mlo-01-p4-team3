"""P-17 표본과 악화 신호를 검사한 뒤 운영자가 승격한다."""
import argparse
import json
import math
import os
from pathlib import Path

from ml.lifecycle_demo import canary_decision
from ops.mlflow.lifecycle import transition


def verdict(report):
    """실측 필드가 빠지거나 수정·취소율 검토 신호가 있으면 승격하지 않는다."""
    for name in ("canary_games", "control_games"):
        if type(report[name]) is not int or report[name] < 0:
            raise ValueError("invalid_cohort_size")
    for name in ("canary_win", "control_win", "fallback", "p95_ms"):
        value = report[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid_canary_measurement")
        if name != "p95_ms" and value > 1:
            raise ValueError("invalid_canary_ratio")
    decision = canary_decision(report["canary_games"], report["control_games"],
                              report["canary_win"], report["control_win"], report["fallback"], report["p95_ms"])
    if decision == "promote":
        if report["intent_edit_cancel"] == "unmeasurable":
            if report.get("source") != "bot":
                raise ValueError("human_intent_signal_required")
        elif report["intent_edit_cancel"] is not False:
            return "hold"
    return decision


def main(argv=None):
    """보호된 관측 보고서를 확인하고 명시한 카나리아 버전에만 적용한다."""
    from mlflow import MlflowClient

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-production", required=True)
    parser.add_argument("--expected-canary", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.report.is_symlink() or (os.name != "nt" and args.report.stat().st_mode & 0o077):
        parser.error("protected_canary_report_required")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    decision = verdict(report)
    client = MlflowClient()
    if str(client.get_model_version_by_alias(args.model, "canary").version) != args.expected_canary:
        parser.error("canary_version_changed")
    if args.apply and decision == "promote":
        result = transition(client, args.model, "promote", args.expected_production, "cohorts_passed")
    else:
        result = {"verdict": decision, "applied": False}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
