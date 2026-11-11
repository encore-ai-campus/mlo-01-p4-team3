# C 인프라·데이터 파이프라인 구현과 검증 기록

11/11 S-11-04 발표 입력 자료다. 오후 dev `098b077`까지 반영했으며, 실측은 해당 후보·환경과 고정 증빙 링크로 구분했다. AWS는 사용자 지시로 보류했다. 네 운영 장애는 동결 후보 `38abc0d`의 템플릿 경로이고, 현재 `098b077` LLM 접속 장애는 별도 실증했다. ([태스크](../tasks/C_infra.md#c-11-04-트러블검증-기록-발표용), [LLM 서빙 범위](../tasks/E_pm.md))

## 담당

C는 VM 자원·서비스 실행, DB 권한과 TLS, Mongo 게임 저장, Outbox→Kafka→Spark/Delta 전송, 광고 집계·대사, 관측·복구·재처리를 담당한다. 게임 규칙은 A, 웹과 응용 계층은 B, 모델과 광고 판정은 D, 통합 판정과 배포 후보·태그 선택은 E와 연결한다. ([담당 범위](../routing/C.md), [태스크](../tasks/C_infra.md))

## 구현

| 영역 | 구현·운영 동작 | 근거 |
|---|---|---|
| 게임 저장 | Mongo majority·버전 CAS, 명령 결과·이벤트·Outbox 원자 저장, 콘텐츠 버전 고정 | [C-06-01](../evidence/C-06-01.md) |
| 이벤트 전송 | TLS Kafka와 지속 Bronze, 같은 checkpoint 재개, ID·seq·본문 hash 전량 대사 | [C-06-02](../evidence/C-06-02.md), [C-07-01](../evidence/C-07-01.md), [C-07-02](../evidence/C-07-02.md) |
| 광고 집계 | serve 귀속일·맥락 조인, 유효/무효·부하 데이터 분리, silver/gold·원장 순액 대사와 환입 | [C-08-02](../evidence/C-08-02.md), [집계 정의](../../pipeline/README.md) |
| 운영 웹·관측 | 두 웹 프로세스와 Caddy, 고정 Host를 쓰는 내부 지표 중계, Prometheus·Grafana·경보 | [C-08-01](../evidence/C-08-01.md), [C-08-04](../evidence/C-08-04.md) |
| 복구·인계 | 실제 서비스 정지·재기동·Delta 대사, 기동→재처리→재학습→대사→종료 런북 | [장애 시험 PR #372](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/372), [런북 PR #373](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/373) |
| 모델 운영 준비 | D 학습 게이트·중앙 등록, 버전 고정·canary·rollback, 중단 상태 CronJob | [C-10-01 PR #370](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/370) |

## 트러블

| 문제 | 원인·대응 | 확인한 결과·출처 |
|---|---|---|
| 일반 판 이벤트에서 Bronze가 재시작 반복 | API 계약상 일반 판 `session_id=null`인데 이전 Bronze 검증이 필수 문자열로 취급했다. 사람 가명 필수·빈 문자열 거부를 유지하면서 null을 허용하고, 같은 checkpoint로 재개했다 | 배치 commit 재개 후 장애 판까지 Delta 대사했다. [수정 PR #371](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/371), [실측 기록](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/a1295c239269cf1cad6800bb714e5054afa78869/docs/evidence/C-11-01.md) |
| 두 웹의 `/metrics`가 Host 검증으로 400 | 공개 출처와 일치하는 고정 Host로 loopback 지표를 중계하고, 운영 두 포트를 별도 scrape 대상으로 등록했다 | 웹 두 대상 up=1, 노드 20대 up. [트러블 C-08-01](../troubles/C.md#c-08-01), [증빙](../evidence/C-08-01.md) |
| 판 저장 뒤 진척 반영 전 중단 시 획득 항목 누락 | 저장된 Outbox 이벤트를 다시 읽고 온라인과 같은 멱등 진척 함수를 호출하도록 복구 CLI를 만들었다 | 두 판에서 단서 3개·결말 1개 복구, 재실행 신규 반영 0건. [트러블 C-09-04](../troubles/C.md#c-09-04), [증빙](../evidence/C-09-04.md) |
| 성능 비교가 계정 이력 누적으로 교란 | 같은 시험 계정의 완료 판이 늘고 새 판 시작 때 과거 판을 개별 load했다. 구간별 새 계정으로 통제하고 최신 dev의 진행 판 ID 일괄 조회를 검증했다 | 실제 Mongo 203회·3102.049ms→1회·2.861ms, 결과·순서 일치. 전체 API 지연 수치가 아니다. [C-11-01](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/C-11-01.md), [JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/perf/C-11-01/open_scan.json) |
| 광고 부하에서 계정 SQL 오류·접속 포화 발견 | 한도40·최대 접속41·누적 접속 초과525를 확인했다. 메모리와 원값을 확인해64로 시험·보존했다 | 현재098b077·한도64의 광고지면 HTTP요청10.006배에서 게임716건 오류0, p95차이−72.962ms. DB 지연 성능은 여전히 실패다. [C-11-01](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/C-11-01.md), [용량 JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/perf/C-11-01/mysql_capacity.json) |

## 산출물·검증 수치

### 원본·메시지·집계 대조

서로 다른 실행의 수치를 합산하지 않는다. 광고 시험의 C 자체 실행은 D가 제공할 실행 ID의 인수 검증을 대신하지 않는다.

| 시험·범위 | 원본→Kafka/bronze→집계 | 불일치·결과 | 증빙 |
|---|---|---|---|
| 11/8 브로커·발행기 정지/재개, 합성 두 판 | Mongo 104건→Kafka 고유 104건→Delta 104건 | 유실 0·중복 0·hash 불일치 0, HTTPS API 오류 0 | [C-07-02](../evidence/C-07-02.md), [JSON](../evidence/perf/C-07-02/live_faults.json) |
| 11/8 C 자체 광고 검증 | 원본 104건→공식 Bronze 고유 104건, gold 6행·학습 3행 | hash 불일치 0, 실제 환입 후 원장/Delta 순액 대사 불일치 0 | [C-08-02](../evidence/C-08-02.md), [JSON](../evidence/perf/C-08-02/actual_ads.json) |
| 11/11 아래 네 장애의 합성 네 판 | Mongo 380건→Kafka 고유 380건→Delta 380건 | 유실 0·중복 0·hash 불일치 0 | [C-11-01 고정 기록](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/a1295c239269cf1cad6800bb714e5054afa78869/docs/evidence/C-11-01.md), [JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/a1295c239269cf1cad6800bb714e5054afa78869/docs/evidence/perf/C-11-01/faults.json) |

### 11/11 실제 장애 복구

운영 후보 `38abc0d`와 같은 엔진·DB 조립의 격리 HTTP 포트에서 합성 판을 진행했다. 측정 구간은 정지 전 기록부터 서비스 active 확인까지다. 별도 감지 지연·재선출 시간만 분리한 수치가 아니며 P-13 복구 합격선을 임의로 정하지 않았다. 다음 표의 모든 수치는 [C-11-01 실행 기록](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/a1295c239269cf1cad6800bb714e5054afa78869/docs/evidence/C-11-01.md)에 근거한다.

| 실제 장애 | 정지 전~복구 확인(KST) | 관측 구간 | 게임 API 요청/오류 |
|---|---|---:|---:|
| Kafka 브로커 정지 | 07:51:09~07:51:30 | 21초 | 15/0 |
| Mongo Primary 정지·선출 | 07:55:51~07:56:25 | 34초 | 15/0 |
| 광고 서버 정지 | 07:59:50~08:00:09 | 19초 | 15/0 |
| Spark 강제 종료·동일 checkpoint 재개 | 08:07:40~08:08:01 | 21초 | 15/0 |

MySQL 수동 승격은 공유 PJ3 복제 채널에서 필요한 binlog가 만료돼 미실행이다. 동결 후보에는 운영 LLM 서버가 없었으며 오후 현재 운영 LLM 접속 장애는 아래 별도 실행으로 검증했다. 생성 서버 자체의 프로세스 종료는 미실행이다. ([C-11-01 한계](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/C-11-01.md))

### 11/11 게임 경로 보호 추가 실측

현재 운영 `098b077`·`content-1.1`, 실제 엔진·DB·광고, 격리 Daphne 두 개+Caddy, 구간별 새 계정 네 명·180초, MySQL 접속 한도64 조건이다. 구간 전 시험 웹을 재시작해 이전 회로 차단 상태를 초기화했다. 30명·매 요청 LLM 추론 성능으로 확대하지 않는다. [실측 증빙](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/C-11-01.md), [결과 JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/perf/C-11-01/protection_current098.json)

| 구간 | 게임 요청/오류 | API p95(ms) | 정상 대비 증가(ms) | 기준 판정 |
|---|---:|---:|---:|---|
| 정상 광고 | 720/0 | 446.033 | 0 | 기준선 |
| 광고 DB 목적지 TCP 왕복 통신 3000ms 지연 | 720/0 | 520.896 | 74.863 | 실패(50ms 초과) |
| 광고지면 HTTP 요청량 10.006배 | 716/0 | 373.071 | -72.962 | 통과 |
| 노트북3 호스트 CPU 압박 | 720/0 | 340.750 | -105.283 | 통과 |

총2876게임 요청 오류0이다. 음수 차이는 관측값이며 부하가 응답을 빠르게 한다는 결론은 아니다. 광고 비율은 지면 HTTP 요청176→1761건으로 Kafka 이벤트량이 아니다. 이전79da6f1의 DB지연 증가184.220ms 등 실패 결과도 원 증빙에 보존했다. 소스와 구간 초기화가 달라 두 수치를 개선 효과로 단정하지 않는다. DB 지연 기준 초과 원인은 추가 분석이 필요하다.

이번214판(완주198·진행16)의 Mongo18389건·Kafka고유18389건·공식Delta18389건은 누락·중복·해시 불일치0이다. 미발행 Outbox0, 전량 load_test=true다. 합성 계정16개와 세션·임시 인증을 정리하고 시험 웹·프록시·CPU 작업을 종료했다. 이전 후보48개와 LLM시험4개 정리는 별도 실행이다. ([대조 JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/perf/C-11-01/current_events.json))

### 현재 운영 LLM 접속 장애

운영 `098b077`·콘텐츠 `content-1.1`과 같은 조립의 격리 웹에서 실제 SSH 접속 터널을 잠시 정지했다. 생성 서버 프로세스는 유지한 연결 장애다. 회의 다듬기 생성4건의 `unreachable`을 확인하고 shown API로 template을 고정했다. 게임은 전체31요청(장애 전 시작1·장애 중30) 오류0으로 완주했다. 정지 전부터 완주·접속 복구·health200 확인까지10.748초였다. 이 판의 Mongo·Kafka·Delta141건은 유실·중복·해시 불일치0이다. ([실측](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/C-11-01.md), [JSON](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/f87fb7a/docs/evidence/perf/C-11-01/llm_link_fault.json))

### 관측·웹·마감 기록

| 대상 | 확인 수치·적용 범위 | 증빙 |
|---|---|---|
| 관측 | 노드 20대·운영 웹 2대상 수집, 대시보드 6종·경보 6개 | [C-08-01](../evidence/C-08-01.md), [JSON](../evidence/perf/C-08-01/live_scrape.json) |
| 실제 엔진 동시 활성 시험 | 활성 30판·303요청·오류 0, 서버 p95 1152.402ms로 1초 기준 초과 | [C-08-01](../evidence/C-08-01.md) |
| 후보 소스의 격리 성능 비교 | 30명, 녹화 엔진·격리 DB 조건. 웹 2프로세스 p95 218.762ms, 1프로세스 400.064ms | [C-08-04 실행 조건](../evidence/C-08-04.md), [JSON](../evidence/perf/C-08-04/rehearsal_1110.json) |
| 11/10 마감 처리 | 18:00:14 KST 설문 보정 완료 후 게임 177건·계정 193건 보호 스냅숏. 전체 내보내기 건수이며 참가자 수가 아님 | [C-08-04](../evidence/C-08-04.md) |

### 인계 상태

- 장애 복구 결과는 PR #372, 재실행 런북은 PR #373에서 검토 가능하다. 런북의 다른 흐름 담당자 실제 실행 검수는 대기다.
- 재학습 PR #370은 격리 모델·게이트·버전 rollback 검증 범위다. 카나리아 승격 경합 수정은 [PR #402](https://github.com/encore-ai-campus/mlo-01-p4-team3/pull/402)로 병합됐고 경합·기존 회귀9건과 실제 격리 MLflow를 검증했다. 검수된 실제 학습 입력·이미지/PVC를 쓴 운영 Job 완료로 발표하지 않는다. ([경합 증빙](../evidence/C-10-01.md))
- 광고 집계의 D 실행 ID, 사용자 테스트 태그 및 보호 위치로 계정 전달·수신 확인은 원래 태스크에서 계속 추적한다. ([C-08-02](../evidence/C-08-02.md), [C-08-04](../evidence/C-08-04.md))
- AWS 확장 작업은 보류이므로 계정별 비용·전체 배포 검증 결과는 없다. ([C 태스크](../tasks/C_infra.md))
