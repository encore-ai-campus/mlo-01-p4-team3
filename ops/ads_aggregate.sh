#!/bin/sh
# 광고 하루 집계(ad-01에서 실행): silver(재판정 발행) → 대기 → ① 원장 내보내기 → ② Spark 서버로 전달 → ③ gold 확정 집계와
#  CTR 칸 내보내기(대조는 확정 마감이 지난 날만) → ④ 결과 회수 → ⑤ ad_update ctr. 어느 단계든 실패하면 ⑤를 하지 않아 광고 서버는 마지막 정상 CTR을 쓴다.
# 설정은 환경 변수(systemd EnvironmentFile)로 받는다. 비밀값은 출력하지 않는다.
#   ADS_SRC            광고 서버 소스 폴더(ads/manage.py가 있는 저장소 루트)
#   ADS_PYTHON         광고 서버 venv python
#   ADS_WORK           작업 폴더(기본 /var/lib/paldo-ads-aggregate)
#   SPARK_HOST         Spark 서버 접속 대상(user@host 또는 ssh 별칭)
#   SPARK_KEY          전달 전용 SSH 키 경로, SPARK_KNOWN_HOSTS 호스트 키 파일
#   SPARK_SRC          Spark 서버의 저장소 폴더, SPARK_WORK Spark 서버 작업 폴더
#   SPARK_SUBMIT       Spark 서버에서 쓸 spark-submit 앞부분(외부 TLS·JAR 옵션 포함, 서버 쪽 보호 래퍼 권장)
#   SPARK_AD_CONFIG    Spark 서버의 광고 보호 설정 JSON 경로
#   ADS_PUBLISH_REJUDGES  1이면 silver에서 재판정 발행(기본 1, 수동 절차와 같음)
#   ADS_REJUDGE_WAIT   재판정 발행 뒤 원장 내보내기까지 기다리는 초(기본 120, gold 재시도 전에도 같은 시간)
#   ADS_METRIC_FILE    마지막 성공 시각 지표 파일(Prometheus textfile, 기본 $ADS_WORK/paldo_ads_aggregate.prom)
# 사용: ads_aggregate.sh [--as-of-ms N] [--dry-run]
set -eu
AS_OF=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --as-of-ms) AS_OF="$2"; shift 2;;
    --dry-run) DRY=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
: "${ADS_WORK:=/var/lib/paldo-ads-aggregate}"
: "${ADS_PUBLISH_REJUDGES:=1}"
: "${ADS_REJUDGE_WAIT:=120}"
: "${ADS_METRIC_FILE:=$ADS_WORK/paldo_ads_aggregate.prom}"
[ -n "$AS_OF" ] || AS_OF=$(($(date +%s) * 1000))
case "$AS_OF" in *[!0-9]*|"") echo "as-of-ms must be digits" >&2; exit 2;; esac
RUN="run-$AS_OF"; LOCAL="$ADS_WORK/$RUN"
mkdir -p "$ADS_WORK"; chmod 700 "$ADS_WORK"

step() {  # step <이름> <ok|fail|skip> <초> [추가 JSON 조각]
  printf '{"msg":"ads_aggregate_step","run":"%s","as_of_ms":%s,"step":"%s","result":"%s","seconds":%s%s}\n' \
    "$RUN" "$AS_OF" "$1" "$2" "$3" "${4:+,$4}"
}
now() { date +%s; }
need() { for v in "$@"; do eval "x=\${$v:-}"; [ -n "$x" ] || { step config fail 0 "\"missing\":\"$v\""; exit 3; }; done; }

command -v flock >/dev/null || { step lock fail 0 '"reason":"flock_missing"'; exit 3; }
exec 9>"$ADS_WORK/aggregate.lock"
if ! flock -n 9; then step lock skip 0 '"reason":"already_running"'; exit 0; fi

need ADS_SRC ADS_PYTHON SPARK_HOST SPARK_KEY SPARK_KNOWN_HOSTS SPARK_SRC SPARK_WORK SPARK_SUBMIT SPARK_AD_CONFIG
SSH="ssh -i $SPARK_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$SPARK_KNOWN_HOSTS"
SCP="scp -O -q -i $SPARK_KEY -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$SPARK_KNOWN_HOSTS"
run() { if [ "$DRY" = 1 ]; then echo "DRY: $*" >&2; else "$@"; fi; }

fail() { step "$1" fail "$2" "\"next\":\"ctr_not_published\""; exit 1; }

mkdir -p "$LOCAL/in" "$LOCAL/out"; R="$SPARK_WORK/$RUN"
t=$(now)
run $SSH "$SPARK_HOST" "mkdir -p '$R/in' '$R/out' && chmod 700 '$R'" || fail prepare $(( $(now) - t ))
step prepare ok $(( $(now) - t ))

# silver를 먼저 돌려 재판정(환입 요청)을 발행하고, 환입이 원장에 반영될 시간을 둔 뒤 원장을 내보낸다.
t=$(now); REJ=""; [ "$ADS_PUBLISH_REJUDGES" = 1 ] && REJ="--publish-rejudges"
run $SSH "$SPARK_HOST" "cd '$SPARK_SRC' && $SPARK_SUBMIT pipeline/spark/ads_silver.py --config '$SPARK_AD_CONFIG' --as-of-ms $AS_OF $REJ"   || fail silver $(( $(now) - t ))
step silver ok $(( $(now) - t ))

export_send() {  # export_send <시도 번호>: ① 원장 내보내기 → ② Spark 서버로 전달
  t=$(now)
  if ! summary=$(cd "$ADS_SRC" && run "$ADS_PYTHON" ads/manage.py ad_export_ledger --out "$LOCAL/in"); then fail export_ledger $(( $(now) - t )); fi
  step export_ledger ok $(( $(now) - t )) "\"attempt\":$1,\"summary\":${summary:-null}"
  t=$(now)
  run $SCP "$LOCAL/in/ledger.json" "$LOCAL/in/budgets.json" "$SPARK_HOST:$R/in/" || fail send $(( $(now) - t ))
  step send ok $(( $(now) - t )) "\"attempt\":$1"
}
gold() {
  run $SSH "$SPARK_HOST" "cd '$SPARK_SRC' && $SPARK_SUBMIT pipeline/spark/ads_gold.py --config '$SPARK_AD_CONFIG' --as-of-ms $AS_OF --phase final --ledger '$R/in/ledger.json' --budgets '$R/in/budgets.json' --export-optimization '$R/out' --reconcile-closed-days"
}
[ "$ADS_PUBLISH_REJUDGES" = 1 ] && [ "$DRY" = 0 ] && sleep "$ADS_REJUDGE_WAIT"
export_send 1
t=$(now)
if ! gold; then
  # 대조 불일치(환입 반영 지연 등)일 수 있어 한 번만 원장을 다시 내보내 재시도한다.
  step gold fail $(( $(now) - t )) '"next":"retry_after_wait"'
  [ "$DRY" = 0 ] && sleep "$ADS_REJUDGE_WAIT"
  export_send 2
  t=$(now); gold || fail gold $(( $(now) - t ))
fi
step gold ok $(( $(now) - t ))

t=$(now)
run $SCP "$SPARK_HOST:$R/out/ctr_cells.json" "$LOCAL/out/" || fail fetch $(( $(now) - t ))
step fetch ok $(( $(now) - t ))

t=$(now)
if [ "$DRY" = 1 ]; then cells=0; else
  cells=$("$ADS_PYTHON" -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["rows"]))' "$LOCAL/out/ctr_cells.json") || fail publish $(( $(now) - t ))
fi
(cd "$ADS_SRC" && run "$ADS_PYTHON" ads/manage.py ad_update ctr --input "$LOCAL/out/ctr_cells.json") || fail publish $(( $(now) - t ))
step publish ok $(( $(now) - t )) "\"ctr_cells\":$cells"

if [ "$DRY" = 0 ]; then
  tmp="$ADS_METRIC_FILE.tmp"
  printf '# HELP paldo_ads_aggregate_last_success_seconds 광고 하루 집계 마지막 성공 시각\n# TYPE paldo_ads_aggregate_last_success_seconds gauge\npaldo_ads_aggregate_last_success_seconds %s\npaldo_ads_aggregate_last_as_of_ms %s\npaldo_ads_aggregate_last_ctr_cells %s\n' \
    "$(now)" "$AS_OF" "$cells" > "$tmp" && mv "$tmp" "$ADS_METRIC_FILE"
  # 최근 7회만 남긴다.
  ls -1d "$ADS_WORK"/run-* 2>/dev/null | sort | head -n -7 | xargs -r rm -rf
fi
step done ok 0
