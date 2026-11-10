"""D 학습 명령을 순서대로 실행하고 D 관문을 통과한 산출물만 등록한다."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from pipeline.spark.privacy_filter import load_tombstones
from tools.ut_freeze import DEFAULT_CUTOFF, DEFAULT_OPEN, KST


MODULES = {"intent": "intent.retrain", "suspicion": "ml.suspicion.retrain", "ad_ctr": "ml.ad_ctr"}


def evaluate(family, report):
    """기존 D의 지표 관문을 같은 학습 보고서에 다시 적용한다."""
    from ml.gate import ad_ctr_gate, intent_gate, suspicion_gate

    if family == "intent":
        return intent_gate(report["candidate"], report["reference"])
    if family == "suspicion":
        return suspicion_gate(report["candidate"], report["reference"])
    return ad_ctr_gate(report["lightgbm"], report["beta"])


def validate(config):
    """세 모델의 D 진입점과 명시한 Delta 입력 버전을 검증한다."""
    if set(config["models"]) != set(MODULES):
        raise ValueError("three_model_families_required")
    if not re.fullmatch(r"[0-9a-f]{40}", config["code_commit"]):
        raise ValueError("full_code_commit_required")
    for family, spec in config["models"].items():
        if not re.fullmatch(r"delta:\S+@\d+", spec["data_version"]):
            raise ValueError("versioned_delta_input_required")
        if not spec["feature_version"] or not re.fullmatch(r"[A-Za-z0-9_.-]+", spec["model"]):
            raise ValueError("feature_and_model_name_required")
        command = spec["train"]
        if not isinstance(command, list) or command[:2] != ["-m", MODULES[family]]:
            raise ValueError("existing_d_training_entrypoint_required")
        if not all(isinstance(arg, str) and arg for arg in command):
            raise ValueError("invalid_training_arguments")
        if "--register" in command or "--registry-dir" in command:
            raise ValueError("candidate_registration_owned_by_runner")
        for field in ("report", "artifact"):
            path = Path(spec[field])
            if path.is_absolute() or not path.parts or ".." in path.parts or path.drive:
                raise ValueError("relative_output_required")
    load_tombstones(config["tombstones"])


def record_candidate(family, spec, report, artifact, config):
    """검증된 출처와 산출물을 MLflow에 기록하고 통과 실행만 candidate로 만든다."""
    import mlflow
    from ops.mlflow.gate import check_run, register_candidate

    decision = evaluate(family, report)
    provenance = report.get("versions", report)
    actual = provenance.get("data_version")
    passed = decision.accepted and actual == spec["data_version"]
    if load_tombstones(config["tombstones"]) != config["_tombstones"]:
        raise ValueError("privacy_registry_changed_during_training")
    tags = {"data_version": actual or "missing", "feature_version": spec["feature_version"],
            "code_commit": config["code_commit"]}
    version = report.get("candidate_version") or report.get("model_version")
    if family == "ad_ctr":
        version = spec["artifact_version"]
    if passed and (not isinstance(version, str) or not version):
        raise ValueError("artifact_version_required")
    if passed and (not artifact.exists() or artifact.is_symlink()):
        raise ValueError("trained_artifact_required")
    reason = "accepted" if passed else ("provenance" if actual != spec["data_version"] else "d_gate")
    experiment = mlflow.set_experiment("paldo-retrain")
    with mlflow.start_run(experiment_id=experiment.experiment_id, tags=tags) as active:
        run_id = active.info.run_id
        mlflow.log_dict(report, "decision.json")
        mlflow.set_tag("paldo.gate.reasons", json.dumps(decision.reasons))
        if passed:
            if artifact.is_dir():
                if any(p.is_symlink() for p in artifact.rglob("*")):
                    raise ValueError("artifact_symlink")
                mlflow.log_artifacts(str(artifact), "model")
            else:
                mlflow.log_artifact(str(artifact), "model")
    client = mlflow.MlflowClient()
    accepted = check_run(client, run_id, lambda _: (passed, reason))
    result = {"family": family, "run_id": run_id, "accepted": accepted}
    if accepted:
        source = client.get_run(run_id).info.artifact_uri.rstrip("/") + "/model"
        registered = register_candidate(client, run_id, spec["model"], "model", source_uri=source)
        client.set_model_version_tag(spec["model"], registered, "paldo.artifact_version", version)
        result["candidate_version"] = registered
    return result


def run(config, output):
    """세 모델을 차례로 실행하며 관문 거부는 등록 없이 결과로 남긴다."""
    validate(config)
    if datetime.fromisoformat(DEFAULT_OPEN) <= datetime.now(KST) < datetime.fromisoformat(DEFAULT_CUTOFF):
        raise ValueError("training_paused_during_formal_test")
    config["_tombstones"] = load_tombstones(config["tombstones"])
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    results = []
    for family in MODULES:
        spec = config["models"][family]
        destination = output / family
        command = [arg.replace("{output}", str(destination)) for arg in spec["train"]]
        if not any("{output}" in arg for arg in spec["train"]):
            raise ValueError("new_training_output_required")
        with (output / (family + ".log")).open("x", encoding="utf-8") as log:
            subprocess.run([sys.executable, *command], check=True, stdout=log, stderr=log,
                           timeout=config.get("timeout_seconds", 7200))
        report = json.loads((destination / spec["report"]).read_text(encoding="utf-8"))
        result = record_candidate(family, spec, report, destination / spec["artifact"], config)
        results.append(result)
        (output / "result.json").write_text(json.dumps(results) + "\n", encoding="utf-8")
    return results


def main(argv=None):
    """설정과 출력은 보호 경로를 쓰고 Job 재시도는 새 작업 경로에서 실행한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.config.is_symlink() or (os.name != "nt" and args.config.stat().st_mode & 0o007):
        parser.error("protected_retrain_config_required")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.check:
        validate(config)
        print(json.dumps({"families": list(MODULES), "configuration_valid": True}))
        return
    if args.out is None:
        parser.error("new_output_directory_required")
    os.umask(0o077)
    print(json.dumps(run(config, args.out)))


if __name__ == "__main__":
    main()
