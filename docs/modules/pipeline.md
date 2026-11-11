# 파이프라인

## 무엇을

인프라 배치 계층이다. 공식 게임 6개·광고 4개 토픽을 Kafka에서 읽어 Delta bronze에 ID/hash 기반 MERGE한다. `pipeline/spark/bronze_contract.py`는 기존 게임 EventEnvelope·광고 serde를 검증한다. `build_silver.py`는 관찰별 후보 TrainingRow를 만들고 `join_labels.py`는 정상 종료 뒤 truth만 라벨로 붙인다. 학습 export는 마을 관찰자·라벨 있음·부하 아님 조건이며 source를 유지한다. `tools/reconcile_game_events.py`와 `pipeline/spark/export_faults.py`는 실제 장애 후 Mongo/Kafka/bronze ID·seq·hash를 대조한다.

## 왜

전송 offset만 저장하면 동일 이벤트 재발행이 중복 학습으로 이어진다. event_id와 내용 hash를 함께 검사해 충돌은 실패시키고 정상 재전송은 삽입하지 않는다. 진실은 특징이 아니라 정상 종료 뒤 정답으로만 사용한다. 발행 지연 중 게임 저장은 계속되어야 하므로 실제 브로커/발행기 정지와 재개 결과를 판별로 대조한다.

## 사용법

```sh
python -m unittest tests.contract.test_pipeline_bronze tests.contract.test_pipeline_silver -v
python -m unittest tests.contract.test_reconcile_events -v
python -m tools.reconcile_game_events --mongo "$PRIVATE_MONGO_METADATA" --published "$PRIVATE_KAFKA_METADATA" --bronze "$PRIVATE_BRONZE_METADATA" --out "$PRIVATE_RESULT"
```

Spark 실행·JAR·TLS·지속 서비스와 갱신 순서는 [pipeline/README.md](../../pipeline/README.md)를 따른다. 공식 런처의 bronze 서비스는 `pipeline/install_bronze_service.py`로 등록한다. 지속 bronze를 중지한 뒤 available-now → silver → labels → verify를 실행하고 재시작한다. writer lock·checkpoint는 삭제하지 않는다. C-07-04 HTTPS 안내·측정은 [ops/proxy/README.md](../../ops/proxy/README.md)를 따른다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| PALDO_PIPELINE_CONFIG | 없음 | 저장소 밖 보호 JSON | 주소·TLS·S3·인증 선택 |
| bronze_path / silver_path / training_export_path | 없음 | 분리된 Delta·TrainingRow JSON 경로 | 대상 데이터·D 인계 위치 변경 |
| checkpoint_path / lock_path | 없음 | 영속 offset·동일 호스트 단일 writer | 임의 삭제·변경 시 재처리·경합 |
| audit_dir | 없음 | offset 범위·Delta version·배치 수치 | 감사 기록 위치 변경 |
| max_offsets | 100 (실측 5000) | 배치 상한 1..10000 | 드라이버 메모리·처리 시간 |
| PALDO_LOG_DIR | out/logs | JSON 로그 디렉터리 | 서비스 계정 쓰기 가능해야 함 |
| features_version | public-snapshot-v1 | 관찰 기반 특징 버전 | 다른 D 특징 버전과 명시 변환 필요 |

## 한계

11/8 일괄 갱신: C-08-01 관측은 `ops/prometheus/README.md`, C-08-02 광고 집계는 `pipeline/README.md`, C-08-03 Job은 `ops/k3s/README.md`, C-08-04 태그·조회 계정은 `ops/runbook/user_test_release.md`(각 태스크 PR의 파일)에서 사용법·설정·한계를 확인한다. P-31 runtime LLM 없음, 기준선 모델 표기, 합성/부하 분리, 20번째 VM 비실행과 E/D 인계 대기를 반영했다. 각 PR 병합 완료를 뜻하지 않는다.


11/8 실제 엔진 200판의 17,823건을 Kafka/Garage Delta에 쓰고 전량 재발행해 행 수 불변·ID/hash 일치·유실/중복 0을 확인했다. 완료 200판에 라벨, silver 19,191행·export 14,460행이었다. driver와 두 executor 모두 Python 3.14.4, Spark 4.1.3·Delta 4.4.1 Spark 4.1용 JAR·Java 21을 썼다. 장애 두 판 각 52건도 실제 스택에서 일치했다. 해당 값은 검증 스냅샷이며 이후 입력으로 전체 bronze 행 수는 증가한다.

malformed 입력은 스트림을 멈춘다. 조용히 skip하거나 정답을 가공하지 않는다. 마스킹은 upstream 계약 책임이다. 보존 snapshot_json을 모델 특징으로 그대로 사용하면 안 된다. 파일 lock은 다른 호스트의 S3 쓰기를 막지 않는다. export는 overwrite이므로 완료된 실행의 버전으로 D에게 인계한다. 운영 스트림은 bronze이고 silver/labels는 명시 배치다. D 모델 학습·배포, 광고 silver/rejudge/gold(C-08-02), 중앙 관측(C-08-01)은 후속 범위다. 공용 도구 로그의 service 필드는 현재 ci이며 component로 작업을 구분한다.

## 변경 이력

일반 사람 판의 회차 없는 이벤트(`session_id=null`)도 동일한 가명·ID·해시 검증으로 적재한다. 필수 가명이 없거나 비사람 이벤트에 사람 식별자가 있으면 거절한다.

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| C-07-01 | 11/7 | 공식 게임·광고 bronze, 관찰 기반 silver·종료 후 라벨·마을 관찰자 export, 실제 200판 전량 재처리·버전 검증과 지속 서비스. 실측 11/8 |
| C-07-02 | 11/7 | 실스택 브로커/발행기 정지·재개와 Mongo/Kafka/Delta ID·seq·hash 대조. 실측 11/8 |
| C-07-04 | 11/7 | pipeline/ops 모듈 일괄 갱신, HTTPS 브라우저·실제 프록시 지연·운영 인계 연결. 실측 11/8 |
| C-08-01 | 11/8 | 지표 중계·읽기 수집·6대시보드·6경보·부하 실측 |
| C-08-02 | 11/8 | 광고 귀속·마감·멱등 집계·실제 환입 재대사 |
| C-08-02 | 11/11 | `ads_contract.ctr_cells`·`ctr_snapshot`, gold 내보내기에 `ctr_cells.json` 추가, `ops/ads_aggregate.sh`·systemd timer로 하루 1회 자동 실행(E 보강) |
| C-08-03 | 11/8 | k3s Job·Kafka sink·5호스트 자원 재측정 |
| C-08-04 | 11/8 | E 태그 archive·4th_private 전용 조회 계정 guard |
