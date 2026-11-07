# 파이프라인

## 무엇을

인프라 배치 계층이다. `pipeline/spark/bronze_contract.py`가 공식 EventEnvelope·토픽·순번·UTC·진실 공개 경계를 검증한다. `ingest_bronze.py`는 Kafka 게임 토픽 6개를 읽어 Delta bronze에 event_id MERGE하고 입력 offset 범위·Delta 버전을 남긴다. 계약 검증은 표준 라이브러리만 쓰고 Spark/Delta import는 실행 시점에 한다.

## 왜

[이벤트 계약](../contracts/events.md)과 C-07-01은 bronze부터 event_id 중복 제거를 요구한다. 전송 offset별 저장만으로는 같은 이벤트의 재발행이 여러 행이 된다. 기존 EventEnvelope·serde를 재사용해 별도 이벤트 형식이 생기지 않도록 했다. 내용이 다른 ID 충돌과 잘못된 봉투는 조용히 건너뛰지 않는다.

## 사용법

```sh
python -m unittest tests.contract.test_pipeline_bronze -v
```

실제 Spark 제출은 [pipeline/README.md](../../pipeline/README.md)의 외부 설정·의존성 조건을 따른다. 저장소 루트를 PYTHONPATH에 두고 `spark-submit`으로 `pipeline/spark/ingest_bronze.py`를 실행한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| PALDO_PIPELINE_CONFIG | 없음, 필수 | 저장소 밖 설정 JSON | 주소·인증·저장 경로를 환경별로 선택 |
| max_offsets | 100 | microbatch 수신 상한 | 드라이버 검증 메모리·지연 변화 |
| bronze_path | 없음, 필수 | Delta 데이터 경로 | 다른 테이블로 저장 |
| checkpoint_path | 없음, 필수 | Kafka offset/checkpoint 영속 경로 | 삭제·변경하면 재처리 범위 변화 |
| lock_path | 없음, 필수 | 단일 호스트 writer lock | 같은 lake에 동일 경로 사용 필요 |
| audit_dir | 없음, 필수 | 배치별 offset·Delta 버전 JSON | 데이터 경로 밖 영속 위치 권장 |
| PALDO_LOG_DIR | out/logs | 구조화 로그 위치 | 서비스 계정 쓰기 가능 경로 필요 |

## 한계

C-07-01 부분 작업이다. 실제 공식 토픽 Kafka→Delta, 200판 대조와 실행기 Python 확인은 미검증이다. 광고 봉투·silver TrainingRow·truth 조인은 미구현이다. malformed 입력은 스트림을 멈추며 별도 검토·수정 발행이 필요하다. 원문 텍스트 마스킹은 upstream 계약 책임이며 이 작업이 마스킹·삭제 체계를 대신하지 않는다. 단일 호스트 lock이 다른 호스트의 동시 S3 쓰기를 막지는 않는다. 로그는 기존 tools.jsonlog를 재사용하므로 service 필드는 ci이며 운영 서비스 로그 분류 정비는 후속 과제다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| C-07-01 | 11/7 | 공식 봉투 기반 bronze 검증·멱등 MERGE·offset/Delta 버전 감사 기록과 계약 테스트를 부분 구현 |
