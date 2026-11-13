# PJ4 관측

기존 Prometheus·Grafana와 PJ3 수집 작업을 보존한다. 보호 인벤토리로 node_exporter 20개에 notebook·vm 라벨을 붙이고 기존 node mTLS 설정을 재사용한다. 웹·광고·outbox·3광고 worker·pipeline·lifecycle 지표는 `relay.py`의 고정 경로로만 수집한다. 수집기 IP 이외에는 403, 다른 경로는 404이며 외부 주소·userinfo·redirect를 허용하지 않는다. upstream은 loopback 또는 같은 호스트의 명시 bind 주소다. 앱 포트나 OS 신뢰 저장소는 바꾸지 않는다.

## 웹 프로세스별 지표

C-08-04 전환에서는 app-01의 기존 relay를 사용한다. 기존 `/web`을 유지하고 두 번째 경로를 추가한다. 두 경로의 `host_headers` 값은 웹의 `PALDO_PUBLIC_ORIGIN`에서 호스트를 읽어 설정한다. relay가 내부 요청을 만들면서 고정 Host를 넣으므로 웹 프로필의 `PALDO_EXTRA_ALLOWED_HOSTS`는 비워 둔다. 브라우저 출처만 Django에서 허용하고, 두 Daphne 포트는 loopback에 둔다.

| Prometheus job | relay 경로 | app-01 내부 upstream | process |
|---|---|---|---|
| `paldo-web` | `/web` | `http://127.0.0.1:8000/metrics` | `web-1` |
| `paldo-web-2` | `/web-2` | `http://127.0.0.1:8002/metrics` | `web-2` |

두 job은 같은 relay 주소와 기존 허용 IP 정책을 사용한다. 8002의 기동 확인 뒤 relay에 경로를 추가하고 두 경로가 200인지 확인한다. 기존 `paldo-web`에는 `process` 라벨만 더하고 `paldo-web-2`를 같은 설정에서 파생해 `metrics_path`와 `process`를 바꾼다. `promtool check config` 통과 후 reload하고, Prometheus에서 `up{job=~"paldo-web(-2)?"}` 두 값이 1인지 확인한다. 게임 API p95와 오류율은 두 프로세스를 합산하는 기존 쿼리를 사용한다. 8002를 장기간 중지할 때는 두 번째 scrape 설정도 내려 정상 운영과 장애를 구분한다.

`pipeline_exporter.py`는 Spark checkpoint **commits에 있는 batch**의 offsets와 Kafka high-water를 비교한다. Kafka consumer commit을 하지 않으며 아직 commit되지 않은 offset 파일을 사용하지 않는다. `ads_p25_audit`와 `ads_run`을 지정하면 해당 실행의 gold audit에서 source·잠정/확정별 지표를 내보낸다. 캠페인·귀속일별 소진율, 지면별·전체 채움률, 노출·클릭별 무효율을 사용하며 분모가 없으면 지표를 내보내지 않는다. `ads_state_exporter.py`는 자기 MySQL의 read-only transaction에서 예산 순액을 SELECT한다. 페이싱 편차는 균등 목표 기준이며 실제 D 목표 곡선은 D의 지표가 필요하다.

`lifecycle_exporter.py`는 Mongo의 공개 종료 결과와 최종 의도 반응 필드만 projection한다. 부하 판은 제외한다. 수정·취소율은 최종 reaction 중 edit/cancel 비율이며 반응이 없으면 해당 비율은 제공하지 않는다. canary 30완료 판 미만의 승률 경보는 평가하지 않는다. 모델 포트 wrapper는 반환값·예외를 보존하고 실제 버전·추론 지연을 기록한다. 현재 기준선 모델을 학습 모델로 표시하지 않는다.

`alerts.yml`은 JSON으로 쓴 유효 YAML이다. ML 수명주기 4신호, API p95 > 1초, outbox 증가 총 6개다. 검증한 뒤 reload한다. C는 탐지하고 D가 rollback을 결정·실행하며 사용자 테스트 중에는 E 승인이 필요하다.

```sh
promtool check rules ops/prometheus/alerts.yml
python -m unittest tests.ops.test_c08_observability -v
python -m ops.web_concurrency --profile "$PRIVATE_VALIDATION_PROFILE" --out "$PRIVATE_RESULT" --ca "$PRIVATE_CA" --concurrency 30 --hold-seconds 180 --execute
```

6개 JSON은 `ops/grafana/dashboards/`에 있다. Prometheus datasource 선택 변수로 환경에 맞추며 재import할 수 있다. P-31에 따라 runtime LLM은 꺼져 있고 큐 대기·생성 지연은 N/A로 표시한다. 값이 없을 때 0 성공값을 만들어 넣지 않는다.

30 활성 판 검증은 load_test=true인 새 합성 계정만 사용한다. HTTP 요청 상한 12를 명시하며 30개 HTTP 요청 동시 실행과 구별한다. 최초 burst는 MySQL 연결 상한 40 소진으로 실패했다. 영구 상한 변경은 적용하지 않았다. 최종 완주와 p95 실측은 증빙을 본다. 11/10에는 MySQL 세 번째 VM을 포함해 `paldo-nodes` 20대의 수집을 확인했다.
