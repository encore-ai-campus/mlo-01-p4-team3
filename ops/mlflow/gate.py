"""검증한 학습 실행만 MLflow 레지스트리 candidate로 등록한다."""
import argparse
from importlib import import_module
import json
import re


def check_run(client, run_id, evaluate):
    """Delta 버전·특징 버전·커밋을 확인하고 D 평가의 결과를 기록한다."""
    run = client.get_run(run_id)
    tags = run.data.tags
    if (run.info.status != "FINISHED" or not re.fullmatch(r"delta:\S+", tags.get("data_version", ""))
            or not tags.get("feature_version", "").strip()
            or not re.fullmatch(r"[0-9a-f]{40}", tags.get("code_commit", ""))):
        passed, reason = False, "required_run_metadata"
    else:
        passed, reason = evaluate(run)
        if type(passed) is not bool or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", reason):
            raise ValueError("invalid_evaluation_result")
    client.set_tag(run_id, "paldo.gate.status", "passed" if passed else "rejected")
    client.set_tag(run_id, "paldo.gate.reason", reason)
    return passed


def lookup_model(client, name, alias):
    """지정 별칭의 MLflow 모델 버전과 source를 돌려준다."""
    if alias not in {"candidate", "canary", "production", "previous"}:
        raise ValueError("invalid_model_alias")
    result = client.get_model_version_by_alias(name, alias)
    return {"version": str(result.version), "source": result.source, "run_id": result.run_id}


def main(argv=None):
    """평가 콜백을 실행하고 통과 실행에만 candidate 별칭을 부여한다."""
    from mlflow import MlflowClient, register_model

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--evaluator", required=True, help="module:function, (bool, reason_code) 반환")
    args = parser.parse_args(argv)
    module, separator, name = args.evaluator.partition(":")
    if not separator or not all(part.isidentifier() for part in module.split(".")) or not name.isidentifier():
        parser.error("invalid_evaluator")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*", args.artifact):
        parser.error("invalid_artifact_path")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", args.model):
        parser.error("invalid_model_name")
    client = MlflowClient()
    if not check_run(client, args.run_id, getattr(import_module(module), name)):
        print(json.dumps({"registered": False}))
        return 1
    source = f"runs:/{args.run_id}/{args.artifact}"
    existing = [version for version in client.search_model_versions(f"name = '{args.model}'")
                if version.run_id == args.run_id and version.source.rstrip("/").endswith("/" + args.artifact)]
    model = min(existing, key=lambda version: int(version.version)) if existing else register_model(source, args.model)
    client.set_registered_model_alias(args.model, "candidate", model.version)
    print(json.dumps({"registered": True, "version": str(model.version)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
