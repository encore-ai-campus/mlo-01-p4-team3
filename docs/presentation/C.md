# C 인프라·데이터 파이프라인 구현과 검증 기록

11/11 S-11-04 발표 입력 자료다. 기준 dev는 `a7e74f2`이며, 오늘 실측은 미병합 PR의 고정 커밋 링크로 구분했다. 이후 시험 결과는 같은 항목에 추가한다. AWS는 사용자 지시로 보류했고 운영 LLM 서버는 P-31에 따라 사용하지 않는다. ([태스크](../tasks/C_infra.md#c-11-04-트러블검증-기록-발표용), [LLM 자원 증빙](../evidence/C-06-04.md))

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

MySQL 수동 승격은 공유 PJ3 복제 채널 I/O 실패 때문에 미실행이다. 광고 DB 지연·10배 광고 폭주·노트북 자원 압박의 p95 증가는 후속 실측 대상이다. 운영 LLM 서버 정지는 적용 대상이 없다. ([C-11-01 한계](https://github.com/encore-ai-campus/mlo-01-p4-team3/blob/a1295c239269cf1cad6800bb714e5054afa78869/docs/evidence/C-11-01.md))

### 관측·웹·마감 기록

| 대상 | 확인 수치·적용 범위 | 증빙 |
|---|---|---|
| 관측 | 노드 20대·운영 웹 2대상 수집, 대시보드 6종·경보 6개 | [C-08-01](../evidence/C-08-01.md), [JSON](../evidence/perf/C-08-01/live_scrape.json) |
| 실제 엔진 동시 활성 시험 | 활성 30판·303요청·오류 0, 서버 p95 1152.402ms로 1초 기준 초과 | [C-08-01](../evidence/C-08-01.md) |
| 후보 소스의 격리 성능 비교 | 30명, 녹화 엔진·격리 DB 조건. 웹 2프로세스 p95 218.762ms, 1프로세스 400.064ms | [C-08-04 실행 조건](../evidence/C-08-04.md), [JSON](../evidence/perf/C-08-04/rehearsal_1110.json) |
| 11/10 마감 처리 | 18:00:14 KST 설문 보정 완료 후 게임 177건·계정 193건 보호 스냅숏. 전체 내보내기 건수이며 참가자 수가 아님 | [C-08-04](../evidence/C-08-04.md) |

### 인계 상태

- 장애 복구 결과는 PR #372, 재실행 런북은 PR #373에서 검토 가능하다. 런북의 다른 흐름 담당자 실제 실행 검수는 대기다.
- 재학습 PR #370은 격리 모델·게이트·버전 rollback 검증 범위다. 검수된 실제 학습 입력·이미지/PVC를 쓴 운영 Job 완료로 발표하지 않는다.
- 광고 집계의 D 실행 ID, 사용자 테스트 태그 및 보호 위치로 계정 전달·수신 확인은 원래 태스크에서 계속 추적한다. ([C-08-02](../evidence/C-08-02.md), [C-08-04](../evidence/C-08-04.md))
- AWS 확장 작업은 보류이므로 계정별 비용·전체 배포 검증 결과는 없다. ([C 태스크](../tasks/C_infra.md))
