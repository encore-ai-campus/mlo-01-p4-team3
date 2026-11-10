"""Read hourly human event totals from one Delta bronze snapshot."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

from pipeline.spark.ingest_bronze import load_config, write_audit
from tools.gitrepo import GitRepo
from tools.ut_freeze import DEFAULT_CUTOFF, DEFAULT_OPEN, KST

EVENTS = ("intent_attempt", "survey_submitted", "notebook_updated", "ad.*")


def main(argv=None):
    """Report complete test hours and the Delta version read, with zeroes for missing types."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    start = datetime.fromisoformat(DEFAULT_OPEN)
    cutoff = datetime.fromisoformat(DEFAULT_CUTOFF)
    checked = datetime.now(KST)
    end = min(checked.replace(minute=0, second=0, microsecond=0), cutoff)
    if end <= start:
        parser.error("no completed formal test hour yet")
    config = load_config(args.config)
    from delta.tables import DeltaTable
    from pyspark.sql import SparkSession, functions as F

    builder = SparkSession.builder.appName("paldo-ut-bronze-counts")
    for key, value in config["spark"].items():
        builder = builder.config(key, value)
    spark = builder.config("spark.sql.session.timeZone", "UTC").getOrCreate()
    try:
        path = config.get("official_bronze_path", config["bronze_path"])
        version = DeltaTable.forPath(spark, path).history(1).first().version
        frame = spark.read.format("delta").option("versionAsOf", version).load(path)
        timestamp = F.to_timestamp("occurred_at")
        ad = F.col("topic").startswith("ad.")
        totals = (frame.where((F.col("source") == "human") & ~F.col("load_test")
                               & (timestamp >= F.lit(start.isoformat()).cast("timestamp"))
                               & (timestamp < F.lit(end.isoformat()).cast("timestamp"))
                               & (F.col("event_type").isin(*EVENTS[:-1]) | ad))
                  .select(F.hour(F.from_utc_timestamp(timestamp, "Asia/Seoul")).alias("hour"),
                          F.when(ad, F.lit("ad.*")).otherwise(F.col("event_type")).alias("kind"))
                  .groupBy("hour", "kind").count().collect())
    finally:
        spark.stop()
    observed = {(row.hour, row.kind): row["count"] for row in totals}
    hours = [{"start_kst": (start + timedelta(hours=i)).isoformat(),
              **{kind: observed.get((start.hour + i, kind), 0) for kind in EVENTS}}
             for i in range(int((end - start).total_seconds() // 3600))]
    report = {"data_version": f"delta:official-bronze@{version}", "feature_version": "ut-hourly-v1",
              "code_commit": GitRepo().head(), "checked_kst": checked.isoformat(), "hours": hours}
    write_audit(args.out, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
