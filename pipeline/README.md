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

## 광고 silver·gold·대사 (C-08-02)

`ads_silver.py`, `ads_gold.py`, `ads_reconcile.py`는 별도 writer lock·driver 포트로 공식 bronze 스트림과 동시에 실행한다. 입력 Delta 버전을 고정하고 최대 100,000행을 넘으면 일부만 계산하지 않고 실패한다. 전량 snapshot을 키·row hash 기반 MERGE하며 사라진 집계 그룹도 지운다. 규모를 넘기기 전 분산 조인으로 전환해야 한다.

귀속은 serve 서버 시각의 Asia/Seoul 날짜·시간이다. 클라이언트 시각은 클릭 인정에 쓰지 않는다. `serve_id`로 serve context를 붙이고 지연 serve는 매 실행 다시 조인한다. 맥락이 없는 행은 학습에서 제외하고 건수를 별도 기록한다. 맥락 조인 자체가 과금을 바꾸지 않는다. 온라인 서명 실패 판정은 보존하며 토큰 서명을 silver에서 재검증했다고 주장하지 않는다.

CTR=유효 click serve 수/유효 impression serve 수(유효 click은 노출 1회 포함), 0노출이면 null이다. 비용은 정수 밀리원, 실현 eCPM=순액×1000/유효 노출, 실현 CPC=순액/유효 클릭, 0분모는 null이다. 무효율은 impression/click 판정 행 중 invalid 비율이며 pending을 별도 센다. 소진율은 캠페인·귀속일·source의 순액/일 예산이다. gold는 캠페인×지면×귀속 시간·source·load_test로, 학습 행은 소재×지면×preset_id×outcome×시간·source로 분리하며 부하·맥락 없음·0유효 노출은 학습에서 제외한다.

잠정 마감=다음 날 서울 자정, 확정 마감=자정+토큰 TTL 30분+판정 유예 2분이다. 잠정·확정 Delta 경로를 분리한다. 같은 silver 관찰 종료 시각으로만 gold를 만든다. 확정된 gold의 변경은 `final_restatement_approved`가 없으면 거절한다. 확정 후 환입 정책 제안은 원 귀속일을 정정하되 Delta 이력과 학습 입력 버전을 보존하는 방식이며, 실제 적용은 PM 확정 뒤에만 한다.

silver는 invalid가 된 온라인 유효 과금만 결정적 ID의 ad.rejudge로 보낸다. SQLite outbox에 먼저 쓰고 Kafka ACK 뒤 published를 기록한다. D의 실제 환입 소비자가 MySQL에 원과금 반대 금액을 1회 추가한 뒤 재대사한다. 수정된 동일 serve의 CPM 과금을 재생성하지 않는다. 원장만/Delta만/중복/금액 차이/고아 환입을 모두 센다.

```sh
spark-submit <외부 TLS·JAR 옵션> pipeline/spark/ads_silver.py --config "$PRIVATE_AD_CONFIG" --as-of-ms "$CUTOFF" --publish-rejudges
spark-submit <외부 TLS·JAR 옵션> pipeline/spark/ads_gold.py --config "$PRIVATE_AD_CONFIG" --as-of-ms "$CUTOFF" --phase final
spark-submit <외부 TLS·JAR 옵션> pipeline/spark/ads_reconcile.py --config "$PRIVATE_AD_CONFIG" --ledger "$PRIVATE_LEDGER_EXPORT"
python -m unittest tests.contract.test_ads_pipeline -v
```

`ops/ad_probe.py --execute`는 C가 만든 격리 합성 실행을 실제 내부 HTTP·MySQL로 검증한다. `ads_probe_snapshot.py`는 그 실행 ID/hash 전체를 공식 bronze와 대조한 뒤 별도 검증 Delta만 만든다. missing serve 주입은 이 복사본의 합성 양수 과금 한 건에만 허용한다. 공식 bronze는 바꾸지 않는다. 이 실행은 PM이 지정한 **D-08-02 7번의 D 제공 실행 ID를 대체하지 않는다**. 그 ID를 받은 뒤 같은 대사를 반복해야 C-08-02 3번을 닫을 수 있다.
