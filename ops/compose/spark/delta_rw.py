"""Spark + Delta 조합 확인(C-05-03 조건 3): Delta 테이블 쓰기 → 추가 → 읽기 → 이전 버전 읽기 1회.

컨테이너(apache/spark 이미지) 안에서 spark-submit으로 실행한다. 명령은 ops/compose/README.md "Spark + Delta 확인".
결과는 구조화 로그(JSON 한 줄)로 표준 출력에 남긴다.
"""
import json
import sys
import time
from datetime import datetime, timezone

from pyspark.sql import SparkSession


def log(level, msg, **fields):
    """구조화 로그 한 줄(README 5절 필수 필드)."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "level": level, "service": "devenv", "component": "delta_rw", "msg": msg}
    rec.update(fields)
    print(json.dumps(rec, ensure_ascii=False), flush=True)


def main(path):
    """`path`에 Delta 테이블을 만들고 다시 읽어 행 수·버전을 확인한다. 성공하면 0."""
    start = time.monotonic()
    spark = SparkSession.builder.appName("paldo-delta-rw").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    rows = [(i, f"game-{i % 3}", "smoke") for i in range(10)]
    df = spark.createDataFrame(rows, "seq INT, game_id STRING, source STRING")
    df.write.format("delta").mode("overwrite").save(path)
    spark.createDataFrame([(10, "game-1", "smoke")], df.schema).write.format("delta").mode("append").save(path)
    now = spark.read.format("delta").load(path)
    v0 = spark.read.format("delta").option("versionAsOf", 0).load(path)
    history = spark.sql(f"DESCRIBE HISTORY delta.`{path}`").select("version", "operation").collect()
    result = {
        "spark_version": spark.version,
        "rows_now": now.count(),
        "rows_v0": v0.count(),
        "versions": [(r.version, r.operation) for r in sorted(history, key=lambda r: r.version)],
        "by_game": {r.game_id: r["count"] for r in now.groupBy("game_id").count().collect()},
    }
    ok = result["rows_now"] == 11 and result["rows_v0"] == 10 and len(result["versions"]) == 2
    log("info" if ok else "error", "delta 쓰기·읽기 " + ("성공" if ok else "실패"),
        duration_ms=int((time.monotonic() - start) * 1000), **result, **({} if ok else {"error_type": "mismatch"}))
    spark.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/paldo_delta_smoke"))
