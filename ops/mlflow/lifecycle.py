"""명시한 모델 하나의 관문 통과 후보를 전환하고 이전 버전으로 복구한다."""
import argparse
from datetime import datetime, timezone
import json
import re


def transition(client, name, action, expected, reason, *, expected_canary=None):
    """기대 운영·평가 후보 버전을 대조하고 그 명시한 버전만 전환한다."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", reason):
        raise ValueError("invalid_model_or_reason")
    if action not in {"canary", "promote", "rollback"}:
        raise ValueError("invalid_lifecycle_action")
    if action == "promote" and (expected_canary is None or not str(expected_canary)):
        raise ValueError("expected_canary_required")
    current = client.get_model_version_by_alias(name, "production")
    if str(current.version) != str(expected):
        raise ValueError("production_version_changed")
    alias = {"canary": "candidate", "promote": "canary", "rollback": "previous"}[action]
    target = client.get_model_version_by_alias(name, alias)
    if action == "promote" and str(target.version) != str(expected_canary):
        raise ValueError("canary_version_changed")
    if action != "rollback":
        run = client.get_run(target.run_id)
        if run.info.status != "FINISHED" or run.data.tags.get("paldo.gate.status") != "passed":
            raise ValueError("candidate_gate_not_passed")
    if action == "promote":
        # 관측 검사와 관문 조회 동안 별칭이 바뀌었으면 첫 쓰기 전에 중단한다.
        if str(client.get_model_version_by_alias(name, "canary").version) != str(expected_canary):
            raise ValueError("canary_version_changed")
        if str(client.get_model_version_by_alias(name, "production").version) != str(expected):
            raise ValueError("production_version_changed")
    # previous를 먼저 저장해 production 변경 뒤 실패해도 복구 대상을 잃지 않는다.
    if action == "promote" and str(current.version) != str(target.version):
        client.set_registered_model_alias(name, "previous", current.version)
    destination = "canary" if action == "canary" else "production"
    client.set_model_version_tag(name, target.version, "paldo.lifecycle.reason", reason)
    client.set_model_version_tag(name, target.version, "paldo.lifecycle.at", datetime.now(timezone.utc).isoformat())
    client.set_registered_model_alias(name, destination, target.version)
    observed = client.get_model_version_by_alias(name, destination)
    if str(observed.version) != str(target.version):
        raise ValueError("alias_verification_failed")
    if action in {"promote", "rollback"}:
        from mlflow.exceptions import MlflowException

        try:
            client.delete_registered_model_alias(name, "canary")
        except MlflowException as error:
            if "canary" in client.get_registered_model(name).aliases:
                raise
    return {"action": action, "model": name, "previous_production": str(current.version),
            "selected_version": str(target.version)}


def main(argv=None):
    """운영자가 관측과 현재 별칭을 확인한 뒤 모델 하나를 전환한다."""
    from mlflow import MlflowClient

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("canary", "promote", "rollback"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-production", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    if args.action == "promote":
        parser.error("promotion requires the cohort check in ops.mlflow.canary")
    print(json.dumps(transition(MlflowClient(), args.model, args.action, args.expected_production, args.reason)))


if __name__ == "__main__":
    main()
