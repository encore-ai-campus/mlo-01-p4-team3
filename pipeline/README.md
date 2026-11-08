# Kafka → Delta 파이프라인(C)

공식 계약은 `engine/domain/events.py`, `docs/contracts/events.md`다. 운영 주소·계정·인증 파일은 저장소 밖 설정으로 주입한다.

## 현재 구현 범위

`spark/ingest_bronze.py`는 공식 게임 6개·광고 4개 토픽을 Structured Streaming으로 읽어 `event_id` 기준 Delta MERGE한다. `spark/bronze_contract.py`는 외부 패키지 없이 공식 `EventEnvelope`를 검증하고 동일 ID의 내용 충돌을 거절한다. 설문 건너뜀의 null 점수, source, load_test, 판·모델·콘텐츠 버전과 payload는 봉투 JSON에 그대로 남긴다.

원본 필드는 `envelope_json`이며 Kafka topic/partition/offset도 보존한다. 재전송은 같은 event_id 한 행으로 적재한다. 중복 행에서 다른 내용이 발견되면 전체 microbatch를 실패시키고 checkpoint를 전진시키지 않는다. 오류를 확인한 운영자가 원본 보존·수정 발행 절차를 결정해야 하며, 무조건 skip하지 않는다.

11/8 공식 경로에서 실제 엔진 200판을 적재·전체 재발행해 ID/hash·순번·행 수를 대조했다. 광고 DTO도 자체 ads-0.6 계약으로 적재한다. 기존 별도 데모 서비스는 중지·비활성화하고 데이터/checkpoint는 보존했다. 실측은 [C-07-01](../docs/evidence/C-07-01.md)을 따른다.

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

## Silver와 학습 export

`build_silver.py --config ...`는 시작 시 고정한 bronze Delta 버전에서 observation의 전체 ObservationSnapshot을 엄격 역직렬화하고 후보별 TrainingRow를 만든다. `row_id`는 snapshot_id:candidate이며 insert-only MERGE한다. features_version은 `public-snapshot-v1`: 관찰자 믿음 prior_suspicion, day, n_candidates, 공개 claims/speeches/tells 개수만 특징으로 쓴다. private role·secret·종료 truth는 특징에 넣지 않는다. `snapshot_json`은 추후 D 특징 추출용 보존 열이며 그대로 모델 특징으로 넣으면 안 된다.

`join_labels.py --config ...`는 정상 game_ended 이후 seq의 ended truth만 game_id로 조인한다. aborted·completed=false·미종료는 라벨을 만들지 않는다. silver의 observer_role/source/load_test는 그대로 유지한다. `training_export_path` JSON에는 TrainingRow 표준 필드만 쓰며 villager·label nonnull·load_test=false만 출력한다. D가 다른 특징 버전을 쓰면 이 버전과 섞지 않고 명시적으로 변환해야 한다. export는 현재 시점 전체 overwrite이므로 D는 완성된 실행의 버전/manifest를 확인한 후 읽는다. 이 작업은 D 모델 학습·배포를 수행하지 않는다.

추가 외부 JSON 키는 `silver_path`, `training_export_path`다. 원본/bronze/silver/export/checkpoint는 각각 분리한다. `verify_run.py --config ... --events ... --name ...`은 입력 전체 ID/hash, seq 연속성, 종료/라벨 판 수, 실제 executor Python을 검사한다.

## 지속 실행·운영 갱신

검증된 Linux 런처가 `bronze` 인자를 받아 보호 설정으로 spark-submit을 실행하면 `python -m pipeline.install_bronze_service --launcher "$PRIVATE_LAUNCHER" --state "$PRIVATE_STATE" --user "$SPARK_USER"`로 `paldo-bronze.service`를 등록할 수 있다. 서비스 계정은 root가 아니며 기존 RPC/TLS 및 S3 설정을 유지한다. 상태/시작/종료는 `systemctl status/start/stop paldo-bronze`다. 로그는 journal과 audit/batch JSON이다. 기존 데모 서비스나 다른 writer를 동시에 실행하면 안 된다.

silver/label 실행도 같은 writer lock을 사용한다. 운영 갱신은 bronze 서비스 중지 → available-now로 밀린 입력 처리 → build_silver → join_labels → verify → bronze 재시작 순서다. 실패해도 checkpoint를 삭제하지 않고 원인 수정 후 재시작한다. 학습 스냅샷의 경로·Delta 버전·입력 SHA는 비공개 인계와 감사 기록에서 확인한다. S3 객체 경로는 기존 Garage client가 허용한 버킷 안의 PJ4 전용 prefix이며 기존 데모/PJ3 데이터와 분리했다.
