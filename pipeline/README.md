# Kafka → Delta 파이프라인(C)

공식 계약은 `engine/domain/events.py`, `docs/contracts/events.md`다. 운영 주소·계정·인증 파일은 저장소 밖 설정으로 주입한다.

## 현재 구현 범위

`spark/ingest_bronze.py`는 공식 게임 토픽 6개를 Structured Streaming으로 읽어 `event_id` 기준 Delta MERGE한다. `spark/bronze_contract.py`는 외부 패키지 없이 공식 `EventEnvelope`를 검증하고 동일 ID의 내용 충돌을 거절한다. 설문 건너뜀의 null 점수, source, load_test, 판·모델·콘텐츠 버전과 payload는 봉투 JSON에 그대로 남긴다.

원본 필드는 `envelope_json`이며 Kafka topic/partition/offset도 보존한다. 재전송은 같은 event_id 한 행으로 적재한다. 중복 행에서 다른 내용이 발견되면 전체 microbatch를 실패시키고 checkpoint를 전진시키지 않는다. 오류를 확인한 운영자가 원본 보존·수정 발행 절차를 결정해야 하며, 무조건 skip하지 않는다.

**C-07-01 부분 구현**이다. 광고 봉투, silver TrainingRow, 종료 후 truth 라벨 조인, 실제 엔진 200판 Kafka→Delta 대조, VM 실행기 Python 실측은 이 PR에서 완료로 판정하지 않는다. 운영 중인 기존 별도 합성 이벤트 데모와 토픽·계약이 다르므로 데모 설정을 정식 입력으로 사용하지 않는다.

## 사용법

Spark 4.1.3 + Delta Spark 4.4.1의 Spark 4.1용 JAR(`io.delta:delta-spark_4.1_2.13:4.4.1`), Kafka 커넥터 4.1.3, S3A 의존성을 운영 환경에서 공급한다. 필요한 Python 의존성은 `requirements/pipeline.txt`에 있다. 새로 Maven 다운로드하거나 기존 서버 설정을 바꾸는 명령은 여기 포함하지 않는다.

저장소 밖 JSON의 필수 키:

| 키 | 내용 |
|---|---|
| bronze_path | 전용 Delta 경로 |
| checkpoint_path | bronze와 겹치지 않는 영속 경로 |
| lock_path | 동일 writer 호스트의 잠금 파일 |
| audit_dir | batch별 입력 offset 범위·Delta 버전·수신/삽입 건수 JSON |
| kafka | 기존 SSL Kafka client 옵션. hostname 검증 유지 |
| spark | `spark.master`, RPC 인증, S3A/TLS·인증 설정 등. 값은 저장소 밖 |
| max_offsets | 기본 100. 1~10000개 이하 microbatch |

```sh
export PYTHONPATH="$PWD"
export PALDO_PIPELINE_CONFIG=/보호된/설정/pipeline.json
export PALDO_LOG_DIR=/쓰기가능한/로그경로
spark-submit <운영환경의 JAR·드라이버 Python·인증 옵션> pipeline/spark/ingest_bronze.py --available-now
```

`--available-now` 없이 실행하면 30초 주기의 스트림이다. 외부 설정 파일을 만들 때는 서비스 계정만 읽도록 권한을 제한한다. CLI/로그에 비밀값을 붙이지 않는다. 새 작업 호스트는 `pipeline`, `engine`, `tools` 패키지를 driver에서 import할 수 있어야 한다.

S3 Delta는 한 driver에서만 쓴다. `lock_path`는 같은 호스트에서의 중복 실행만 차단하며 다른 호스트의 동시 쓰기를 막지 못한다. checkpoint는 삭제하지 않고 보관한다. `failOnDataLoss=true`이므로 Kafka 보존 기간을 넘긴 누락은 실패 처리된다.

계약 단위 테스트:

```sh
python -m unittest tests.contract.test_pipeline_bronze -v
```

## C-06-02 공식 토픽과 발행

`topics.yaml`은 JSON 문법의 YAML이다. `python -m pipeline.apply_topics`로 검증하고 관리자 보호 TLS JSON과 `--apply`로 없는 토픽만 만든다. 기존 토픽 설정이 계약과 다르면 변경하지 않고 실패한다. 게임 발행은 별도 프로세스 `python -m server.infra.outbox_publisher --kafka-config <보호 JSON>`이며 PALDO_MONGO_URI를 외부 주입한다. ack 뒤 Mongo 표시·실패 재시도와 지표를 제공한다. 소비자는 event_id와 내용 hash를 durable sink와 함께 저장한다. 광고 MySQL 발행은 D 담당이다. 실제 시험·제한은 [C-06-02 증빙](../docs/evidence/C-06-02.md)을 따른다.
