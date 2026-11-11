# PJ4 운영 인계

서버 역할은 [VM 역할표](vm_roles.md), 인증정보는 저장소 밖 보호 인벤토리를 사용한다. 명령은 표에 지정한 VM에서 실행한다. 작업 시작 전에 실제 웹 unit의 WorkingDirectory와 ExecStart를 확인해 현재 배포 소스·Python·보호 프로필 경로를 확보한다.

```sh
sudo systemctl show paldo-web@8000 paldo-web@8002 paldo-outbox -p WorkingDirectory -p ExecStart -p ActiveState
```

이후 명령의 `SOURCE`, `PYTHON`, `PROFILE`, `PRIVATE_*`는 그 인계 경로로 설정한다. 프로필 내용을 터미널이나 채팅에 출력하지 않는다.

11/11 오후 확인한 운영 후보는 `098b077`·콘텐츠 `content-1.1`이며 LLM 터널·복기 작업자·NPC 대사 작업자가 실행 중이었다. 이후 배포에서는 위 unit의 실제 프로필을 다시 기준으로 삼는다. 과거 동결 후보의 템플릿 경로와 현재 LLM 경로를 구분한다.

## 기동

| 순서 | 실행 VM | 명령 | 통과 확인 |
|---|---|---|---|
| 1 | Mongo 세 노드 | `sudo systemctl start mongod` | 보호 설정으로 hello의 Primary와 세 member 확인 |
| 2 | MySQL Primary·Replica | `sudo systemctl start mysql` | Primary 연결, Replica의 `pj4` IO·SQL Yes 및 지연 0 |
| 3 | Garage 두 노드 | `sudo systemctl start pj3-garage` | 기존 TLS 클라이언트로 lake 읽기 확인 |
| 4 | Kafka 세 노드 | `sudo systemctl start pj3-kafka` | 브로커 3, 공식 토픽의 ISR·복제 3 |
| 5 | Spark master·worker | `sudo systemctl start pj3-spark` | 클러스터 worker 가용 상태 확인 |
| 6 | Spark master | `sudo systemctl start paldo-bronze` | 서비스 active와 함께 새 배치 commit·audit 증가 확인 |
| 7 | 광고 서버 | `sudo systemctl start paldo-ads paldo-ads-publish paldo-ads-expire paldo-ads-refund` | 광고 health와 worker 지표 확인 |
| 8 | 게임 웹(LLM 사용 시) | `sudo systemctl start paldo-llm-tunnel` | 보호 인계 경로의 생성 서버 health 200 |
| 9 | 게임 웹(LLM 사용 시) | `sudo systemctl start paldo-review-worker paldo-npc-line-worker` | 두 worker active·작업 결과 확인 |
| 10 | 게임 웹 | `sudo systemctl start paldo-outbox paldo-web@8000 paldo-web@8002 caddy` | 두 health·metrics 200, HTTPS·wss 확인 |

`is-active`만으로 적재 성공을 판정하지 않는다. 11/11에는 일반 사람 판의 null 회차를 거부해 Bronze가 재시작을 반복했다. [일반 판 수정 PR](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/371)의 검증기를 포함한 소스로 실행하고 새 체크포인트 commit과 `bronze_batch_committed`를 확인한다.

```sh
sudo systemctl show paldo-bronze -p ActiveState -p NRestarts -p Result
sudo journalctl -u paldo-bronze --since '10 minutes ago' --no-pager
```

로그는 보호 서버 안에서 확인하고 식별자·접속정보를 지운 건수만 증빙에 남긴다. 계정·광고 DB TLS·스키마 분리 및 복제 확인은 [MySQL 런북](../mysql/README.md), 웹 두 프로세스와 고정 Host 수집은 [프록시](../proxy/README.md) 및 [지표](../prometheus/README.md)를 따른다.

## 데이터 재처리

현재 Primary의 MySQL 접속 한도는 C-11-01 검증 뒤64로 보존했다. 용량·TLS·복제 확인은 [MySQL 런북](../mysql/README.md)을 따른다. 공유 PJ3 채널의 binlog 만료는 해결되지 않았으므로 전체 승격을 PJ4 지연0만으로 판정하지 않는다. LLM 설정·모드·작업자 운영은 [LLM 절차](llm_gen.md)를 따른다.

외부 설정의 기존 checkpoint·bronze·writer lock을 그대로 사용한다. 정식 스트림을 먼저 중지하고 단일 writer로 available-now를 실행한다. 다른 작업이 같은 writer lock을 잡고 있으면 중복 실행을 중단한다.

```sh
sudo systemctl stop paldo-bronze
PIPELINE_USER=$(systemctl show paldo-bronze -p User --value)
PIPELINE_USER=${PIPELINE_USER:-root}
sudo -u "$PIPELINE_USER" "$PRIVATE_PIPELINE_LAUNCHER" bronze --available-now
sudo -u "$PIPELINE_USER" "$PRIVATE_PIPELINE_LAUNCHER" silver
sudo -u "$PIPELINE_USER" "$PRIVATE_PIPELINE_LAUNCHER" labels
sudo systemctl start paldo-bronze
```

실패하면 실패 배치·예외 유형을 기록하고 원인을 고친 뒤 같은 checkpoint로 재개한다. 종료 전에 스트림 active와 새 batch commit을 확인한다. 삭제 요청이 있으면 [개인정보 런북](privacy.md)의 제외 목록을 먼저 적용한다. Kafka 보존 기간이 지난 offset은 실제 소멸 범위와 승인된 복구 원본을 검토한다.

11/10 마감 스냅숏은 `backfill_surveys` 다음에 생성됐다. 같은 파일의 보존 사본으로 계정·게임을 검증하고 E·D의 집계·분할에는 고정 cutoff 18:00 KST를 사용한다.

## 재학습

[재학습·카나리아 PR](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/370)의 실행기·CronJob·롤백 런북을 사용한다. 승인된 Delta 테이블 버전, E 검수 라벨·평가 맥락, 최신 삭제 목록, 입력 PVC, ML 의존성 이미지가 필요하다. 초기에는 수동 Job을 한 번 실행한다. 야간 CronJob은 검증 전 `suspend=true`를 유지한다.

```sh
"$PYTHON" -m ops.mlflow.retrain --config "$PRIVATE_RETRAIN_CONFIG" --check
kubectl -n paldo-ml get cronjob paldo-retrain
kubectl -n paldo-ml create job --from=cronjob/paldo-retrain "paldo-retrain-manual-$(date +%s)"
```

평가 거부도 정상 결과다. run의 출처·특징·SHA 및 거부 사유를 확인하고 후보 등록 수가 0인지 본다. 통과 후보는 D가 카나리아·대조 최소 표본과 경보를 확인해 승격한다. 복구는 previous를 production에 적용하며 진행 중 판은 시작 때 고정한 모델을 유지한다.

실제 입력·이미지·PVC와 CronJob 설치는 C-10-01의 미완료 구간이다. 위 `--check`와 `get cronjob`이 성공한 환경에서만 `create job`을 진행한다. 정의 파일의 server dry-run과 격리 모델 검증을 실제 학습 실행으로 기록하지 않는다. 승격은 [카나리아 경합 수정 PR #402](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/402)를 포함하고, 기대 후보 버전이 달라지면 관측 보고서부터 다시 확인한다.

## 대사

같은 합성 판의 Mongo next_seq·원본 봉투 해시, Kafka 수신 metadata, 공식 Delta metadata를 보호 경로에 확보한다. 참조 대조 JSON은 판 ID·seq·해시만 포함한다.

```sh
"$PYTHON" -m tools.reconcile_game_events --mongo "$PRIVATE_MONGO_METADATA" --published "$PRIVATE_KAFKA_METADATA" --bronze "$PRIVATE_BRONZE_METADATA" --out "$PRIVATE_RECONCILIATION"
```

`ok=true`, 누락·초과·잘못된 ID·해시 불일치 0, Bronze 원본 중복 0을 확인한다. Kafka 원시 재수신 중복은 별도 숫자로 기록하고 고유 이벤트 수와 비교한다. 광고는 [광고 집계·대사](../../pipeline/README.md)의 최종·잠정 귀속일과 원장 잔액까지 확인한다.

## 종료

시험용 unit·부하 생성기를 먼저 종료하고 복구 타이머가 남아 있는지 확인한다. 정상 서비스를 종료하는 운영 시간에는 웹·광고 요청을 먼저 종료한 뒤 아웃박스·worker 배출 상태를 확인한다.

```sh
sudo systemctl stop paldo-web@8000 paldo-web@8002 caddy
# LLM 사용 배포는 새 요청을 막은 뒤 작업자·터널 순서로 종료한다.
sudo systemctl stop paldo-review-worker paldo-npc-line-worker
sudo systemctl stop paldo-llm-tunnel
sudo systemctl stop paldo-ads
# 아웃박스 및 worker의 pending=0 확인 후
sudo systemctl stop paldo-outbox paldo-ads-publish paldo-ads-expire paldo-ads-refund
# 마지막 Kafka 입력까지 적재하고 commit된 것을 확인한 뒤
sudo systemctl stop paldo-bronze
```

공유 DB·Kafka·Garage·Spark VM 종료는 같은 인스턴스를 사용하는 PJ3 및 다른 작업의 종료 여부를 먼저 확인한다. MySQL에는 PJ4 외에 PJ3 복제 채널도 있다. 전체 인스턴스 승격을 PJ4 채널 검사만으로 실행하지 않는다.

## 검수 기록

다른 흐름 담당자가 위 순서의 기동·재처리·재학습 준비·대사를 수행하고 아래 표를 채운다.

| 검수자 흐름 | 소스 SHA | 실행 일시 | 기동부터 대사까지 걸린 시간 | 막힌 단계와 수정 후 재실행 결과 |
|---|---|---|---|---|
| 대기 | 대기 | 대기 | 대기 | C가 아닌 담당자 1명의 실제 실행 필요 |
