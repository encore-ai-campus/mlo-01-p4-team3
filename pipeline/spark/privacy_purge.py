"""보호 계획의 식별자로 Delta 개인 행을 삭제하고 이전 파일을 정리한다."""
import argparse
import json
import os
from pathlib import Path

from pipeline.spark.ingest_bronze import writer_lock


KINDS = {"bronze", "game_silver", "ad_silver"}


def row_filter(kind, scope):
    """테이블 스키마에 맞는 가명·원본 키 조건을 구성한다."""
    from pyspark.sql import functions as F

    if kind == "bronze":
        fields = [(F.col("game_id"), "game_ids"), (F.col("event_id"), "ad_event_ids")]
        fields += [(F.get_json_object("envelope_json", f"$.{field}"), key) for field, key in (
            ("player_pseudonym", "players"), ("session_pseudonym", "ad_sessions"), ("serve_id", "ad_serve_ids"),
            ("request_id", "ad_request_ids"), ("charge_id", "ad_charge_ids"),
            ("reserved_charge_id", "ad_charge_ids"), ("source_event_id", "ad_event_ids"),
        )]
    elif kind == "game_silver":
        fields = [(F.col("game_id"), "game_ids")]
    elif kind == "ad_silver":
        fields = [(F.col(field), key) for field, key in (
            ("event_id", "ad_event_ids"), ("serve_id", "ad_serve_ids"), ("request_id", "ad_request_ids"),
            ("online_charge_id", "ad_charge_ids"), ("expected_charge_id", "ad_charge_ids"),
        )]
    else:
        raise ValueError("unknown_privacy_delta_kind")
    condition = F.lit(False)
    for field, key in fields:
        values = scope.get(key, [])
        if not isinstance(values, list) or len(values) > 10000 or any(not isinstance(v, str) or not v for v in values):
            raise ValueError("invalid_privacy_scope")
        if values:
            condition = condition | field.isin(values)
    return condition


def run_purge(spark, targets, scope, *, vacuum_hours=None, apply=False):
    """모든 테이블을 먼저 점검하고 논리 삭제 후 0건을 확인한다."""
    from delta.tables import DeltaTable

    if not targets or len({row["path"] for row in targets}) != len(targets):
        raise ValueError("explicit_distinct_delta_targets_required")
    planned = []
    for row in targets:
        if row["kind"] not in KINDS:
            raise ValueError("invalid_privacy_delta_kind")
        table = DeltaTable.forPath(spark, row["path"])
        condition = row_filter(row["kind"], scope)
        planned.append((row["kind"], table, condition, table.toDF().where(condition).count()))
    results = []
    for kind, table, condition, before in planned:
        if apply and before:
            table.delete(condition)
        remaining = table.toDF().where(condition).count() if apply else before
        if apply and remaining:
            raise ValueError("privacy_delta_rows_remaining")
        results.append({"kind": kind, "before": before, "after": remaining})
    if vacuum_hours is not None:
        for (_, table, _, _), result in zip(planned, results):
            table.vacuum(vacuum_hours).collect()
            result["vacuum_completed"] = True
    return results


def protected_json(path):
    """운영 계획과 인증 설정은 읽기 권한이 제한된 파일에서만 읽는다."""
    if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o007):
        raise ValueError("privacy_protected_json_required")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None):
    """확인 없는 VACUUM과 운영 쓰기를 막고 집계 결과만 출력한다."""
    from pyspark.sql import SparkSession

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-drained", action="store_true")
    parser.add_argument("--tombstone-registered", action="store_true")
    parser.add_argument("--vacuum-hours", type=int)
    parser.add_argument("--short-retention-approved", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not (args.writers_drained and args.tombstone_registered):
        parser.error("privacy_writer_drain_and_tombstone_required")
    if args.vacuum_hours is not None and (
        not args.apply or args.vacuum_hours < 0 or (args.vacuum_hours < 168 and not args.short_retention_approved)
    ):
        parser.error("privacy_vacuum_not_approved")
    config, scope = protected_json(args.config), protected_json(args.scope)
    builder = SparkSession.builder.appName("paldo-privacy-purge")
    for key, value in config["spark"].items():
        builder = builder.config(key, value)
    if args.vacuum_hours is not None and args.vacuum_hours < 168:
        builder = builder.config("spark.databricks.delta.retentionDurationCheck.enabled", "false")
    with writer_lock(config["lock_path"]):
        spark = builder.getOrCreate()
        try:
            print(json.dumps(run_purge(spark, config["privacy_delta_targets"], scope,
                                       vacuum_hours=args.vacuum_hours, apply=args.apply)))
        finally:
            spark.stop()


if __name__ == "__main__":
    main()
