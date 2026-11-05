# PRD · 팔도무록: 귀신이 숨어든 마을

> 작성 규칙: 실제 접속 정보(IP, 계정, 비밀번호, 키)와 개인정보는 쓰지 않는다. 서버는 논리 서버명으로만 부른다. 담당자 실명은 쓰지 않는다.
> 이 문서는 "무엇을 만들고 무엇으로 합격인가"를 정한다. 상세 규격은 계약 문서(`contracts/`)에 두고 여기서는 위치만 가리킨다.

- document_id: `PRD-PALDO-001`
- version: `v1`
- document_state: `Draft`
- brd_reference: `BRD-PALDO-001@v1`
- scope_basis: `기획서 paldo_design_v1.md v1.1(2026-11-04 확정 결정과 1.1절 합격선), 계약 contracts/(contract-0.3, ads-0.3), 태스크 tasks/(커밋 c953d22 + E 11/6 일부 미룸·C-11-04 추가 반영분)`
- owner_role: `STK-PM-001`
- reviewer_roles: [`STK-ENGINE-001`, `STK-WEB-001`, `STK-INFRA-001`, `STK-MLAD-001`] (흐름별로 다른 흐름 1명이 검토, 11절)

## 1. 제품 정의와 범위

팔도무록은 조선 산골 마을에서 창귀가 씐 주민을 찾는 **싱글플레이 사회적 추론 웹 게임**이다. 플레이어는 항상 무당(유일한 점술가)이고, NPC 주민들과 행적 진술을 대조하고 캐묻고 투표해 씐 자를 가둔다. 승패와 진실은 결정론적 도메인 엔진이 정하고, 로컬 LLM은 추궁 답변의 말투만 덧붙인다. 플레이 로그는 이벤트로 쌓여 의도 해석기·씐 자 확률 모델의 학습과 평가에 쓰이고, 평가 관문을 통과한 모델만 서비스에 반영된다. 무료 게임이며 가상 광고주의 광고 경매·과금·집계 루프로 수익 모델을 보인다.

한 판의 흐름: 판 시작(프리셋·결말 선택) → 아침(실종 발표, 피해 장소·시간대 공개, 행적 진술) → 회의(캐묻기, 자유 입력·버튼, 고백, 기색) → 비밀 투표(결선, 무감금) → 밤(점괘, 씐 자 행동) → 종료 판정(G=0 → 무당 감금 → G≥V+M) → 결과·원한 단서·설문 → 복기.

### 1.1 포함 범위

- 도메인 엔진: 인원 N·창귀 수 K 파라미터, 프리셋 5/1·6/1(기본)·7/1·8/1·9/2·11/2, 배정 검증기, 점괘·기색·고백·투표·밤 처리, 원한 단서와 퇴마·한풀이 결말, 선택형 도전
- NPC: 네 층 상태(진실, 자기 지식, 믿음, 공개 기록), 투표 세 층(추정 → 결정 → 안전), 씐 자 전략, 행동마다 이유 코드·근거 ID
- 입력: 자유 입력(BERT 계열 의도 해석 + 규칙 매칭) → 발언 카드 확인, 버튼 입력
- 웹: Django 템플릿 + htmx + Alpine.js. 회의·진술표·투표·밤·결과·복기·수첩·설문·업데이트 내역 화면, 회의·밤 화면의 비주얼 노벨 연출(장면 배경 + 인물 초상 + 대화창), 마을 지도와 인물 초상
- LLM: llama.cpp(CPU 양자화) + LangGraph. 추궁 답변 표현(필수), 복기 해설·운영 분석 에이전트·템플릿 변형 사전 생성(확장)
- 데이터: MongoDB 판 문서 + 아웃박스, Kafka, Spark → Delta Lake(Garage), 봇 시뮬레이션(k3s Job)
- ML: 의도 해석기(규칙 기준선 → BERT), 씐 자 확률 모델(LightGBM), 평가 관문, MLflow 레지스트리, 카나리아·롤백, 밴딧, 사람 데이터 기반 재학습 루프
- 광고: 광고 서버(ad-01), MySQL 원장(Primary + Replica), eCPM 2등 가격 경매, 예산 예약·확정·해제·환입, 노출·클릭 유효 판정, 건별 대사, CTR·페이싱 갱신 루프, 합성 트래픽 검증
- 운영: VM 20대 배치, Prometheus·Grafana·경보, 구조화 로그, 장애 복구 시험, 런북, AWS 시연 환경과 비용 차단
- 개인정보: 가명 UUID, 동의 화면, 원문 마스킹, 삭제 요청·보존 기한 삭제
- 콘텐츠·에셋: 인물·비밀·단서·템플릿 대사(창작), 생성형 AI 사전 제작 이미지(생성 기록·규격 검사)
- 사용자 테스트(내부망, 목표 30명 × 4판), 증빙 문서·CI 검사, 사업 검토 데모 25분

### 1.2 제외 범위

- 실제 결제와 실제 광고주, 실제 광고 정산
- 모바일 앱, 동시 접속 멀티플레이
- GPU, 외부 유료 API
- 플레이어가 씐 자를 맡는 모드
- 몰래 하는 난이도 조절, 개인별 비공개 난이도
- 재방문율 측정(출시 후 지표로 BRD 3.1절에만 정의, 파일럿에서는 측정하지 않음), 사람 행동 예측 모델(이탈 예측, 사람 모방 봇)
- 출시(외부 공개)용 도메인 운영(파일럿 기간은 AWS 데모 환경까지)
- production 수준 HA·24시간 관제

## 2. 역할과 담당자

실제 담당자는 결정 대기(P-01, 11/5 09:00)다. 정해지면 `tasks/README.md` 1절에만 적고 이 문서는 역할 ID로 둔다.

### 2.1 담당자별 역할 분담

| 역할 ID | 흐름 | 담당 범위(코드 위치) | 태스크 파일 | PRD 검토자(제안) |
|---|---|---|---|---|
| `STK-ENGINE-001` | A 엔진 | `engine/domain` 규칙·NPC·결정 층, 실제 이미지 에셋 제작(초상·장면 배경, `content/images/`·`tools/assets/cutout.py`, 11/5 PM 결정으로 B에서 이전) | `tasks/A_engine.md` | `STK-MLAD-001`(의심 모델 포트·시뮬레이터 사용) |
| `STK-WEB-001` | B 웹·API | `server/`, `web/`, 광고 지면 연동·웹소켓 중계, 회의·밤 VN 연출과 VN 연출 규칙(`contracts/vn.md`), 사용자 테스트 리허설 지원·버그 분류, A 에셋 제출 검토(11/5 PM 결정) | `tasks/B_web.md` | `STK-INFRA-001`(저장소·로그·배포) |
| `STK-INFRA-001` | C 인프라/파이프라인 | `server/infra`, `pipeline/`, `ops/`, 광고 인프라, AWS | `tasks/C_infra.md` | `STK-WEB-001`(저장소·아웃박스 사용) |
| `STK-MLAD-001` | D ML·광고 | `intent/`, `ml/`, `engine/sim`, `ads/` | `tasks/D_ml_ads.md` | `STK-ENGINE-001`(엔진·관측 경계) |
| `STK-PM-001` | E PM | `content/`, `llm/`, 에셋 규격·임시 에셋·검사·검수, 사용자 테스트, 프리셋 반례 테스트·최종 파라미터 재실행·원한 단서 해금·복기용 이유 조회(11/5 PM 결정으로 A에서 이전, 코드는 `engine/`), 첫 초상 화풍 사전 확인, 통합 주관 | `tasks/E_pm.md`(INT 포함) | `STK-WEB-001`(LLM 폴링·설문 화면) |
| `STK-PRES-001` | S 데모 자료 트랙 | `docs/presentation/` | `tasks/P_presentation.md` | `STK-PM-001` |

- 검토자 짝은 인터페이스가 가장 많이 맞닿는 흐름으로 제안했다. P-01 결정 때 PM이 확정한다.
- 사람 판단 조건(말투, 의미 보존, 해설 인과, 이미지)의 검수자는 결과물 작성자와 다른 흐름 1명이다(`tasks/README.md` 3절). 이미지 검수는 11/11 E-11-10에서 검수자 E가 한 번에 한다(실제 에셋 작성자는 A, 11/5 PM 결정으로 B에서 이전. 스타일 가이드 확인만 11/5, 검수자 B).
- 모든 PR은 작성자가 아닌 1명의 승인이 있어야 병합된다(`tasks/README.md` 3절 ⑧, `main` 보호 규칙).

### 2.2 서비스 흐름

| 구분&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 흐름&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|
| 게임 요청 | 브라우저 → Django 응용 계층(명령 검증: `command_id`, `expected_state_version`) → 도메인 엔진 → MongoDB 판 문서(상태 + 진실 + 아웃박스 + 명령 기록 단일 쓰기) → 허용 정보만 화면으로 |
| 자유 입력 | 문장 → 의도 해석기(소비 없음, `intent_attempt` 기록) → 발언 카드 0~N장 → 확인·수정·재입력·취소 → 확인한 카드만 명령으로 실행 |
| 추궁 답변 | 캐묻기 수락 → 엔진이 반응 의도·사실 문장 확정 → 폴백 문장 먼저 표시 → LLM 작업(작업 ID) → 검증 통과 시 1초 폴링으로 교체, 실패·시간 초과면 폴백 유지 |
| 이벤트·학습 | 아웃박스 발행기 → Kafka(`paldo.*`) → Spark bronze·silver(판 종료 후 진실 조인) → Delta → 학습 → 평가 관문 → MLflow(candidate → canary → production) 또는 거부 기록 |
| 광고 | 화면 그린 뒤 광고 칸 지연 로드 → 광고 서버 경매·예산 예약 → 노출 비콘·클릭 리다이렉트 → 유효 판정·과금 확정 → MySQL 아웃박스 → Kafka(`ad.*`) → silver·gold·건별 대사 → CTR·페이싱 갱신(버전) → 다음 경매 |
| 사용자 데이터 반영 | 사람 문장·수첩·투표·설문 → 의도 해석기 재학습, 대역 봇 보정 → 시뮬레이션 재생성 → 씐 자 모델 재학습, 밴딧 비율 갱신(설문은 전략별 집계값으로 밴딧 보상에만, privacy.md 4절) → 관문 통과 시 적용(판 시작 때 버전 고정)과 게임 안 공개 |
| 실패 | 6절 실패 경계 표 |

## 3. 아키텍처 요구사항

### 3.1 계층과 의존 방향

| 계층 | 위치 | 역할 |
|---|---|---|
| 표현 | `web/`(템플릿·htmx 조각), `server/api/`(HTTP 핸들러) | 입력 검증, DTO 변환, 허용 정보만 직렬화. 규칙을 두지 않는다 |
| 응용 | `server/app/` | 명령 처리 5단계(중복 → 버전 → 전이 → 원자 저장 → 경합), 이벤트 봉투 작성, LLM 작업·광고 칸 조립 |
| 도메인 | `engine/domain/`, `ads/domain/`, `contracts/` | 게임 규칙·NPC 결정·승패, 광고 경매·예산·판정 규칙. 표준 라이브러리만 쓰고 단독 테스트한다 |
| 포트 | `engine/ports.py`, `server/app/ads_port.py` | 엔진·응용이 바깥을 부르는 인터페이스. 가짜 구현은 `engine/testing/`, `ads/testing/` |
| 인프라 | `server/infra/`(Mongo, LLM·의도 어댑터), `pipeline/`(Spark), `ops/`(배포·관측), `ads/app/`(MySQL·발행), `intent/`, `ml/`, `llm/` | 포트 구현, 외부 시스템 연동 |

- 의존 방향: 표현 → 응용 → 도메인, 인프라 → 도메인(포트 구현). 도메인은 Django, MongoDB, Kafka, LLM을 import하지 않는다.
- 시뮬레이터와 웹은 같은 엔진을 쓴다. 엔진은 이벤트를 반환만 하고 발행하지 않는다(`contracts/ports.md` 1절).
- 각 포트는 가짜 구현으로 먼저 개발하고, 진짜로 바꿔도 받는 쪽 코드를 고치지 않는다(`contracts/parallel_dev.md`, `tasks/README.md` 3절 교체표).

### 3.2 런타임 구성

| 구성요소 | 기술 | 배치(논리 서버) | 담당 |
|---|---|---|---|
| 웹·API, 아웃박스 발행기, 의도 해석 추론 | Django + htmx + Alpine.js | app-01 | B·C·D |
| 판 저장 | MongoDB Replica Set 3노드, `w=majority` | mongo-01~03 | C |
| 이벤트 | Kafka 3노드(KRaft), 보존 1일 | kafka-01~03 | C |
| 배치 | Spark Master 1 + Worker 1 | spark-master-01, spark-worker-01 | C |
| 학습 데이터·아티팩트 | Delta Lake on Garage | lake-storage-01 | C |
| 모델 관리·학습 | MLflow 추적·레지스트리 | nifi-01(ML 작업 서버) | C·D |
| 대량 시뮬레이션·재학습 실행 | k3s 2노드(Job, CronJob) | spark-worker-02, 03 | C·D |
| LLM | llama.cpp + LangGraph, 큐 우선순위 캐묻기 > 복기 > 운영 분석 | llm-01(노트북 3) 또는 같은 노트북 Windows 직접 실행(E-06-07 벤치로 결정) | E·C |
| 광고 서버 | Django 별도 서비스 | ad-01 | D·C |
| 광고 원장 | MySQL Primary + Replica | mysql-01, 02 | C·D |
| 관측 | Prometheus, Grafana, node_exporter | metrics-01 | C |
| 배포 자동화 | Ansible | bastion-01 | C |

- VM 20대의 노트북별 배치와 자원 근거는 기획서 6.3절이 원본이다. mysql-03, media-storage-01은 끈다.
- 기술 버전은 `contracts/tech_stack.md`의 기록값이며 11/5 실측(C-05-01, C-05-03)으로 확정한다. ML·LLM 작업도 Python 3.14 단일 환경이다(11/5 PM 확정, C-05-03 실측).
- 개발은 각자 PC의 Docker Compose 축소 구성(`ops/compose/dev.yml`), 통합·사용자 테스트는 VM, 시연은 AWS(범위 P-05)로 한다.

### 3.3 경계 원칙

- **상태와 로그의 일치**: 판 상태와 미발행 이벤트를 같은 판 문서에 쓴다(아웃박스). 게임 요청 경로는 Kafka 장애와 무관하게 진행된다.
- **진실의 경계**: 브라우저 응답과 학습 입력에는 진실을 넣지 않는다. 진실은 판 종료 후 `paldo.truth`로만 나가고 라벨로만 조인된다.
- **LLM의 경계**: LLM은 상태를 바꾸지 않고 사실을 더하지 않는다. 생성 답변과 폴백은 같은 정보량이다.
- **광고의 경계**: 게임 명령 처리 경로에서 광고를 부르지 않는다. 광고 실패는 빈 광고로 끝나고 게임 오류가 되지 않는다. 광고 서버에는 세션 가명 ID만 보낸다.
- **모델의 경계**: 모델 버전은 판 시작 때 고르고 판이 끝날 때까지 고정한다. 모델 실패·지연은 그 결정만 규칙 v0로 대체한다.

## 4. 기능·데이터·운영 요구사항 catalog

- 상태 값: `planned | in_progress | pass | fail | not_verified`. 지금은 모두 `planned`다.
- 우선순위: 4절의 요구사항 93개는 모두 Must다. "(확장)" 표시도 이번 범위에 포함되므로 Must로 본다.
- 요구사항은 기능 단위다. 세부 완료 조건은 연결된 태스크 문서에 있다. 태스크 완료(`check_evidence <ID> --complete` 통과)는 증빙이 갖춰졌다는 뜻이고, AC 판정은 11절 규칙대로 따로 한다.
- owner는 주 담당 역할이고, due는 연결 태스크 중 마지막 날짜다. "(확장)"은 기획서의 확장 단계 항목이며 범위에 포함된다.

### 4.1 게임 엔진 (A)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-ENG-001` | planned | 프리셋 6종으로 판을 만들고 역할·비밀·동선을 배정한다. 배정 검증기 위반 0, 같은 시드는 같은 판 | `BR-OBJ-001` | `AC-ENG-001` | A-05-01, E-05-03 | `STK-ENGINE-001` | 11/5 |
| `FR-ENG-002` | planned | 아침에 실종자와 피해 장소·시간대를 공개하고(0일째 포함) 주장 스키마에 맞는 행적 진술을 낸다 | `BR-OBJ-001` | `AC-ENG-001` | A-05-01, A-06-02 | `STK-ENGINE-001` | 11/6 |
| `FR-ENG-003` | planned | 회의·캐묻기·비밀 동시 투표·결선·무감금을 처리하고 종료 판정을 G=0 → 무당 감금 → G≥V+M 순서로 한다. 합법 명령·횟수 제한을 어기면 상태 불변으로 거부 | `BR-OBJ-001` | `AC-ENG-002` | A-05-02 | `STK-ENGINE-001` | 11/5 |
| `FR-ENG-004` | planned | 밤 처리, 점괘(밤 시작 시 판정, 하루 1회 공개, 거짓 공개 허용), 고백, 기색(거짓 주장에만, 주장당 1회 고정) | `BR-OBJ-001` | `AC-ENG-003` | A-06-01 | `STK-ENGINE-001` | 11/6 |
| `FR-ENG-005` | planned | 관측 투영 → 믿음 갱신 → 행동 선택 순서. 행동 선택은 진실을 읽지 못하고, 결정 직전 스냅샷과 이유 코드·근거 ID를 남기며, 진실은 판 종료 후에만 낸다 | `BR-OBJ-005` | `AC-ENG-004` | A-06-02, A-06-03 | `STK-ENGINE-001` | 11/6 |
| `FR-ENG-006` | planned | NPC 규칙 v0: 믿음 갱신, 행동 선택, 씐 자 전략, 비밀 주민 고백 | `BR-OBJ-001` | `AC-ENG-005` | A-07-01 | `STK-ENGINE-001` | 11/7 |
| `FR-ENG-007` | planned | 투표 세 층(추정 → 결정 → 안전)과 의심 모델 포트. 모델 실패·100ms 초과·버전 불일치면 그 결정만 규칙 v0로 대체하고 폴백 수를 센다 | `BR-OBJ-005` | `AC-ENG-005` | A-07-02 | `STK-ENGINE-001` | 11/7 |
| `FR-ENG-008` | planned | 창귀 2명 규칙(공동 밤 명령, 실행자, 팀 전략 4종 비율 외부 주입, 감금 공개형·비공개형 전환) | `BR-OBJ-001` | `AC-ENG-006` | A-08-01 | `STK-ENGINE-001` | 11/8 |
| `FR-ENG-009` | planned | 큰 판 회의 발언 축약(반복 진술 접기, 변경점만, 회의 축약)과 프리셋 선택·11인 판 화면 | `BR-OBJ-001` | `AC-ENG-006` | A-08-02, B-08-02 | `STK-ENGINE-001` | 11/8 |
| `FR-ENG-010` | planned | 원한 단서 해금(판 밖 진척, 중복 무시), 판 시작 때 결말 선택, 같은 가설 단서 3조각이면 한풀이, 선택형 도전 3종, 진척 장애 후 재처리 복구 | `BR-OBJ-001` | `AC-ENG-007`, `AC-ENG-009` | E-09-08, B-08-01, E-07-05, C-09-04(재처리 도구) | `STK-PM-001` | 11/9 |
| `FR-ENG-011` | planned | 6인 외 프리셋의 반례 테스트와 승률·판 길이 리포트. 공개 기준(P-08)을 통과한 프리셋만 연다 | `BR-OBJ-001` | `AC-ENG-008` | E-10-09, A-10-01, D-10-03, E-10-08 | `STK-ENGINE-001` | 11/10 |
| `FR-ENG-012` | planned | 복기용 이유 조회 `review_trace`(행동 시점 기록만, 선택 필드 `win_prob`) | `BR-OBJ-001` | `AC-WEB-006` | E-10-10 | `STK-PM-001` | 11/10 |
| `NFR-BAL-001` | planned | 6인·창귀 1, 피해 장소 공개 상태에서 대역 "논리 우선 봇" 승률 40~60%(1,000판 이상, 95% 구간). 모델 v0 연결 후와 최종 파라미터에서도 유지 | `BR-OBJ-001` | `AC-BAL-001` | A-08-03, A-10-01, E-11-11 | `STK-ENGINE-001` | 11/11 |

### 4.2 웹·API (B)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-WEB-001` | planned | `contracts/api.md` 2절의 엔드포인트 10개. 입력 JSON 엄격 검증(알 수 없는 필드 400) | `BR-OBJ-001` | `AC-WEB-001` | B-05-02 | `STK-WEB-001` | 11/5 |
| `FR-WEB-002` | planned | 명령 멱등성과 버전 충돌: 같은 `command_id`는 replay, 오래된 버전 409, 엔진 거부 422, 동시 명령 중 1건만 수락 | `BR-OBJ-007` | `AC-WEB-002` | B-05-02, B-06-02, C-06-01 | `STK-WEB-001` | 11/6 |
| `FR-WEB-003` | planned | 회의·진술표·투표·밤 화면(htmx 조각, 폼마다 `state_version`) | `BR-OBJ-001` | `AC-WEB-003` | B-05-03, B-06-01 | `STK-WEB-001` | 11/6 |
| `FR-WEB-004` | planned | 화면 정보 제한: 렌더된 HTML·JSON에 진실·NPC 믿음·타 NPC 개인 지식 0건 | `BR-OBJ-001` | `AC-WEB-004` | B-05-03, INT-07 | `STK-WEB-001` | 11/7 |
| `FR-WEB-005` | planned | 자유 입력 수명주기: 해석(소비 없음) → 카드 → 확인·수정·재입력·취소·버튼 전환·해당 없음. 실패 상태 5종 처리, 해석 후 버전이 바뀌면 실행 불가, 지원·미지원 예시 표시 | `BR-OBJ-003` | `AC-INT-001` | B-07-01 | `STK-WEB-001` | 11/7 |
| `FR-WEB-006` | planned | 추궁 답변 표시: 폴백 문장 먼저, `GET /games/{id}/renders/{job_id}` 1초 폴링, `final`이면 폴링 중지, 단계가 넘어가면 미완료 작업 취소(B-07-01 조건 7, 11/4 PM 결정) | `BR-OBJ-004` | `AC-LLM-002` | B-05-02, B-07-01, E-07-01, INT-09 | `STK-WEB-001` | 11/9 |
| `FR-WEB-007` | planned | 무당의 수첩(인물별 의심), 투표 직전 확신도, 판 종료 설문(재미·억울함·NPC 그럴듯함, 5점, 건너뛰기) | `BR-OBJ-002` | `AC-WEB-007`, `AC-UT-002` | B-07-02, E-06-04 | `STK-WEB-001` | 11/7 |
| `FR-WEB-008` | planned | 결과·원한 단서·결말·도전 선택 화면 | `BR-OBJ-001` | `AC-ENG-007` | B-08-01 | `STK-WEB-001` | 11/8 |
| `FR-WEB-009` | planned | 판 시작 때만 모델 버전 선택·고정, 사용자 테스트 세션은 카나리아 끔, 게임 안 "업데이트 내역" 화면 | `BR-OBJ-005` | `AC-ML-005` | B-09-01 | `STK-WEB-001` | 11/9 |
| `FR-WEB-010` | planned | 복기 화면: 종료 후에만(그 전 403), NPC 행동별 이유·근거, LLM 해설 폴링과 템플릿 대체, 승리 확률 그래프(있을 때) | `BR-OBJ-001` | `AC-WEB-006` | B-10-01 | `STK-WEB-001` | 11/10 |
| `FR-WEB-011` | planned | 마을 지도·인물 초상. 이미지는 `asset_url(content_id)`(manifest)로만 참조 | `BR-OBJ-001` | `AC-WEB-008`, `AC-ASSET-002` | B-05-03, B-10-02 | `STK-WEB-001` | 11/10 |
| `FR-WEB-012` | planned | 회의·밤 화면 비주얼 노벨 연출: 장면 배경(회의 고정·밤 공통 2장) 위 말하는 인물 sprite(평소 머리~허리, 전신 컷은 첫 등장·추방/처형 결과·엔딩·밤 습격 결과 4경우만, 대사창 숨김)와 대사창(이름표·대사, 한 줄씩 넘김). 밤 화면에는 sprite 없음. 표시 방식만 바꾸고 주장 ID·정정·철회·기색·근거·11인 축약 결과 유지, 배경·sprite 선택에 숨은 정보 사용 금지(엔딩도 같은 그림), 읽기 상태는 브라우저에서만(서버 명령 없음), 폴링 답변은 `job_id` 일치 문장만 교체, 다음 버튼·입력 중 단축키 무시·문장 단위 낭독·즉시 표시. 진술표·수첩·투표·결과·복기는 기존 화면(11/5 PM 결정) | `BR-OBJ-001` | `AC-WEB-010` | B-10-07, B-09-06 | `STK-WEB-001` | 11/10 |

### 4.3 의도 해석 (D·E)

| ID | 상태 | 요구사항 | BRD 목표 | AC | 태스크 | owner | due |
|---|---|---|---|---|---|---|---|
| `FR-INT-001` | planned | 규칙 기준선 해석기와 인물·근거 매칭 v0(분류 → 인물 → 근거 → 문맥 → 검증), 버전 기록 | `BR-OBJ-003` | `AC-INT-002` | D-05-01, D-07-01 | `STK-MLAD-001` | 11/7 |
| `FR-INT-002` | planned | 의도 예시·대조쌍·별칭 사전과 LLM 변형(계보 기록, 검수 통과분만) | `BR-OBJ-003` | `AC-INT-002` | E-06-02 | `STK-PM-001` | 11/6 |
| `FR-INT-003` | planned | BERT 계열 미세조정과 `IntentParser` 연결(설정 한 줄 전환) | `BR-OBJ-003` | `AC-INT-003` | D-05-02, D-07-03, D-09-01, B-09-02 | `STK-MLAD-001` | 11/9 |
| `FR-INT-004` | planned | 사람 평가 세트 `test_human` 동결(사용자 단위 분리, 학습 세트와 교집합 0, Delta 버전 기록) | `BR-OBJ-003` | `AC-INT-004` | E-09-02 | `STK-PM-001` | 11/9 |
| `NFR-INT-001` | planned | `test_human`에서 명령 전체 일치율 80% 이상이면서 기준선보다 높음. 미지원 오인율·잘못된 즉시 실행률(0) 보고 | `BR-OBJ-003` | `AC-INT-003` | D-09-01, D-10-01 | `STK-MLAD-001` | 11/10 |

### 4.4 ML·학습 루프 (D·C)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-ML-001` | planned | 봇 정책 4종(무작위, 논리 우선, 반복 의심형, 거짓말 공략형)과 플레이어 대역 봇, 시뮬레이션 리포트 | `BR-OBJ-001` | `AC-ML-001` | D-06-01 | `STK-MLAD-001` | 11/6 |
| `FR-ML-002` | planned | k3s Job 대량 시뮬레이션(6인 3,000판 이상, 다른 프리셋 각 300판 이상)이 Kafka → bronze까지 적재 | `BR-OBJ-005` | `AC-ML-001` | C-08-03, D-08-01 | `STK-INFRA-001` | 11/8 |
| `FR-ML-003` | planned | 씐 자 확률 모델 학습: 마을 관찰자 행만, 진실 누수 0, 게임 단위 분할, 미학습 전략 별도 평가, 창귀 2명 주변확률 | `BR-OBJ-005` | `AC-ML-002` | D-07-02, D-08-01 | `STK-MLAD-001` | 11/8 |
| `NFR-ML-001` | planned | 씐 자 모델 v0: 로그 손실 < 규칙 v0, ECE ≤ 0.05, 어댑터 추론 p95 100ms 이하 | `BR-OBJ-005` | `AC-ML-002` | D-08-01, D-08-04 | `STK-MLAD-001` | 11/8 |
| `FR-ML-004` | planned | MLflow 추적·레지스트리(아티팩트는 Garage). 데이터 버전·특징 버전·코드 커밋 태그가 없으면 등록 실패 | `BR-OBJ-005` | `AC-ML-003` | C-09-01 | `STK-INFRA-001` | 11/9 |
| `FR-ML-005` | planned | 평가 관문(`contracts/ml_lifecycle.md` 2절)을 코드로. 실패하면 등록하지 않고 거부 사유를 남긴다 | `BR-OBJ-005` | `AC-ML-003` | D-09-02, C-09-01 | `STK-MLAD-001` | 11/9 |
| `FR-ML-006` | planned | 밴딧(톰슨 샘플링)으로 씐 자 전략 비율을 판 종료마다 갱신. 보상식·1인 가중치 상한·최소 판 수는 P-16 | `BR-OBJ-005` | `AC-ML-004` | D-09-02 | `STK-MLAD-001` | 11/9 |
| `FR-ML-007` | planned | 재학습 루프 실증:<br>⓪ 의도 해석기 재학습<br>① 대역 봇 보정<br>② 시뮬레이션 재생성·씐 자 모델 재학습 → 관문 → 승격 또는 거부, 삭제 요청 데이터 0건, 시연용 단일 명령 | `BR-OBJ-005` | `AC-ML-005` | D-10-01, D-11-01 | `STK-MLAD-001` | 11/11 |
| `FR-ML-008` | planned | 재학습 CronJob(초기 수동 트리거), 카나리아 20%(관찰 기간·최소 판 수·중단 기준 P-17), 경보 → 롤백 → 다음 판부터 이전 버전 | `BR-OBJ-005` | `AC-ML-005` | C-10-01, B-09-01 | `STK-INFRA-001` | 11/10 |
| `FR-ML-009` | planned | (확장) 판 중 마을 승리 확률 예측, 자기 대전 강화학습 정책 비교, 프리셋 리포트 | `BR-OBJ-005` | `AC-ML-006` | D-10-03 | `STK-MLAD-001` | 11/10 |

### 4.5 LLM (E)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-LLM-001` | planned | LLM 벤치마크로 모델·실행 방식·스레드·배정 RAM 결정(후보별 라이선스 기록), 최종 자원 확정 | `BR-OBJ-004` | `AC-LLM-001` | E-06-07, C-06-04 | `STK-PM-001` | 11/6 |
| `FR-LLM-002` | planned | LLM 서버와 LangGraph: `LineRenderer` 수명주기 6상태, 시간 초과 시 실제 생성 취소, 큐 우선순위, `render_job` 이벤트 | `BR-OBJ-004` | `AC-LLM-002` | E-07-01 | `STK-PM-001` | 11/7 |
| `FR-LLM-003` | planned | 추궁 표현: 프롬프트, 검증기(형식·길이·금지 인물·장소·역할어·부정·단정 추가·사실 보존), 같은 정보량의 폴백. 말투 사람 평가(P-09) | `BR-OBJ-004` | `AC-LLM-003` | E-08-01 | `STK-PM-001` | 11/8 |
| `NFR-LLM-001` | planned | 추궁 전체 응답(큐 대기 + 처리 + 생성 + 검증) p95 15초 이하, 폴백률 보고 | `BR-OBJ-004` | `AC-LLM-003` | E-08-01, INT-09 | `STK-PM-001` | 11/9 |
| `FR-LLM-004` | planned | (확장) 복기 해설: `review_trace`의 근거 ID만 사용(사후 합리화 금지), 실패 시 템플릿 해설 | `BR-OBJ-004` | `AC-LLM-004` | E-10-01 | `STK-PM-001` | 11/10 |
| `FR-LLM-005` | planned | (확장) 운영 분석 에이전트: 읽기 전용 조회 인터페이스로 지표를 읽고 개선안 리포트, 수치 불일치 0 | `BR-OBJ-007` | `AC-LLM-005` | C-10-03, E-10-02 | `STK-PM-001` | 11/10 |
| `FR-LLM-006` | planned | (확장) 템플릿 변형 사전 생성(오프라인, 사람 검수 후 사용) | `BR-OBJ-004` | `AC-CONT-002` | E-09-03 | `STK-PM-001` | 11/9 |

### 4.6 광고 (D·B·C)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-AD-001` | planned | 지면 4곳만(회의·투표·밤 제외), 규격 `SLOT_SIZES`, 광고 칸 지연 로드, 게임 명령 경로의 광고 호출 0, 노출 비콘(50% 이상 1초)·서명 클릭 링크. 비콘은 게임 웹 웹소켓, 클릭·랜딩은 게임 웹 중계(브라우저는 게임 웹 출처에만 접속, 11/4 PM 결정) | `BR-OBJ-006` | `AC-AD-001`, `AC-WEB-009` | B-07-05, B-07-03 | `STK-WEB-001` | 11/7 |
| `FR-AD-002` | planned | 광고 서버 API 3종(serve·비콘·클릭), eCPM 2등 가격 경매, 가상 랜딩, admin 캠페인 관리, 이미지는 `image_path`로만 | `BR-OBJ-006` | `AC-AD-002` | D-05-03 | `STK-MLAD-001` | 11/5 |
| `FR-AD-003` | planned | MySQL 예산 원장: 원자적 예약, 확정(멱등), 만료 해제 작업, 환입, 원장·아웃박스 같은 트랜잭션, `ad.*` 발행 | `BR-OBJ-006` | `AC-AD-003` | D-06-02, C-06-03 | `STK-MLAD-001` | 11/6 |
| `FR-AD-004` | planned | 노출·클릭 유효 판정(pending → valid/invalid, 무효 사유 우선순위, 클릭은 노출 증거, 지연 이벤트 비과금) | `BR-OBJ-006` | `AC-AD-003` | D-05-03, D-06-02 | `STK-MLAD-001` | 11/6 |
| `FR-AD-005` | planned | 합성 트래픽 생성기: 정답 CTR 숨김, 실제 광고 API 경유, 피로 효과, 무효 클릭 주입, `syn:` 격리 | `BR-OBJ-006` | `AC-AD-004` | D-06-03 | `STK-MLAD-001` | 11/6 |
| `FR-AD-006` | planned | 광고 silver·gold 집계, 건별 대사 4종, 잠정·확정 마감, 원본 → gold 합계 대조 | `BR-OBJ-006`, `BR-OBJ-010` | `AC-AD-005`, `AC-AD-009` | C-08-02, D-10-02 | `STK-INFRA-001` | 11/8 |
| `FR-AD-007` | planned | CTR 갱신(베타 사전분포, 확정분 재계산)·톰슨 샘플링·페이싱이 실제 파이프라인을 거쳐 버전과 함께 광고 서버에 반영, 시나리오 1~5 정답 대비 채점 | `BR-OBJ-006` | `AC-AD-006` | D-08-02 | `STK-MLAD-001` | 11/8 |
| `FR-AD-008` | planned | 실제 광고 서버 연결(웹·광고 로그가 `serve_id`로 이어짐)과 장애 4종 격리, 회로 차단 | `BR-OBJ-006` | `AC-AD-007` | B-08-03, D-08-02 | `STK-WEB-001` | 11/8 |
| `FR-AD-009` | planned | 시나리오 6(맥락별 CTR), LightGBM CTR 비교와 관문, 페이싱 보정, 자동 입찰(`bid_version`), 사람 트래픽 분리 보고, 최종 리포트 | `BR-OBJ-006`, `BR-OBJ-010` | `AC-AD-006`, `AC-AD-008` | D-10-02, D-11-01 | `STK-MLAD-001` | 11/11 |
| `NFR-AD-001` | planned | 합성 검증: 예측 CTR 상대오차 20% 이내, 일 예산 소진 오차 10% 이내, 무효 과금 0건, 건별 대사 불일치 0건 | `BR-OBJ-006` | `AC-AD-006` | D-08-02, D-10-02, C-08-02, INT-11 | `STK-MLAD-001` | 11/11 |

### 4.7 데이터 파이프라인 (C)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-DATA-001` | planned | Mongo 저장소: CAS commit, 상태·진실·아웃박스·명령 기록 단일 문서 쓰기(`w=majority`), 진척·콘텐츠 버전 저장 | `BR-OBJ-007` | `AC-DATA-001` | C-06-01 | `STK-INFRA-001` | 11/6 |
| `FR-DATA-002` | planned | 아웃박스 발행기와 토픽 정의(파티션·키·보존·복제), acks=all 후 발행 표시, 소비자 `event_id` 중복 제거 | `BR-OBJ-007` | `AC-DATA-002` | C-06-02 | `STK-INFRA-001` | 11/6 |
| `FR-DATA-003` | planned | Spark → Delta: bronze MERGE 멱등, silver 학습 행(판 종료 후 라벨 조인, 중단 판 라벨 없음), `source`·`load_test` 분리, Delta 버전·오프셋 기록 | `BR-OBJ-005` | `AC-DATA-003` | C-07-01 | `STK-INFRA-001` | 11/7 |
| `FR-DATA-004` | planned | 중지·재개 무손실: Kafka 브로커·발행기·소비자·Spark 중지 후 재개해도 판별 `next_seq`(마지막 `seq` + 1) = 중복 제거 후 고유 발행 건수 = bronze 행 수, 대조 스크립트가 불일치 목록 출력 | `BR-OBJ-007` | `AC-DATA-004` | C-07-02, INT-11 | `STK-INFRA-001` | 11/11 |

### 4.8 운영·성능 (C·B)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-OPS-001` | planned | VM 20대 실측·점검표, 파일럿 역할 재배정, LLM 잠정 자원, 실행 시간표·디스크 예산, 개발 Compose와 Python 환경(ML·LLM 포함 3.14 단일, 11/5 확정, C-05-03) | `BR-OBJ-007` | `AC-OPS-001` | A-05-01, B-05-01, C-05-01, D-05-01, E-05-01(각 노트북 VM 점검 조건), C-05-02, C-05-03 | `STK-INFRA-001` | 11/5 |
| `FR-OPS-002` | planned | 모든 서비스·배치·스크립트의 구조화 로그(JSON 한 줄, 필수 필드)와 `/metrics` 또는 결과 JSON. 웹은 `request_id`·`X-Request-ID`·`/healthz`·`/readyz` | `BR-OBJ-007` | `AC-OPS-002` | B-05-01, D-05-03, E-07-01, INT-07 | `STK-INFRA-001` | 11/7 |
| `FR-OPS-003` | planned | Prometheus·Grafana: 전 서비스·VM 수집, 대시보드 6종(게임·LLM·모델·파이프라인·광고·자원), 경보 규칙, 대시보드 JSON 저장 | `BR-OBJ-007` | `AC-OPS-003` | C-08-01 | `STK-INFRA-001` | 11/8 |
| `FR-OPS-004` | planned | 장애 복구 시험 6종(Kafka 브로커, Mongo Primary, MySQL Primary, 광고 서버, LLM 서버, Spark 배치)과 런북·재실행 절차 | `BR-OBJ-007` | `AC-OPS-004`, `AC-OPS-006` | C-11-01, C-11-03 | `STK-INFRA-001` | 11/11 |
| `FR-OPS-005` | planned | AWS 계정 준비(모든 계정 예산 알림 50·80·100%), 비용 견적, 시연 환경 배포, 종료 절차, 비용 차단 | `BR-OBJ-007` | `AC-OPS-005` | C-09-03, C-10-02, C-12-02 | `STK-INFRA-001` | 11/12 |
| `FR-OPS-006` | planned | AWS 환경 전체 동작·속도 검증과 화면 검증 | `BR-OBJ-007` | `AC-OPS-005` | C-11-02, E-11-09 | `STK-INFRA-001` | 11/11 |
| `NFR-PERF-001` | planned | 게임 API(LLM 제외) p95 1초 이하(필수), 300ms 목표 대비 실측 보고. 부하 조건은 P-07 | `BR-OBJ-007` | `AC-PERF-001` | B-06-02, INT-09, INT-11 | `STK-WEB-001` | 11/11 |
| `NFR-PERF-002` | planned | 처리량·지연 표: 게임 API p95, 발행 지연, 배치 시간, LLM p95, 광고 p95를 측정 조건과 함께 | `BR-OBJ-007` | `AC-PERF-002` | INT-11 | `STK-INFRA-001` | 11/11 |
| `NFR-PERF-003` | planned | 성능 수치는 Prometheus 지표 또는 `tools/perf/` 측정 스크립트 결과 JSON만 인정 | `BR-OBJ-009` | `AC-GOV-002` | 전 태스크(공통 완료 정의 ④) | `STK-PM-001` | 11/12 |

### 4.9 개인정보·보안 (B·C·E)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-PRIV-001` | planned | 가명 UUID는 가입 때 계정에 발급(쿠키 발급 없음), 실명·연락처 입력란 없음, 동의 기록 없는 계정은 판 시작 불가, 테스트 세션·고정 시드 판, 운영자 화면 인증 | `BR-OBJ-008` | `AC-PRIV-001` | B-06-03, E-06-04 | `STK-WEB-001` | 11/6 |
| `FR-PRIV-002` | planned | 자유 입력·설문 원문 마스킹(규칙 버전 기록), 로그에는 원문 미기록 | `BR-OBJ-008` | `AC-PRIV-002` | B-07-01, C-09-02 | `STK-WEB-001` | 11/9 |
| `FR-PRIV-003` | planned | 저장 위치 표, 가명 ID 삭제 요청 처리(전 위치, Kafka는 P-18), 보존 기한 삭제 작업을 시험 데이터로 실행하고 2026-12-12 실행 예약, 재학습 입력 제외. 12/12 실제 삭제는 파일럿 종료 후 운영 의무(BRD `BR-OBJ-008` ④) | `BR-OBJ-008` | `AC-PRIV-003` | C-09-02, D-10-01, INT-11 | `STK-INFRA-001` | 11/11 |
| `NFR-SEC-001` | planned | 저장소에 비밀정보(사설 IP, 비밀번호, 키, 토큰) 0건. 접속 정보는 저장소 밖에만 | `BR-OBJ-008` | `AC-SEC-001` | E-05-02, C-06-03, C-10-02 | `STK-PM-001` | 11/10 |
| `NFR-SEC-002` | planned | 내부 사용자 테스트와 AWS 데모의 게임 웹은 HTTPS(wss 포함). 내부망은 자체 서명 프록시와 첫 접속 경고 안내, 보안 쿠키, 혼합 콘텐츠 0건(11/4 PM 결정, BRD `BR-OQ-023`) | `BR-OBJ-008` | `AC-SEC-002` | C-07-04, B-07-05, C-10-02 | `STK-INFRA-001` | 11/7 |
| `FR-AUTH-001` | planned | 개인 계정 가입·로그인·로그아웃. 가입은 로그인 ID·비밀번호·동의만(이름·연락처·이메일 없음), 모든 게임 화면·API 로그인 필수(JSON 401, 화면은 로그인 이동), CSRF 유지, 공개 가입(11/4 결정, 초대 코드 기능 `PALDO_SIGNUP_INVITE_CODE`는 남기되 파일럿은 미설정), 같은 접속 주소 가입 속도 제한 | `BR-OBJ-008` | `AC-AUTH-001` | B-06-04, C-06-03 | `STK-WEB-001` | 11/6 |
| `FR-AUTH-002` | planned | 비밀번호 규칙: 8자 이상, 소문자·숫자·특수문자 각 1개 이상, 흔한 비밀번호·로그인 ID와 비슷한 비밀번호 거부. Django 해시만 저장, 평문은 DB·로그 0건 | `BR-OBJ-008` | `AC-AUTH-002` | B-06-04 | `STK-WEB-001` | 11/6 |
| `FR-AUTH-003` | planned | 5회 연속 실패 15분 잠금(없는 ID와 같은 오류 문구), 세션 유효 기간 1,209,600초 명시, 복구는 관리자 초기화(`changepassword`)만 | `BR-OBJ-008` | `AC-AUTH-003` | B-06-04, E-06-04 | `STK-WEB-001` | 11/6 |
| `FR-AUTH-004` | planned | 가명 분리: 로그인 ID·비밀번호 해시는 계정 전용 MySQL 스키마에만, 게임 데이터·이벤트·로그·지표는 `player_pseudonym`만(광고는 기존대로 세션 가명만, 로그인 ID 0건). 판과 하위 객체는 소유 계정만 접근(다른 계정 404). `StartGameInput.player_pseudonym`은 서버가 채움(본문에 있으면 400). 삭제 요청 시 계정 행 삭제 | `BR-OBJ-008` | `AC-AUTH-004` | B-06-04, C-06-03, C-09-02 | `STK-WEB-001` | 11/11 |

### 4.10 콘텐츠·에셋 (E, 실제 에셋 제작은 A)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-CONT-001` | planned | 첫 플레이 버전 인물 6·비밀 6과 확장 주민 풀·비밀·단서. 콘텐츠 묶음 엄격 검증, 설화 원전 인용 0 | `BR-OBJ-001` | `AC-CONT-001` | E-05-03, E-08-02 | `STK-PM-001` | 11/8 |
| `FR-CONT-002` | planned | 템플릿 대사(행동 유형 × 성향 4종, 반응 템플릿에 `{fact}`, 템플릿당 변형 2개 이상) | `BR-OBJ-004` | `AC-CONT-002` | E-06-01 | `STK-PM-001` | 11/6 |
| `FR-ASSET-001` | planned | 스타일 가이드·파일 규격(용량 500KB 이하)·임시 에셋 전체, 생성 도구 확정(P-10)·생성 기록, 에셋 자동 검사 도구(CI) | `BR-OBJ-009` | `AC-ASSET-001` | E-05-05, A-08-04, E-08-07 | `STK-PM-001` | 11/8 |
| `FR-ASSET-002` | planned | 실제 에셋: 무당 여·남 2종 + 6명 sprite(전신 512×1280, 초상은 sprite에서 파생, 애니풍에 가까운 반실사 일러스트, 11/5 PM 결정), 장면 배경 2장(회의·밤), 확장 주민 sprite. 장소·UI 아이콘·마을 지도·가상 광고주 소재는 임시 에셋으로 시연(유형별 실제/임시 정책, 11/5 PM 결정) | `BR-OBJ-009` | `AC-ASSET-001` | A-09-05, A-11-03, A-11-04, E-08-07, E-11-10 | `STK-ENGINE-001` | 11/11 |
| `FR-ASSET-003` | planned | 임시 → 실제 에셋 교체 때 B·D 코드의 이미지 경로 변경 0줄, 화면·광고 테스트 통과 | `BR-OBJ-009` | `AC-ASSET-002` | E-11-10, B-05-03, D-05-03 | `STK-PM-001` | 11/11 |

### 4.11 사용자 테스트 (E)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp; | 요구사항 | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-UT-001` | planned | 테스트 준비: 일정·장소·인원(P-12), 진행 순서(동의 → 튜토리얼 → 4판 → 설문), 설문 문항 확정, 고정 시드 판 목록 | `BR-OBJ-002` | `AC-UT-001` | E-06-04, E-08-04, B-08-02 | `STK-PM-001` | 11/8 |
| `FR-UT-002` | planned | 사전 테스트(11/7)와 정식 테스트 운영, 테스트 중 수정 | `BR-OBJ-002` | `AC-UT-001` | E-07-02, E-09-01, A-09-02, B-09-03 | `STK-PM-001` | 11/9 |
| `FR-UT-003` | planned | 수집 확인과 리포트: 판 수 = 설문 이벤트 수(건너뜀 포함), 설문 평균·응답률, 판 번호별 승률, 판 길이·이탈 지점, 캐묻기·고백 유도율 | `BR-OBJ-002` | `AC-UT-002` | D-09-02, E-10-03, INT-09 | `STK-PM-001` | 11/10 |
| `NFR-UT-001` | planned | 사람 판 소요 시간 중앙값 10분 이하 | `BR-OBJ-001` | `AC-UT-001` | E-09-01, INT-09 | `STK-PM-001` | 11/9 |

### 4.12 저장소·통합·사업 검토 데모 (E·S)

| ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | BRD&nbsp;목표&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | AC&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 태스크&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | owner&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | due&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|---|---|
| `FR-GOV-001` | planned | GitHub 저장소(P-11, 조직 생성·초대, 공개), `main` 보호(필수 체크 4개, 작성자 외 승인 1명, 관리자 우회 불가), 담당 배정, 태스크 → 이슈 생성 | `BR-OBJ-009` | `AC-GOV-001` | E-05-01 | `STK-PM-001` | 11/5 |
| `FR-GOV-002` | planned | CI 필수 체크 4개(`core`, `django`, `evidence`, `docs`)와 `check_evidence`·`check_docs`·`check_secrets` | `BR-OBJ-009` | `AC-GOV-001` | E-05-02 | `STK-PM-001` | 11/5 |
| `FR-GOV-003` | planned | 모든 태스크의 증빙 문서(명령·출력·수치·로그·실제 소요 시간), 흐름별 트러블·검증 기록(데모 자료 트랙 입력), 모듈 문서 일괄 갱신 | `BR-OBJ-009` | `AC-GOV-002` | 전 태스크(공통 완료 정의 ③⑦), A-10-03, B-10-04, C-11-04, D-11-03 | `STK-PM-001` | 11/12 |
| `FR-GOV-004` | planned | 통합 1·2·3 판정(끝에서 끝까지 흐름 2개, 대조표, 릴리스 후보 SHA 재검사) | `BR-OBJ-009` | `AC-GOV-003` | INT-07, INT-09, INT-11, E-07-03, E-09-04, E-11-01, A-07-03, A-09-03, A-11-01, B-07-04, B-09-04, B-11-01, C-07-03, C-09-04, D-07-04, D-09-04, D-11-02 | `STK-PM-001` | 11/11 |
| `FR-PRES-001` | planned | 사업 검토 데모 구성안과 시연 시나리오: 25분 타임라인, 핵심 장면 4개, 장면별 실패 시 대체 화면 | `BR-OBJ-009` | `AC-PRES-001` | S-10-01 | `STK-PRES-001` | 11/10 |
| `FR-PRES-002` | planned | 데모 이미지·구간 자료·데모 자료 완성·시연 영상 백업. 수치마다 증빙 링크 | `BR-OBJ-009` | `AC-PRES-001` | S-11-01 ~ S-11-07 | `STK-PRES-001` | 11/11 |
| `FR-PRES-003` | planned | 리허설 2회(25분 이내)·사업 검토 데모·최종 산출물 확정(데모 자료 + 저장소 링크, 확정 SHA 기록) | `BR-OBJ-009` | `AC-PRES-002` | S-12-01 ~ S-12-05 | `STK-PRES-001` | 11/12 |

요구사항 수: 기능(FR) 83개, 비기능(NFR) 10개, 합계 93개. 모든 태스크(A~E, INT, S)가 4절 표의 태스크 칸에 1번 이상 나온다(범위 표기 `S-11-01 ~ S-11-07` 포함).

## 5. 데이터 계약

상세 규격은 계약 문서가 원본이다. 이 절은 무엇이 어디에 있는지만 적는다. 계약을 바꾸면 계약 버전을 올리고(`contract-x.y`, `ads-x.y`) 계약 테스트(`tests/contract/`)를 같이 고친다.

| 계약&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 위치&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 핵심 내용 | 소유&nbsp;&nbsp;&nbsp; |
|---|---|---|---|
| 도메인 | `contracts/domain.md` | 참가자·ID, 프리셋, 단계별 합법 명령(`PHASE_RULES`), 횟수(`LIMITS`), 투표·밤 처리 순서, 창귀 2명 규칙, 고백·기색, 정보 공개 범위, 11인 판 축약, 판 밖 진척, 배정 검증기 | A |
| 포트 | `contracts/ports.md` | 엔진 ↔ 바깥 인터페이스와 가짜 구현, 이벤트 처리 통일 규칙, 명령 처리 책임, 의심 모델 출력, LLM 비동기 작업, 광고 포트 보호값 | A·전원 |
| API | `contracts/api.md` | 응답 형식, 엔드포인트 10개, 명령 충돌 코드(200/409/422), 자유 입력 수명주기, LLM 폴링, 화면 정보 제한 | B |
| 이벤트 | `contracts/events.md` | `EventEnvelope`(`event_id = game_id:seq`, 버전·시드·출처 필드), 토픽 6개, 진실 경계, 분할 키, 라벨 출처, 고정 평가 세트, 광고 이벤트 | A·C·D |
| 의도 | `contracts/intent.md` | 라벨, 인물·근거 해소 순서, 결과 규칙, `intent_attempt` payload | D |
| 콘텐츠 | `contracts/content.md` | 구성 요소, 프리셋별 필요 수, 콘텐츠 버전, 대사 원칙 | E |
| 모델 수명주기 | `contracts/ml_lifecycle.md` | 학습 최소 조건, 평가 통과 기준, 등록·승격 순서, 카나리아·버전 고정, 성능 저하 감지와 롤백 | D·C·B |
| 광고 | `contracts/ads.md` | 데이터 모델(밀리원, 지면·규격), API, 경매, 예산, 유효 판정, 대사·마감, 합성 격리, 개인정보 | D |
| 개인정보 | `contracts/privacy.md` | 가명 ID, 원문 마스킹·보존, 수집 고지 문구, 학습 사용 범위 | B·C·D·E |
| 기술 스택 | `contracts/tech_stack.md` | 실행 환경, 구성 요소 버전(기록값, 11/5 실측), 테스트 실행 방식 | C |
| 병렬 개발 | `contracts/parallel_dev.md` | 흐름별 시작 세트, 계약 시나리오, 가짜 → 진짜 인수 조건 | 전원 |

### 5.1 저장소별 핵심 데이터

| 저장소&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 데이터&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 키·불변조건&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|
| MongoDB 판 문서 | 판 상태, 진실, 아웃박스(미발행 이벤트), 명령 기록, `next_seq` | `game_id`. `state_version` CAS. 한 문서 원자 쓰기. `seq`는 0부터 빈틈없이 증가 |
| MongoDB 진척·콘텐츠 | 플레이어 진척(단서, 결말 해금), 콘텐츠 버전 | `player_pseudonym`. 단서 중복 획득 없음. 콘텐츠 이전 버전 조회 가능, 단서 ID 삭제 금지 |
| Kafka | `paldo.game`, `paldo.observations`, `paldo.intent`, `paldo.llm`, `paldo.player`, `paldo.truth`, `ad.serve`, `ad.impression`, `ad.click` | 게임 키 `game_id`, 광고 키 `serve_id`. 보존 1일. 소비자는 `event_id`로 중복 제거 |
| Delta Lake | bronze(원본), silver(학습 행·광고 판정·조인), gold(광고 캠페인 × 지면 × 시간대) | `event_id` MERGE 멱등. 실행마다 Delta 버전·입력 오프셋 기록. `source`·`load_test`로 데이터 종류 분리 |
| MySQL 광고 원장 | 캠페인·소재·예산, 예약·확정·해제·환입 행, 아웃박스 | 금액은 정수 밀리원. `charge_id` 유일. 예약 합계 + 확정 합계 ≤ 일 예산 |
| MLflow | 실행 기록, 레지스트리(candidate·canary·production·previous), 거부 태그 | 데이터 버전·특징 버전·코드 커밋 태그 필수 |

### 5.2 데이터 종류 구분

| 종류&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | `source`&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 용도 | 쓰지&nbsp;않는&nbsp;곳&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|
| 사람 플레이 | `human` | 평가, 상대 모델 보정, 의도 해석기 학습 | 사용자 단위 분할 밖의 섞임 |
| 봇 시뮬레이션 | `bot` | 밸런스, 씐 자 모델 학습 | 사람 행동의 증거 |
| 녹화 판 | `scripted` | 형식 검증, 파이프라인 시험 | 모델 성능 판단 |
| 합성 게임 이벤트 | `synthetic` | 파이프라인 형식·처리량 시험 | 게임 모델(의도·씐 자) 학습·성능 판단 |
| 광고 합성 트래픽 | `synthetic`(`syn:` 캠페인) | 광고 알고리즘·회계·제어 루프 검증. 광고 CTR 갱신·모델 비교·시나리오 채점에는 쓴다(평가용 생성기는 학습용과 분리, 정답 CTR은 평가 전용) | 실제 광고 성과 보고, 실제 캠페인 상태와 섞기 |
| 부하 시험 | `load_test=true` | 처리량·복구 시험 | 학습, 광고 과금 |

## 6. 처리 흐름과 실패 경계

### 6.1 게임 명령 처리

```text
요청 검증 → 중복 확인(command_id) → 버전 확인(expected_state_version) → 엔진 전이 → 원자 저장(상태·진실·아웃박스·명령 기록) → 허용 정보 직렬화 → 응답
```

| 단계 | 통과 기준 | 실패 처리 |
|---|---|---|
| 요청 검증 | 알 수 없는 필드 없음, 타입 일치 | 400, 상태 불변 |
| 중복 확인 | 처음 보는 `command_id` | 저장된 응답 그대로 200 `replayed=true` |
| 버전 확인 | 저장된 `state_version`과 일치 | 409 `state_version_conflict` + 현재 화면. 자동 재적용 없음 |
| 엔진 전이 | 합법 명령, 슬롯 충족, 횟수 안 | 422 + 사유, 소비 없음, 상태 불변 |
| 원자 저장 | CAS 성공 | 경합에서 진 요청은 409 또는 replay |
| 직렬화 | 금지 키 0 | 테스트에서 실패(배포 차단) |

### 6.2 자유 입력

```text
문장 → 마스킹 → 해석(분류 → 인물 → 근거 → 문맥 → 검증) → 카드 0~N장 → 플레이어 반응 → (확인·수정만) 6.1 명령 처리
```

- 해석 단계는 소비가 없다. 실패 상태(`empty_input`, `unsupported_intent`, `missing_target`, `ambiguous_evidence`, `unparseable`)마다 화면 처리가 정해져 있다(`contracts/api.md` 4절).
- 해석 뒤 판 버전이 바뀌면 그 시도로는 실행할 수 없다(409, 다시 입력).
- MVP는 항상 확인 카드를 거친다. 잘못된 즉시 실행률은 0이어야 한다.

### 6.3 추궁 답변(LLM)

```text
캐묻기 수락 → 엔진이 반응 의도·사실 문장 확정 → 폴백 문장 표시 + LLM 작업 등록 → 큐 → 생성 → 검증 → 완료면 교체
```

| 실패 | 처리 |
|---|---|
| 시간 초과(`deadline_ms`) | 생성을 실제로 취소하고 폴백 유지(`timeout`) |
| 검증 실패(금지어, 사실 변경, 형식) | 폴백 유지(`failed`) |
| 판 단계 넘어감 | 미완료 작업 취소(`cancelled`), 늦은 결과 버림(`discarded`) |
| LLM 서버 정지 | 모든 답변이 폴백. 게임 진행은 계속된다 |

### 6.4 이벤트 적재

```text
아웃박스 → 발행기(acks=all 후 발행 표시) → Kafka → Spark bronze(MERGE) → silver(판 종료 후 진실 조인) → Delta 버전 기록
```

- 발행 실패는 재시도하고, 재발행은 소비자가 `event_id`로 제거한다.
- Kafka·발행기·Spark가 멈춰도 게임 요청은 계속된다. 재개 후 대조 스크립트로 유실 0·중복 0을 확인한다.
- 중단된 판에는 라벨을 붙이지 않는다. 진실은 학습 입력에 넣지 않는다.

### 6.5 재학습과 승격

```text
최소 조건 확인 → 학습(데이터·특징·코드 버전 기록) → 평가 관문 → candidate 등록 또는 거부 기록 → canary(신규 판 20%) → production, 이전 버전 previous
```

- 최소 조건 미달이면 재학습하지 않는다. 관문 실패는 거부 사유를 남기고 기존 모델을 유지한다(성공 경로).
- 폴백률 5% 초과, 추론 p95 100ms 초과면 즉시 롤백한다. 진행 중 판은 원래 버전을 유지한다.
- 사용자 테스트 세션에서는 카나리아를 끈다.

### 6.6 광고

```text
광고 칸 지연 로드 → AdGateway.fetch(연결 100ms, 전체 300ms, 동시 8) → 경매·예산 예약 → 노출 비콘/클릭 → 유효 판정 → 과금 확정 → 아웃박스 → Kafka → silver·gold → 대사 → CTR·페이싱 갱신
```

| 실패 | 처리 |
|---|---|
| 광고 서버 시간 초과·오류·혼잡 | 빈 광고(`filled=false`), 결과 코드 지표, 게임 영향 없음 |
| 연속 실패 5회 | 30초 동안 호출 생략(`circuit_open`) |
| 서명 오류·만료·부하 시험·serve 없음·중복·가시성 미달 | 무효(사유 기록), 과금 없음 |
| 예약 후 기한 내 미확정 | 해제 시각(serve + 30분 + 유예 2분) 뒤 해제 |
| 확정 뒤 silver 재판정으로 무효 | 원래 행 유지, 음수 환입 행 추가 |

## 7. 운영 요구사항

### 7.1 로그

- 형식: JSON 한 줄. 표준 출력 + 파일(`/var/log/paldo/<서비스>.jsonl`, 로컬은 `out/logs/`). 보존 7일.
- 필수 필드: `ts`(UTC ISO8601), `level`, `service`, `component`, `msg`.
- 해당하면 넣는 필드: `request_id`, `game_id`, `command_id`, `event_id`, `attempt_id`, `job_id`, `serve_id`, `charge_id`, `model_version`, `duration_ms`.
- 원문 자유 입력·설문 의견은 로그에 넣지 않는다. 오류는 `level=error`와 `error_type`. 예외를 삼키면 `warning` 이상을 남긴다.
- 규칙의 원본은 `tasks/README.md` 5절이다.

### 7.2 계측

- 이름: `paldo_<구성요소>_<측정>_<단위>`. 지연은 히스토그램(라벨 `route` 또는 `stage`, `status`), p50·p95는 Grafana `histogram_quantile`.
- 서비스마다 `/metrics`. 배치·스크립트는 Pushgateway 또는 결과 JSON(`out/perf/<ID>.json`).
- 측정 스크립트는 `tools/perf/`에 두고 시드·입력을 고정해 같은 명령으로 다시 돌린다. 증빙의 수치는 결과 JSON과 대조된다.
- 대시보드 6종과 경보 규칙은 `FR-OPS-003`.

### 7.3 보존과 정리

| 대상 | 보존 | 근거 |
|---|---|---|
| 서비스 로그 | 7일 | `tasks/README.md` 5절 |
| Kafka 토픽 | 1일 | `contracts/tech_stack.md` |
| 사용자 테스트 데이터(가명 ID가 붙은 모든 데이터) | 2026-12-12 삭제 | `contracts/privacy.md`, 결정 11/4 |
| 원문 입력 | 마스킹 저장, 2026-12-12에 라벨·특징을 포함해 가명 ID가 붙은 데이터 전부 삭제(11/4 결정) | `contracts/privacy.md` 2절 |
| 모델 파일·체크포인트·Delta 재처리분 | 노트북별 디스크 예산으로 관리 | C-05-02 |

### 7.4 환경과 비용

| 환경 | 용도 | 규칙 |
|---|---|---|
| 로컬 Compose | 개발 | 각자 PC. 가짜 LLM(`fixtures/llm`)으로 개발 가능 |
| 노트북 VM 20대 | 통합, 사용자 테스트 | 실행 시간표대로. 사용자 테스트 중에는 llm-01·ad-01·app-01에 학습을 올리지 않는다 |
| AWS | 시연, 이후 외부 공개 | 계정별 1인 6만 원. 모든 계정에 예산 알림. 범위는 P-05. 시연 후 종료 절차 |

### 7.5 접근과 비밀

- 접속 정보(IP, 계정, 비밀번호)는 저장소 밖 비공개 문서에만 둔다. 저장소·공유 문서·로그·채팅에는 논리 서버명만 쓴다.
- 설정은 환경 변수와 비밀 저장소로만 받는다. 광고 토큰 서명 키도 저장소에 넣지 않는다.
- 운영자 화면(Django admin)은 로그인 필수다.
- 게임 화면·API도 개인 계정 로그인 필수다(11/4 결정). 계정 DB는 광고와 분리한 전용 스키마·DB 계정이고 접속 정보는 `PALDO_AUTH_DB_*`로만 받는다. 비밀번호는 해시만 저장한다(`FR-AUTH-001~004`).

## 8. Acceptance criteria

- 상태는 모두 `planned`로 시작한다. 확인하지 못한 항목은 `not_verified`로 둔다.
- Evidence의 `evidence/<ID>.md`는 `docs/evidence/<태스크ID>.md`이고, 수치는 `docs/evidence/perf/<ID>/<이름>.json`(`tasks/README.md` 4절 "결과 JSON 형식")과 대조된다.

| AC&nbsp;ID&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Given&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | When&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | Then&nbsp; | Evidence&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 상태&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|---|---|
| `AC-ENG-001` | 프리셋 6종 × 시드 200개 | 판 생성과 0일째·1일째 아침 처리 | 배정 검증기 위반 0, 역할 수가 프리셋 표와 일치, 같은 시드 같은 판, 피해 장소·시간대가 공개 기록에 있음 | `evidence/A-05-01.md`, `evidence/A-06-02.md` | planned |
| `AC-ENG-002` | 계약 시나리오(결선 재동점, 무당 감금, 마지막 씐 자 감금 등) | 실제 엔진으로 재생, 위반 명령 주입 | 모든 checks 통과, 거부 시 `state_version` 불변, 종료 사유가 판정 순서대로 기록 | `evidence/A-05-02.md`, INT-07 #2 | planned |
| `AC-ENG-003` | 밤·점괘·고백·기색 시나리오 | 실제 엔진으로 재생 | 점괘 판정 시점·공개 횟수, 고백자 맑음, 기색 1회 고정이 테스트로 확인 | `evidence/A-06-01.md` | planned |
| `AC-ENG-004` | 실제 엔진 200판(`--preset base6 --seed 1`) | 인수 패키지 검사 스크립트 실행 | 행동 선택이 진실을 읽지 못함(테스트), 결정마다 같은 `snapshot_id`, `truth_revealed`는 종료 후에만, 모든 NPC 행동에 이유 코드, `seq` 빈틈 0 | `evidence/A-06-02.md`, `evidence/A-06-03.md` | planned |
| `AC-ENG-005` | 규칙 v0 NPC와 `FakeSuspicionModel` 실패 주입 | 500판 실행과 층별 테스트 | 합법 행동 위반 0, 마을 추론 위반 0, 실패한 결정만 `-fallback` 정책으로 대체 | `evidence/A-07-01.md`, `evidence/A-07-02.md` | planned |
| `AC-ENG-006` | duo9·duo11 프리셋, 11인 판 100판 | 공개형·비공개형 각 500판, 축약 전후 측정 | 위반 0, 승률·판 길이 표, 회의 화면 항목 수 축약 전후 기록 | `evidence/A-08-01.md`, `evidence/A-08-02.md`, `evidence/B-08-02.md` | planned |
| `AC-ENG-007` | 고백·추궁으로 단서를 얻는 판, 진척 반영 전 장애 주입 | 결말 선택 후 판 진행, 재처리 | 같은 가설 3조각이면 한풀이 해금, 중복 획득 0, 재처리로 복구. 결과 화면에 단서·결말 표시 | `evidence/E-09-08.md`, `evidence/B-08-01.md`, INT-11 #2·#14 | planned |
| `AC-ENG-008` | 프리셋 5, 7, 8, 9/2, 11/2 | 각 500판 시뮬레이션과 반례 테스트 | 승률·판 길이·무감금 빈도·고백률 표, 공개 여부가 P-08 기준으로 기록됨 | `evidence/A-10-01.md`, `docs/reports/presets.md` | planned |
| `AC-ENG-009` | 선택형 도전 3종(점 1회 제한, 비밀 있는 주민 3명, 목격 정보 축소) | 프리셋 옵션으로 각 300판 시뮬레이션, 판 시작 화면에서 선택 | 옵션별 승률 기록, 공개 여부가 P-08 기준으로 정해짐, 도전 선택 화면 동작 | `evidence/E-09-08.md`, `evidence/B-08-01.md` | planned |
| `AC-BAL-001` | 6인·창귀 1, 피해 장소 공개, 최종 파라미터 | 대역 "논리 우선 봇" 1,000판 이상 | 마을 승률 40~60%이고 95% 구간 보고, 마을 추론 위반 0, 명령·시드로 재현 | `evidence/A-08-03.md`, `evidence/A-10-01.md`, `evidence/E-11-11.md`, INT-11 #1 | planned |
| `AC-WEB-001` | 녹화 판 13단계 | API로 재생, 알 수 없는 필드 요청 | 엔드포인트 10개 응답, 단계별 `view`가 픽스처와 같음, 400 거부 | `evidence/B-05-02.md` | planned |
| `AC-WEB-002` | 같은 판 | 같은 `command_id` 재전송, 오래된 버전, 명령 20개 동시 전송 | replay 200, 409, 수락 1건이고 `state_version` 증가 1 | `evidence/B-06-02.md`, `evidence/C-06-01.md`, INT-07 #4 | planned |
| `AC-WEB-003` | 실제 엔진·웹·Mongo | 버튼만으로 6인 판 진행 | 3판 완주, 오류 0, 화면 렌더 p95 기록 | `evidence/B-05-03.md`, `evidence/B-06-01.md`, INT-07 #1 | planned |
| `AC-WEB-004` | 판 3개 전체 | 모든 HTML·JSON 응답을 금지 키 검사 | 진실·NPC 믿음·타 NPC 개인 지식 0건 | `evidence/B-05-03.md`, INT-07 #6 | planned |
| `AC-WEB-006` | 종료된 판 20개(녹화·시뮬레이션) | 종료 전후 복기 요청 | 종료 전 403, 모든 NPC 투표에 이유(누락 0), 행동 시점 기록만 사용, 해설 실패 시 템플릿 | `evidence/E-10-10.md`, `evidence/B-10-01.md` | planned |
| `AC-WEB-007` | 진행 중인 판 | 수첩 저장, 투표 직전 확신도 입력, 설문 제출·건너뜀 | `notebook_updated`·확신도·`survey_submitted` 이벤트가 `paldo.player` 토픽으로 나감(테스트), 건너뜀도 기록 | `evidence/B-07-02.md` | planned |
| `AC-WEB-008` | 실제 에셋(없으면 임시 에셋) | 지도·초상 화면 렌더 | 주민 풀 전원 초상과 지도 1장 표시, 장소 영역 좌표는 manifest에서 읽음(임시 지도는 안내용 고정 좌표), 임시 아이콘 옆 기능·장소 이름 텍스트, 화면 하단 이미지 출처 표기, 이미지 총 용량과 첫 화면 로드 시간 기록 | `evidence/B-10-02.md` | planned |
| `AC-INT-001` | 자유 입력 화면 | 카드 수정, 다중 의도, 문맥 누락, 미지원, 버전 변경 후 실행 | 각 반응과 실패 상태 5종이 계약대로 처리, 해석 단계 소비 0, `intent_attempt`에 마스킹된 원문 | `evidence/B-07-01.md`, INT-09 #3 | planned |
| `AC-INT-002` | 시드 세트(계보 단위 분할) | 기준선 해석기 평가 | 해석 5단계 단위 테스트 통과, 매크로 F1·명령 전체 일치율·미지원 검출률 기준값 기록, 변형 문장은 검수 통과분만 | `evidence/D-05-01.md`, `evidence/E-06-02.md` | planned |
| `AC-INT-003` | 동결된 `test_human` | BERT 해석기 평가 | 명령 전체 일치율 80% 이상이면서 기준선보다 높음(200문장 미만이면 부트스트랩 95% 구간, 겹치면 개선 아님), 잘못된 즉시 실행률 0, 해석 지연 p95 기록 | `evidence/D-09-01.md`, `evidence/D-10-01.md` | planned |
| `AC-INT-004` | 사전 테스트와 11/9 오전 문장 | 13:00 동결 | 사용자 단위 분리, 평가·학습 세트 문장·사용자 교집합 0, Delta 버전 기록 | `evidence/E-09-02.md` | planned |
| `AC-ML-001` | 봇 정책 4종, k3s | 각 200판, 6인 3,000판 이상 + 프리셋별 300판 이상 | 합법 위반 0, 같은 시드 같은 결과, 결과가 Kafka(`source=bot`) → bronze 적재, 리포트 표 생성 | `evidence/D-06-01.md`, `evidence/C-08-03.md`, `evidence/D-08-01.md` | planned |
| `AC-ML-002` | 6인 판 평가 세트 | 씐 자 모델 v0 학습·평가 | 특징의 진실 필드 0, 로그 손실 < 규칙 v0, ECE ≤ 0.05, 추론 p95 100ms 이하, 실패 시 엔진 폴백 | `evidence/D-07-02.md`, `evidence/D-08-01.md` | planned |
| `AC-ML-003` | 가짜 모델과 실제 모델 | 필수 태그 누락 등록, 관문 기준별 통과·실패 입력 | 태그 없으면 등록 실패, 관문 실패면 미등록 + 거부 태그, 기준별 단위 테스트 통과 | `evidence/C-09-01.md`, `evidence/D-09-02.md` | planned |
| `AC-ML-004` | 판 종료 보상 입력 | 밴딧 갱신 | P-16 보상식대로 비율 갱신, 1인 가중치 상한·최소 판 수 경계 테스트 통과, 중복 보상 0 | `evidence/D-09-02.md`, INT-11 #3 | planned |
| `AC-ML-005` | 사용자 테스트 문장·수첩·투표 | 재학습 → 관문 → 승격 또는 거부, 가짜 악화 신호 주입 | 전 실행에 데이터·특징·코드 버전, 카나리아 → production 또는 거부 기록, 롤백 후 다음 판부터 이전 버전, 진행 중 판 버전 유지, 삭제 요청 데이터 0, 업데이트 내역 화면에 승격 기록 | `evidence/D-10-01.md`, `evidence/C-10-01.md`, `evidence/B-09-01.md`, `evidence/D-11-01.md` | planned |
| `AC-ML-006` | 시뮬레이션 판 | 승리 확률 예측·강화학습 정책 비교 | 로그 손실·보정도 표, 모델 출력이 `review_trace.win_prob`로 복기 화면 그래프에 표시되고 Grafana 모델 대시보드에 "예측 평균 vs 실제 승률" 패널, 같은 시드 1,000판 비교 표(나쁘면 그대로 보고), `docs/reports/presets.md` 필수 열에 빈칸 0 | `evidence/D-10-03.md`, `evidence/B-10-01.md` | planned |
| `AC-LLM-001` | 추궁 요청 픽스처, 3~4B 후보 2개 이상 + 7~8B 1개 | 단계별 탈락 측정 | 모델 1개·실행 방식·스레드·배정 RAM 결정과 라이선스 기록. 15초 조합이 없으면 그 사실과 차선을 기록 | `evidence/E-06-07.md`, `evidence/C-06-04.md` | planned |
| `AC-LLM-002` | LLM 서버와 웹 | 시간 초과, 단계 넘김, 늦은 응답, 서버 정지 주입 | 6상태 수명주기, 실제 생성 취소 로그, 폴백 먼저 표시 후 교체, `final`이면 폴링 중지, `render_job` 이벤트에 버전·시간·폴백 | `evidence/E-07-01.md`, INT-09 #5 | planned |
| `AC-LLM-003` | 추궁 예시 100건, 위반 예시 20개 이상 | 생성·검증 측정, 말투 사람 평가 | 위반 예시 전부 거부, 폴백 문장에 사실 문장 100% 포함, 전체 응답 p95 15초 이하, 폴백률·실패 사유 분포, P-09 방식 평가 결과 | `evidence/E-08-01.md`, `docs/evidence/perf/E-08-01*.json` | planned |
| `AC-LLM-004` | 종료된 판 20개 | 복기 해설 생성 | 해설의 근거 ID가 모두 `review_trace`에 있음(판 20개 통과율·지연 p95 기록), 검수자(A)가 기준표(근거를 반대로 설명하지 않음, 없는 인과를 만들지 않음)로 10개 판정해 위반 0. 위반이 있으면 프롬프트·검증기 수정 후 새 10개를 다시 판정해 위반 0 | `evidence/E-10-01.md` | planned |
| `AC-LLM-005` | 조회 인터페이스(읽기 전용) | 에이전트 3회 실행 | 리포트 수치와 조회 결과 불일치 0, 쓰기 권한 없음 | `evidence/C-10-03.md`, `evidence/E-10-02.md`, `docs/reports/ops_agent/` | planned |
| `AC-AD-001` | 가짜 광고 서버 | 전 화면 렌더, 명령 처리 | 광고는 지면 4곳에만, 규격 일치, 명령 처리 중 광고 호출 0, 결과 코드 지표 | `evidence/B-07-03.md` | planned |
| `AC-WEB-009` | 로그인·비로그인 브라우저, 가짜 광고 서버(지연·실패 주입) | `/ws/game/` 연결, `ad.impression`·잘못된 메시지 전송, `/ads/click`·`/ads/landing` 요청, 연결 끊기 | 비로그인 4401, 다른 출처 거부, 오류 메시지 뒤 연결 유지, 비콘 중계 `ack`, 클릭은 실패해도 302, 자동 재연결과 대기열 20개, 게임 API p95 증가 50ms 이하 | `evidence/B-07-05.md` | planned |
| `AC-WEB-010` | 임시 배경(11/10)·실제 배경(11/11), 6인 판·11인 녹화 판 | VN 화면으로 완주, 조각 교체·409·단계 변경·새로고침, 폴백 표시 중 `final` 도착과 늦은 응답, 키보드만 조작, 폭 360px·확대 200%, 전신 컷 4조건(첫 등장, 추방·처형 결과, 엔딩, 아침 회의의 밤 습격 결과 발표) | 기존 회의 화면과 주장 목록 같음, 전신 컷은 4조건에서만 보이고 그 밖에는 0, 같은 키는 읽던 위치 유지·단계 변경 때 초기화, `job_id` 일치 문장만 교체, 입력 중 단축키 무시, sprite·선택지 가림 0, 숨은 정보 기반 배경 0·밤 화면 sprite 0, 금지 키 0, 판당 읽기 시간 기록 | `evidence/B-10-07.md`, `evidence/A-11-03.md` | planned |
| `AC-AD-002` | 경매·판정·예산 픽스처 | HTTP 경유 재생, admin으로 캠페인 생성·중지 | 기대값과 같음, 다음 serve부터 반영, 랜딩은 사이트 안, 이미지 파일명이 코드에 없음, serve p95 기록 | `evidence/D-05-03.md` | planned |
| `AC-AD-003` | MySQL 원장 | 동시 예약 100건, 같은 `charge_id` 재확정, 만료·지연 이벤트, 무효 재판정 | 예약 + 확정 ≤ 일 예산, 재확정 효과 없음, 만료 해제, 지연 이벤트 비과금, 환입 1회 | `evidence/D-06-02.md`, INT-11 #13 | planned |
| `AC-AD-004` | 합성 생성기 설정(무효 0·5·20%) | 실제 광고 API로 트래픽 생성 | 정답 파일은 평가 전용, 서버가 고른 광고만 노출, 각 비율에서 무효 과금 0, `syn:` 격리 테스트 통과, 시드 재현 | `evidence/D-06-03.md` | planned |
| `AC-AD-005` | 합성 트래픽 1회분 | silver·gold·대사·마감 실행 | 건별 대사 4종 불일치 0, 원본 → gold 합계 대조표, 잠정·확정 마감 둘 다 저장 | `evidence/C-08-02.md`, INT-11 #4 | planned |
| `AC-AD-006` | 시나리오 1~6, 평가용 생성기(시드 3개 이상) | 실제 파이프라인 경유 갱신과 정답 대비 채점 | CTR 상대오차 20% 이내, 소진 오차 10% 이내, 무효 과금 0, 대사 불일치 0, 갱신 버전이 다음 serve에 나타남, 결론 한정 문구 | `evidence/D-08-02.md`, `evidence/D-10-02.md`, `docs/reports/ads_scenarios.md`, INT-09 #2 | planned |
| `AC-AD-007` | 실제 광고 서버, 장애 4종(중단·무응답·3초 지연·오류) | 각 5분 `api_bench.py` 부하 | 게임 API p95 증가 50ms 이하, 게임 오류율 증가 0, 회로 차단 지표, 같은 `serve_id`의 웹·광고 로그 | `evidence/B-08-03.md`, INT-09 #9 | planned |
| `AC-AD-008` | 광고 CTR 모델 후보, CPC 캠페인 gold 집계 | LightGBM 관문·카나리아·악화 신호 주입, 입찰가 갱신 작업 실행 | 채택이면 serve 이벤트 `ctr_version`이 새 버전으로 바뀌고 롤백 후 이전 버전으로 돌아옴(거부면 서빙 버전 불변), 입찰가가 상·하한 안에서 바뀌고 `bid_version` 증가가 다음 경매 serve에 나타남, 상한 초과 갱신은 잘림, 실현 CPC와 목표 CPC 차이 표 | `evidence/D-10-02.md` | planned |
| `AC-AD-009` | 합성 트래픽 시나리오 1~6 결과, C-08-02 집계 정의서 | 광고 수익 지표 요약 표 작성 | 채움률·실현 eCPM·예산 소진율·무효 노출률·무효 클릭률·무효 과금 건수가 시나리오별로 기록되고 무효 과금 0건(목표치 없음, `BR-OBJ-010`) | `docs/reports/ads_scenarios.md`, D-10-02 #7 |
| `AC-DATA-001` | VM Mongo 레플리카셋 | 동시 commit 20개, 진행 중 판이 있는 상태에서 콘텐츠 새 버전 적재 | 성공 1건, 단일 문서 원자 쓰기, 진행 중 판은 시작 때 고정한 콘텐츠 버전으로 완주하고 새 판은 새 버전을 씀, 이전 버전 조회 가능, commit 지연 p50·p95 | `evidence/C-06-01.md`, INT-11 #14 | planned |
| `AC-DATA-002` | 토픽 정의 | 발행·소비, 같은 이벤트 재발행 | 토픽표대로 생성, acks=all 후 발행 표시, 중복 제거, 발행 지연 p95·미발행 건수 지표 | `evidence/C-06-02.md` | planned |
| `AC-DATA-003` | A-06-03의 200판 | bronze·silver 배치 2회 | 행 수 동일(멱등), 이벤트 수 = bronze 행 수, 종료 판 수 = 라벨 붙은 판 수, Delta 버전·오프셋 기록 | `evidence/C-07-01.md`, INT-09 #1 | planned |
| `AC-DATA-004` | 판 진행 중 | Kafka 브로커 1대·발행기·소비자·Spark 중지 후 재개 | 판별 `next_seq`(마지막 `seq` + 1) = 중복 제거 후 고유 발행 건수 = bronze 행 수, 유실 0·중복 0, 게임 API 오류 0 | `evidence/C-07-02.md`, INT-07 #5, INT-11 #4·#5 | planned |
| `AC-OPS-001` | VM 20대, 개발 PC | 실측과 환경 설치 | 기동 여부·자원·버전 표, 재배정 결과, 실행 시간표·디스크 예산, Compose 기동과 Python 3.14 필수 패키지 import 결과 | `evidence/C-05-01.md`, `evidence/C-05-02.md`, `evidence/C-05-03.md` | planned |
| `AC-OPS-002` | 구성요소 목록 | 각 구성요소 동작 | 구조화 로그·`/metrics`(또는 결과 JSON) 누락 0, 증빙마다 로그 발췌 3줄 이상 | INT-07 #9, 각 태스크 증빙 "로그" 절 | planned |
| `AC-OPS-003` | 전 서비스 가동 | 수집·대시보드·경보 확인 | 대시보드 6종 JSON 재로딩 가능, 경보 규칙(롤백 신호 4종 + API p95 + 미발행 증가) 동작 | `evidence/C-08-01.md` | planned |
| `AC-OPS-004` | VM 실제 스택 | 장애 6종 주입 | 각각 복구 시간·유실·중복·게임 오류율 표, 유실·중복 0, RTO는 P-13(미정이면 측정·보고), 런북 | `evidence/C-11-01.md`, `evidence/C-11-03.md`, INT-11 #7 | planned |
| `AC-OPS-005` | AWS 계정(P-05 범위) | 예산 알림 설정, 배포, 종료 절차 실행 | 모든 계정 알림 3단계, 계정별 견적 6만 원 이하, 상태 확인 전부 성공, 종료 → 재기동 확인, 시연 후 비용 차단 | `evidence/C-09-03.md`, `evidence/C-10-02.md`, `evidence/C-11-02.md`, `evidence/E-11-09.md`, `evidence/C-12-02.md` | planned |
| `AC-OPS-006` | VM 실제 스택, C-11-03 런북 | 광고 DB 지연·광고 이벤트 10배·노트북 3 CPU 압박 주입, C가 아닌 담당자 1명이 런북으로 기동 → 대사 실행 | 각 압박에서 게임 API p95 1초 이하·오류율 증가 0, 런북 실행의 막힌 곳·걸린 시간 기록, 막힌 곳은 런북 수정 후 재실행 성공 | `evidence/C-11-01.md`, `evidence/C-11-03.md` | planned |
| `AC-PERF-001` | P-07 부하 조건 | `api_bench.py` 실행 | 게임 API p95 1초 이하, 300ms 목표 대비 실측값 | `evidence/B-06-02.md`, INT-09 #8, `docs/evidence/perf/` | planned |
| `AC-PERF-002` | 측정 조건(부하·시간·데이터 크기) 명시 | 통합 3 측정 | 게임 API·발행 지연·배치 시간·LLM·광고 p95 표 | INT-11 #6 | planned |
| `AC-PRIV-001` | 가입한 계정 | 동의 기록 없는 계정의 판 시작 시도, 세션 지정 | 가명 UUID가 계정에 저장(쿠키 발급 없음), 실명·연락처 입력란 없음, 동의 기록 없으면 시작 불가, 고정 시드 판 배정, 사람 판 이벤트에 가명·세션 ID | `evidence/B-06-03.md` | planned |
| `AC-PRIV-002` | 전화번호·이메일·주민번호 형식이 든 입력 | 저장·로그 기록 | 저장 원문은 `<MASK>`, 마스킹 규칙 버전 기록, 로그에 원문 없음 | `evidence/B-07-01.md`, `evidence/C-09-02.md` | planned |
| `AC-PRIV-003` | 시험용 가명 ID 데이터(실제 참가자 데이터 아님) | 삭제 요청 1건과 보존 기한 작업 실행 | 저장 위치 표 전체에서 0건(Delta VACUUM 포함, Kafka는 P-18 결정 시점 기준), 재학습 입력 0건, 2026-12-12 실제 삭제 실행 예약과 방법이 런북에 있음 | `evidence/C-09-02.md`, `evidence/D-10-01.md`, INT-11 #9 | planned |
| `AC-AUTH-001` | 비로그인 상태, 초대 코드 설정 있음·없음 | api.md 2절 모든 엔드포인트·화면 순회, 가입 시도, CSRF 토큰 없는 POST | 비로그인 JSON 401·화면 로그인 이동, 가입 입력란은 ID·비밀번호·확인·동의(·초대 코드)뿐, 코드 설정 시 불일치 가입 거부·미설정 시 공개 가입, 같은 접속 주소 10분 6번째 가입 요청 429, CSRF 없는 POST 403 | `evidence/B-06-04.md` | planned |
| `AC-AUTH-002` | 규칙별 거부·통과 비밀번호 | 가입 시도 후 DB·로그 검색 | 규칙마다 거부 사례 거부·통과 사례 통과, 저장 값은 Django 해시 형식, 평문 0건 | `evidence/B-06-04.md` | planned |
| `AC-AUTH-003` | 고정 시계 | 같은 ID로 5회 연속 실패 후 맞는 비밀번호, 15분 경과 후 재시도, 없는 ID 시도 | 잠금 중 거부·15분 뒤 성공·성공 시 횟수 초기화, 없는 ID와 틀린 비밀번호 문구 동일, 설정 `SESSION_COOKIE_AGE` = 1,209,600, `changepassword` 절차 문서화 | `evidence/B-06-04.md` | planned |
| `AC-AUTH-004` | 테스트 계정 2개, 계정·광고 DB 계정 | 가입 → 로그인 → 판 1개(실패 경로 포함) → 로그·이벤트·`/metrics`·광고 요청 검색, 본문에 `player_pseudonym` 넣은 판 시작, 상대 계정 판 접근, 교차 스키마 접근, 삭제 요청 | 로그인 ID 검색 0건, 본문 가명 400, 상대 판 404, 계정·광고 DB 계정의 상대 스키마 접근 거부, 삭제 후 계정 행 0건 | `evidence/B-06-04.md`, `evidence/C-06-03.md`, `evidence/C-09-02.md` | planned |
| `AC-SEC-001` | 저장소 전체 | `python -m tools.check_secrets` | 비밀정보 의심 0건, 규칙 위반 PR에서 검사가 실패함 | `evidence/E-05-02.md`, CI `core` | planned |
| `AC-SEC-002` | app-01 HTTPS 프록시 뒤 게임 웹 | 브라우저로 HTTPS 접속 → 가입·로그인 → 판 시작, 프록시 경유·직접 `api_bench.py` 1분씩 | `/ws/game/` 101(wss), 혼합 콘텐츠 경고 0, 광고 서버 주소 요청 0, 세션·CSRF 쿠키 `Secure`, 프록시 추가 지연 p50·p95 표 | `evidence/C-07-04.md` | planned |
| `AC-CONT-001` | 콘텐츠 묶음 | 엄격 검증, 배정 검증기 6인 시드 200개 | 검증 통과, 별칭 고유, 위반 0, 원전 인용 0 | `evidence/E-05-03.md`, `evidence/E-08-02.md` | planned |
| `AC-CONT-002` | 템플릿·변형 | `tools/check_templates.py`, 검수 | 반응 템플릿 `{fact}` 포함, 자리표시자 오탈자 0, 템플릿당 변형 2개 이상, 사전 생성 변형은 검수 통과분만 | `evidence/E-06-01.md`, `evidence/E-09-03.md` | planned |
| `AC-ASSET-001` | 규격표·콘텐츠 ID 목록 | `check_assets.py`(임시 `--placeholder`, 실제 기본, 시연 세트 `--allow-placeholder place,ui,map,ad`) | 에셋 유형별 기대 키 집합(인물 ID, 장면 ID, `Place`, 아이콘 ID, `creative_id` × 지면 규격)마다 파일 정확히 1개, 실제 필수 유형(초상·sprite·배경)의 임시 파일 0, 초상-sprite 쌍 불일치 0(초상은 sprite 파생), sprite 기준선 자동 검사 위반 0, 누락 0·남는 파일 0, manifest 항목 = 기대 키 수, 규격·용량·생성 기록 위반 0(임시 에셋은 생성 기록 면제), 스타일 가이드 검수 기록(E-05-05), 모든 실제 파일(sprite와 파생 초상 포함, 원본·합성 화면)의 에셋1·5 일괄 검수 기록(E-11-10) | `evidence/E-05-05.md`, `evidence/E-08-07.md`, `evidence/E-11-10.md` | planned |
| `AC-ASSET-002` | 임시 에셋으로 개발된 화면·광고 | 실제 에셋으로 같은 경로 덮어쓰기 | B·D 코드 이미지 경로 변경 0줄, 화면·광고 테스트 통과 | `evidence/E-11-10.md` | planned |
| `AC-UT-001` | 테스트 준비 완료(P-12) | 사전·정식 사용자 테스트 | 목표 30명 × 평균 4판 대비 실제 참가자 수·판 수(미달이면 원인), 소요 시간 중앙값 10분 이하, 완주율, 설문 응답률, 원하는 행동 완료율 표 | `evidence/E-06-04.md`, `evidence/E-07-02.md`, `evidence/E-09-01.md`, INT-09 #10 | planned |
| `AC-UT-002` | 사용자 테스트 데이터 | 수집 확인과 리포트 작성 | 판 수 = 설문 이벤트 수(건너뜀 포함), 설문 평균과 응답 수, 판 번호별 승률, 이탈 지점, 미달 시 원인 분석 | `evidence/D-09-02.md`, `evidence/B-07-02.md`, `docs/reports/user_test.md`, INT-09 #7 | planned |
| `AC-GOV-001` | GitHub 저장소(P-11) | 규칙 위반 PR 3개 생성 | 필수 체크 4개가 각각 해당 위반에서 실패, `main` 보호 동작, 이슈 수가 `--dry-run`과 일치 | `evidence/E-05-01.md`, `evidence/E-05-02.md` | planned |
| `AC-GOV-002` | 완료 처리할 태스크 | `python -m tools.check_evidence <ID> --complete` | 조건 번호마다 통과 행, 수치가 결과 JSON과 일치, 실제 소요 시간 숫자, 문서 검사 통과 | `docs/evidence/<ID>.md`, CI `evidence`·`docs` | planned |
| `AC-GOV-003` | 통합일(11/7, 11/9, 11/11) | INT 항목 판정, 릴리스 후보 SHA 재검사 | INT 표 전 항목 통과, 끝에서 끝까지 흐름 2개와 시연 장면 4개 재실행 통과, `blocker` 미해결 0 | `evidence/INT-07.md`, `evidence/INT-09.md`, `evidence/INT-11.md` | planned |
| `AC-PRES-001` | 각 흐름의 트러블·검증 기록(A-10-03, B-10-04, C-11-04, D-11-03) | 데모 자료·시연 시나리오 작성 | 25분 타임라인, 핵심 장면 4개와 대체 화면, 수치마다 증빙 링크, 시연 영상 백업 | `docs/presentation/`, `evidence/S-10-01.md` ~ `evidence/S-11-07.md` | planned |
| `AC-PRES-002` | 완성된 데모 자료 | 리허설 2회와 사업 검토 데모, 최종 산출물 확정 | 25분 이내, 최종 산출물(데모 자료 + 저장소 링크) 전달 확인, 확정 SHA가 INT-11 #12 통과 SHA 또는 그 뒤 CI 녹색 `fix` PR만 더한 SHA, 확정 직전 `check_evidence --all --until 11/12` 통과 | `evidence/S-12-01.md` ~ `evidence/S-12-05.md` | planned |

## 9. 담당자별 구현 체크리스트

태스크의 완료 조건이 상세 체크리스트다. 아래는 흐름별로 놓치기 쉬운 경계만 적는다.

### 9.1 A 엔진 (`STK-ENGINE-001`)
- [ ] 엔진은 Django·Mongo·Kafka·LLM을 import하지 않는다. 이벤트는 반환만 한다.
- [ ] 행동 선택 함수에 진실 객체를 넘기지 않는다(시그니처와 테스트).
- [ ] 밸런스는 피해 장소 공개 상태에서만 맞춘다. 40~60%를 못 맞추면 완료가 아니다(`NFR-BAL-001`).
- [ ] 모든 NPC 행동에 이유 코드·근거 ID를 행동 시점에 기록한다(복기·해설의 근거).

### 9.2 B 웹·API (`STK-WEB-001`)
- [ ] 명령 처리 5단계와 200/409/422 구분, 서버 자동 재적용 없음.
- [ ] 응답에 금지 키 0(녹화 판 전 단계 검사).
- [ ] 게임 명령 경로에서 광고·LLM을 동기 호출하지 않는다. 추궁 답변 폴링 조각과 폴백 우선 표시(`FR-WEB-006`).
- [ ] 이미지는 `asset_url(content_id)`·`image_path`로만 참조한다.
- [ ] 회의·밤 VN 연출은 표시 방식만 바꾼다: 주장·정정·기색 정보 유지, 배경·sprite 선택에 숨은 정보 금지, 전신 컷은 4조건만, 읽기 상태는 서버 명령 없이(`FR-WEB-012`).
- [ ] 로그인 ID는 계정 저장소 밖(이벤트·로그·지표·광고 요청)에 0건. 가명 ID는 서버가 로그인 계정에서 채운다(`FR-AUTH-004`).

### 9.3 C 인프라/파이프라인 (`STK-INFRA-001`)
- [ ] 기술 버전 실측(11/5)과 `contracts/tech_stack.md` 갱신.
- [ ] 아웃박스·MERGE·`event_id` 중복 제거로 유실 0·중복 0을 대조표로 보인다.
- [ ] 개인정보 저장 위치 표에 Delta 이전 버전·MLflow·`out/` 산출물까지 넣는다.
- [ ] AWS 모든 계정 예산 알림, 시연 후 비용 차단.

### 9.4 D ML·광고 (`STK-MLAD-001`)
- [ ] 학습 입력에 진실 필드 0, 마을 관찰자 행만, 사람 데이터는 사용자 단위 분할.
- [ ] 관문 실패·거부도 기록한다(성공 경로). 첫 모델은 규칙 기준과 비교한다.
- [ ] 합성 트래픽 결론은 "알고리즘·회계·제어 루프 검증"으로 한정한다.
- [ ] 금액은 정수 밀리원, `charge_id` 유일, 예약 원자성.

### 9.5 E PM (`STK-PM-001`)
- [ ] LLM은 사실을 더하지 않는다. 폴백과 정보량이 같다.
- [ ] 사람 평가 세트는 학습·튜닝에 쓰지 않는다(동결, 교집합 0).
- [ ] 결정 대기(P-xx)를 기한 안에 정하고 `tasks/README.md` 7절과 BRD 미결 질문에 반영한다.
- [ ] 에셋은 생성 기록·규격 검사를 통과한 것만 쓴다.

### 9.6 S 데모 자료 트랙 (`STK-PRES-001`, P-24)
- [ ] 데모 자료의 모든 수치는 증빙 링크가 있다.
- [ ] 시연 장면마다 실패 시 대체 영상 구간이 있다.
- [ ] 확정 SHA는 INT-11 #12 재검사를 통과한 SHA이거나, 그 뒤 CI 녹색인 `fix` PR만 더한 SHA다. 확정 직전 `check_evidence --all --until 11/12` 통과(S-12-01).

## 10. 구현 순서

상세 일정은 `tasks/`가 기준이다. 아래는 요구사항이 어느 날 확인되는지만 보인다.

| 날짜&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 주요 작업&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 확인되는&nbsp;요구사항&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 완료&nbsp;evidence&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|
| 11/5(목) | VM 실측·재배정, 저장소·CI, 엔진 판 생성·회의·투표, API·회의 화면(가짜 엔진), 의도 기준선, 광고 서버 골격, 인물·비밀, 임시 에셋 | `FR-OPS-001`, `FR-GOV-001·002`, `FR-ENG-001·003`, `FR-WEB-001`, `FR-AD-002` | `evidence/C-05-01.md`, `evidence/E-05-02.md`, `evidence/A-05-02.md` |
| 11/6(금) | 밤·점괘·관측 경계, Mongo 저장소·아웃박스·광고 DB, 멱등성·가명 ID, 봇 정책, 광고 원장·합성 트래픽, LLM 벤치, 템플릿·의도 예시, 사용자 테스트 준비 | `FR-ENG-004·005`, `FR-DATA-001·002`, `FR-WEB-002`, `FR-PRIV-001`, `FR-AUTH-001~003`, `FR-ML-001`, `FR-AD-003~005`, `FR-LLM-001` | `evidence/A-06-03.md`, `evidence/C-06-02.md`, `evidence/E-06-07.md` |
| 11/7(토) | NPC 규칙 v0, Spark → Delta, 무손실 시험, 자유 입력·설문·광고 지면, 씐 자 모델 첫 학습, LLM 서버, 원한 단서·결말 초안, **사전 테스트**, **통합 1** | `FR-ENG-006·007`, `FR-ENG-010`(원한 단서 초안), `FR-DATA-003·004`, `FR-WEB-005·007`, `FR-AD-001`, `FR-LLM-002` | `evidence/INT-07.md` |
| 11/8(일) | 창귀 2명·큰 판, 밸런스 1차, 관측·광고 집계·k3s, 대량 시뮬레이션·모델 v0, CTR·페이싱 루프, 추궁 표현 완성, 확장 콘텐츠, 이미지 생성 도구·에셋 검사 도구 | `FR-ENG-008·009`, `FR-ASSET-001`, `FR-CONT-001`, `NFR-BAL-001`(1차), `FR-OPS-003`, `FR-AD-006·007·008`, `FR-ML-002·003`, `NFR-ML-001`, `FR-LLM-003` | `evidence/A-08-03.md`, `evidence/D-08-02.md`, `evidence/E-08-01.md` |
| 11/9(월) | 결말·도전, MLflow·관문·밴딧, BERT 연결, 개인정보 삭제, **정식 사용자 테스트**(임시 에셋), 평가 세트 동결, 첫 버전 초상, **통합 2** | `FR-ENG-010`, `FR-ML-004~006`, `FR-INT-003·004`, `NFR-INT-001`, `FR-PRIV-003`, `FR-UT-002`, `NFR-UT-001`, `NFR-LLM-001` | `evidence/INT-09.md` |
| 11/10(화) | 밸런스 재최적화·프리셋 리포트, 복기, 재학습 실증·카나리아·롤백, 광고 CTR 비교·자동 입찰, AWS 배포, 해설·운영 분석 에이전트, 회의·밤 VN 연출, 데모 구성안 | `FR-ENG-011·012`, `FR-WEB-010·012`, `FR-ML-007~009`, `FR-AD-009`, `FR-OPS-005`, `FR-LLM-004·005`, `FR-UT-003`, `FR-PRES-001` | `evidence/D-10-01.md`, `evidence/C-10-02.md` |
| 11/11(수) | 장애 복구 시험, AWS 검증, 최종 재실행, C 트러블·검증 기록, 장면 배경·확장 초상(오전) → 에셋 검수·교체 확인, **통합 3**, 데모 자료 | `FR-OPS-004·006`, `NFR-PERF-001·002`, `NFR-AD-001`, `FR-ASSET-002·003`, `FR-GOV-003`, `FR-GOV-004`, `FR-PRES-002` | `evidence/INT-11.md` |
| 11/12(목) | 리허설·사업 검토 데모·최종 산출물 확정, AWS 비용 차단 | `FR-PRES-003`, `FR-OPS-005`(비용 차단) | `evidence/S-12-01.md`, `evidence/C-12-02.md` |

## 11. 검토와 baseline

**문서의 역할**
- PRD는 "무엇을 만들고 무엇으로 합격인가"를 정한다. 태스크(`tasks/`)는 "누가 언제 하는가", 기획서는 "왜 그렇게 정했는가"를 담는다.
- 요구사항과 합격선이 서로 다르면 이 PRD가 기준이다. 변경은 PRD를 기준으로 하고, 같은 변경(같은 PR)에서 태스크·기획서 1.1절·BRD 3절을 함께 고친다.

**검토와 상태**
- BRD는 PM이 검토하고 11/5 오전에 baseline한다.
- PRD는 흐름별로 다른 흐름 1명이 검토한다(2.1절 짝, P-01 때 확정). 4절 표에서 자기 흐름 요구사항과 AC를 보고, 태스크 완료 조건과 어긋나는 곳을 PM에게 알린다.
- 요구사항·AC에 걸린 결정 대기 항목(P-01, P-05, P-07, P-08, P-09, P-10, P-11, P-12, P-13, P-14, P-16, P-17, P-18, P-23, P-24)이 남아 있으면 `Draft`를 유지한다. 모든 Must 요구사항이 AC·태스크·evidence에 연결되고 고아 요구사항 0건이며 위 결정이 반영되면 `Baselined`로 바꾼다.
- 실제로 검증하기 전에는 `pass` 대신 `planned` 또는 `not_verified`를 쓴다.
- **AC 판정 규칙**: AC의 Evidence 칸에 있는 태스크·INT 항목이 모두 완료(`check_evidence --complete` 통과 PR 병합)되면, 그 흐름의 PRD 검토자(2.1절)가 Then 항목을 증빙과 하나씩 대조해 `pass` 또는 `fail`로 바꾼다. 태스크 완료만으로 자동 `pass`가 되지 않는다. 요구사항 상태는 연결 AC가 모두 `pass`일 때 `pass`다.
- AC가 태스크 완료 조건보다 강한 곳이 있으면 PRD가 기준이므로 태스크 조건을 맞춰 고친다(지금 알려진 곳은 12절).

**일정·시간 관리와의 연결**
- 모든 증빙과 PR 본문에 `실제 소요 시간(h)`을 적는다. 계획 대비 실제는 `python -m tools.hours_report --until <MM-DD>`로 흐름 × 날짜별로 본다.
- P-23(남은 시간 배분 조정)은 11/6 09:00 스탠드업에서 11/5 실제 소요 시간 요약을 보고 정한다. 태스크를 옮기거나 줄이면 4절 표의 태스크·due를 같이 고친다. 요구사항을 빼는 것은 범위 축소이므로 PM 결정 없이 하지 않는다.
- P-24(데모 자료 트랙 담당·시간)는 11/10 09:00에 정한다. 그 전까지 `FR-PRES-001~003`의 owner는 `STK-PRES-001`(미배정)이고, S 시간은 흐름별 한도에서 뺀다.

**변경 기록**

| 날짜&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 버전&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 변경&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 근거&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|
| 2026-11-04 | v1 | 최초 작성(1단계: 1·3·5·6·7절, 2단계: 2·4·8·9·10·11·12절). 태스크는 c953d22 + 11/5 PM 결정 반영분(E 11/6 일부 미룸, C-11-04 추가)으로 추적 | BRD-PALDO-001@v1 |
| 2026-11-04 | v1 | 사업 문서 표현으로 통일(사업 검토 데모, 최종 산출물 확정, 데모 자료 트랙, 조직 저장소). 요구사항·AC 내용은 그대로. `BR-OBJ-010`을 `FR-AD-006`·`FR-AD-009`에 연결, 재방문율은 출시 후 지표(BRD 3.1절)로 표기 | BRD-PALDO-001@v1(사업 문서 재작성) |
| 2026-11-04 | v1 | 실제 에셋 담당 이전(E → B, 검수자 E: `FR-ASSET-001·002` 태스크·담당, 2.1절, 2.3절 검수), 웹소켓 광고 신호 중계(`FR-AD-001`, `AC-WEB-009`), 내부 사용자 테스트 HTTPS(`NFR-SEC-002`, `AC-SEC-002`), 12절 시간 초과 갱신 | PM 결정(11/4), BRD `BR-OQ-023` |
| 2026-11-04 | v1 | 개인 계정 결정 반영: `FR-AUTH-001~004`·`AC-AUTH-001~004` 추가, `FR-PRIV-001`·`AC-PRIV-001`의 쿠키 가명 발급을 가입 시 계정 발급으로 변경, 7.5절·9.2절·10절·12절 갱신 | PM 결정(11/4), BRD `BR-OQ-022` |
| 2026-11-05 | v1 | B 부담 완화: 고정 시드 판 비교 리포트·AWS 화면 검증·에셋 일괄 검수를 E로 이전(`FR-ENG-011`·`FR-OPS-006`·`FR-ASSET-002`·`AC-OPS-005`·`AC-ASSET-001` 태스크 ID, 이미지 검수 문장), 기술 스택 확정(ML·LLM Python 3.14 단일, Delta 좌표), 12절 갱신 | PM 결정(11/5), C-05-03 실측 |
| 2026-11-05 | v1 | 단순 확인 태스크를 완료 조건으로 합침(README 2절 규칙): 추적 태스크 ID 갱신(`FR-OPS-001` 노트북 VM 점검 → 각 흐름 11/5 첫 태스크, `FR-AD-008` D-08-03 → D-08-02, `FR-ASSET-003`·`AC-ASSET-002` E-11-06 → E-11-10, `FR-UT-001` B-08-04 → B-08-02, `FR-UT-003`·`AC-UT-002` D-09-03 → D-09-02), 12절 VM 확인 위치(D-07-02·E-06-07) |
| 2026-11-05 | v1 | 회의·밤 화면 비주얼 노벨 연출 추가(`FR-WEB-012`, `AC-WEB-010`), 실제 에셋을 초상·장면 배경으로 한정하고 아이콘·지도·광고 소재는 임시 유지(`FR-ASSET-002`, `AC-ASSET-001`, `AC-WEB-008`), 1.1절·9.2절·10절·12절 갱신 | PM 결정(11/5), 크로스체크 16 |
| 2026-11-05 | v1 | 실제 이미지 에셋 제작 담당 B → A(`STK-ENGINE-001`·`STK-WEB-001`, `FR-ASSET-001·002` 태스크, `AC-WEB-010` 증빙), 프리셋 반례 테스트·최종 파라미터 재실행 A → E(`FR-ENG-011`, `NFR-BAL-001`, `AC-BAL-001` 태스크). 요구사항·AC 내용은 그대로 | `tasks/README.md` 7절 |
| 2026-11-05 | v1 | A 에셋 주담당 재배치: 원한 단서 해금(`FR-ENG-010`)·복기용 이유 조회(`FR-ENG-012`) 태스크 A → E(E-09-08, E-10-10, 담당 `STK-PM-001`, 증빙 경로), VN 연출 규칙 E-09-07 → B-09-06(`FR-WEB-012`), 리허설 지원·버그 분류 E → B(2.1절 역할 표). 요구사항·AC 내용은 그대로 | `tasks/README.md` 7절 |
| 2026-11-05 | v1 | 화풍을 애니풍에 가까운 반실사 일러스트로 변경(도트 폐기), 인물 에셋을 전신 sprite 1장(초상은 파생)으로(`FR-ASSET-002`, `AC-ASSET-001`), VN 화면 평소/전신 컷 두 모드와 전신 컷 4조건(`FR-WEB-012`, `AC-WEB-010`), `STK-ENGINE-001` 산출물 `cutout.py` | PM 결정(11/5), 크로스체크 18 |

## 12. 구현 준비 검토

2026-11-04 작성 시점에 PRD와 태스크·계약을 대조한 결과다. 처리 담당이 정해지면 상태를 바꾼다.

| 구분&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 검토&nbsp;항목&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 확인 내용&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | 결과&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|---|
| 추적 | 요구사항 ↔ 태스크 | 95개 요구사항 모두 태스크 ID가 있고, 태스크 문서의 모든 태스크 ID가 4절 태스크 칸에 나온다(검사 스크립트로 대조: 없는 ID 0, 빠진 태스크 0). AC는 정의 75개 = 참조 75개(11/4 `AC-AD-009`, `AC-AUTH-001~004`, `AC-WEB-009`, `AC-SEC-002` 추가). `FR-WEB-006`은 11/4 B-07-01 조건 7로 구현 태스크를 정했다 | 94개 연결 |
| 추적 | BRD 목표 → 요구사항 | `BR-OBJ-001`~`010` 모두 요구사항이 1개 이상 있다(`BR-OBJ-010` 광고 수익 지표는 `FR-AD-006`·`FR-AD-009`: 계측 D-05-03, 집계 C-08-02, 대시보드 C-08-01, 보고 D-10-02·D-11-01). 출시 후 지표 `BR-KPI-001~003`은 파일럿 합격선이 아니라 요구사항을 두지 않는다 | ID 연결 확인. `BR-OBJ-010` 집계 정의는 미결(BRD `BR-OQ-021`) |
| 공백(해결, 11/4) | 추궁 답변 폴링 화면 | `contracts/api.md` 5절의 폴링 조각(폴백 먼저 표시, 1초 폴링, `final`이면 중지)이 B 태스크 완료 조건에 명시돼 있지 않다. B-05-02는 JSON 엔드포인트, INT-09 #5는 판정만 한다 | **해결**: B-07-01 조건 7(+0.5h)로 추가(11/4 PM 결정). 정적 파일 연결(`/static/assets`, `/static/ads`)도 B-05-03 조건 5(+0.25h)로 추가 |
| 결정 대기 | 합격선이 정해지지 않은 요구사항 | `NFR-PERF-001`(부하 조건 P-07), `FR-LLM-003`(말투 평가 P-09), `FR-ENG-011`(프리셋 공개 기준 P-08), `FR-ML-006`(P-16), `FR-ML-008`(P-17), `FR-PRIV-003`(P-18), `FR-OPS-004`(RTO P-13), `FR-OPS-005·006`(P-05), `FR-INT-002`(P-14), `FR-ASSET-001`(P-10), `FR-UT-001`(P-12), `FR-GOV-001`(P-11) | 기한까지 Draft 유지, 미정이면 해당 태스크는 착수만(`tasks/README.md` 7절) |
| 용어(해결) | 이벤트 대조식 | 태스크 C-07-02 1번·INT-11 #4의 "`seq` 합계"는 0부터 시작하는 `seq` 값의 합으로 읽히면 이벤트 수와 같지 않다. PRD는 판별 `next_seq`(마지막 `seq` + 1) = 중복 제거 후 고유 발행 건수 = bronze 행 수로 정의했다 | **해결(11/4 PM)**: C-07-02 1번·INT-11 #4를 PRD 정의로 고쳤다 |
| 결정 대기 | 설문 응답률 목표 | BRD `BR-OQ-002`. 태스크(E-09-01)는 응답률을 표로 보고만 한다 | 목표가 정해지면 `AC-UT-001`에 기준 추가 |
| 환경 | 기술 버전 | `contracts/tech_stack.md`는 기록값이다. ML·LLM Python 3.14 단일과 Delta 좌표 `io.delta:delta-spark_4.1_2.13:4.4.1`은 C-05-03 실측(기준 PC)으로 11/5 확정. Ubuntu VM 확인은 남음(D-07-02 6번·E-06-07 3번 ② 완료 조건, 11/5 PM 결정) | 두 조건에서 import 실패가 나오면 이슈로 대응하고 계약 갱신 |
| 환경 | LLM 지연 가정 | 3~4B Q4로 p95 15초 가능은 실측 전 가설(`BR-ASM-002`) | E-06-07 결과로 확인, 없으면 차선을 PM이 결정 |
| 일정 | 시간 배분 | 흐름별 날짜별 시간은 team_plan 전체 시간표. 실제 에셋은 11/5 결정으로 A가 맡고 범위는 초상·장면 배경으로 줄였다 | 11/6 스탠드업에서 `hours_report`로 P-23 결정 |
