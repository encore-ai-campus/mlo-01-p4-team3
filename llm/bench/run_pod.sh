#!/usr/bin/env bash
# 인프라: RunPod Pod 안에서 실험 작업을 타이머로 감싸고, 끝나면 결과 반출 → 자기 삭제(E-08-13 6번, E-11-12 7번 ②).
#
#   STAGE=s1 MAX_MINUTES=90 EXPORT_CMD='<반출 명령>' bash llm/bench/run_pod.sh <작업 명령...>
#
# - STAGE(s0|s1|s2)별 상한(s0 20, s1 90, s2 90분)을 넘는 MAX_MINUTES, 정수가 아닌 값은 거부(exit 2).
# - 작업이 먼저 끝나거나 MAX_MINUTES가 다 되면(작업 중단) EXPORT_CMD를 `timeout 60`으로 실행한다(실패해도 삭제는 진행).
# - 그다음 `runpodctl pod delete "$RUNPOD_POD_ID"`(정지가 아니라 삭제)를 최대 3회(5초 간격). 셋 다 실패하면 exit 3.
#   Pod 범위 키로 자기 삭제가 되는지는 미확인(0단계 확인). 실패해도 노트북의 watch_pods.py가 시간 초과로 지운다.
# - 로그(표준 출력·오류)는 key|token|authkey 가 들어간 값을 지워 내보낸다. 환경 변수 값은 출력하지 않는다.
# - 테스트용: RUNPODCTL(가짜 명령 경로), TICK_SECONDS(1분을 몇 초로 셀지, 기본 60), RETRY_SLEEP(기본 5).
set -u

RUNPODCTL="${RUNPODCTL:-runpodctl}"
TICK_SECONDS="${TICK_SECONDS:-60}"
RETRY_SLEEP="${RETRY_SLEEP:-5}"
STAGE="${STAGE:-}"
MAX_MINUTES="${MAX_MINUTES:-}"

redact() { sed -E 's/((key|token|authkey|KEY|TOKEN|AUTHKEY)[A-Za-z_]*[=: ]+)[^ ]+/\1***/g'; }
say() { printf '{"component":"run_pod","msg":"%s"}\n' "$1"; }

case "$STAGE" in
  s0) CAP=20 ;;
  s1|s2) CAP=90 ;;
  *) say "STAGE는 s0|s1|s2"; exit 2 ;;
esac
if ! [[ "$MAX_MINUTES" =~ ^[0-9]+$ ]] || [ "$MAX_MINUTES" -lt 1 ] || [ "$MAX_MINUTES" -gt "$CAP" ]; then
  say "MAX_MINUTES는 1~${CAP} 정수(${STAGE})"; exit 2
fi
if [ -z "${RUNPOD_POD_ID:-}" ]; then
  say "RUNPOD_POD_ID 없음"; exit 2
fi
if [ "$#" -eq 0 ]; then
  say "작업 명령이 없음"; exit 2
fi

LIMIT_S=$(( MAX_MINUTES * TICK_SECONDS ))
say "작업 시작(상한 ${MAX_MINUTES}분)"
"$@" > >(redact) 2> >(redact >&2) &
JOB=$!
ELAPSED=0
while kill -0 "$JOB" 2>/dev/null; do
  if [ "$ELAPSED" -ge "$LIMIT_S" ]; then
    say "시간 초과: 작업 중단"
    kill "$JOB" 2>/dev/null; sleep 1; kill -9 "$JOB" 2>/dev/null
    break
  fi
  sleep 1
  ELAPSED=$(( ELAPSED + 1 ))
done
wait "$JOB" 2>/dev/null
say "작업 끝(경과 ${ELAPSED}초)"

if [ -n "${EXPORT_CMD:-}" ]; then
  if timeout 60 bash -c "$EXPORT_CMD" 2>&1 | redact; then say "반출 완료"; else say "반출 실패(삭제는 진행)"; fi
else
  say "EXPORT_CMD 없음: 반출 건너뜀"
fi

for i in 1 2 3; do
  if "$RUNPODCTL" pod delete "$RUNPOD_POD_ID" > /dev/null 2>&1; then
    say "자기 삭제 요청 성공(${i}회째)"; exit 0
  fi
  say "자기 삭제 실패(${i}회째)"
  [ "$i" -lt 3 ] && sleep "$RETRY_SLEEP"
done
say "자기 삭제 3회 실패: watch_pods.py 또는 콘솔에서 삭제"
exit 3
