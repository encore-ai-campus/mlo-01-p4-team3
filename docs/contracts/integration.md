# 영역 간 호출 계약 (통합 지도)

- 목적: 흐름(A~E)과 프로세스가 **서로를 어떻게 부르는지** 한 표로 본다. 자기 코드가 누구를 부르고 누가 자기를 부르는지, 그동안 어떤 가짜를 쓰는지, 언제 진짜로 붙는지를 여기서 찾는다.
- 이 문서는 **지도**다. 요청·응답 형식, 시간 제한 같은 세부는 각 계약 문서의 절에 있고, 여기서는 링크만 건다. 둘이 다르면 각 계약 문서가 맞다.
- 파일 위치: [structure.md](structure.md). 계층 규칙: [루트 README](../../README.md#계층과-의존-방향).
- 계약·태스크·기획서에 없던 값은 7절 결정 19건(11/4 PM 승인)으로 정했고, 해당 계약·태스크 문서에 반영했다. 이 문서의 `결정 #N`은 7절 번호다.

## 1. 원칙

| 원칙 | 내용 | 근거 |
|---|---|---|
| 호출 방식 분류(이 문서의 표기) | ① 같은 프로세스 안의 호출(포트·함수) ② HTTP(브라우저·내부 서비스·Prometheus 수집) ③ Kafka 토픽 ④ 저장소 접근(Mongo·MySQL 연결, Delta 테이블, MLflow 레지스트리, 파일). 포트 호출 뒤에 저장소 접근이 있으면 "①→④"로 함께 적는다 | - |
| 포트 뒤에 숨긴다 | 게임 쪽 코드는 바깥을 `engine/ports.py`와 `server/app/ads_port.py`의 포트로만 부른다. HTTP·DB 세부는 인프라 구현 안에 있다 | [ports.md](ports.md) |
| 게임 경로 보호 | 게임 명령 처리 중에는 광고를 부르지 않고 LLM을 기다리지 않는다. 광고는 화면이 그려진 뒤 지연 로드하고, LLM은 폴백을 먼저 보여 주고 폴링한다 | [ports.md 4·5절](ports.md), [api.md 5절](api.md) |
| 상태와 이벤트는 한 번에 쓴다 | 게임은 Mongo 판 문서의 아웃박스, 광고는 MySQL 트랜잭션의 아웃박스로 쓰고, 발행기가 Kafka로 보낸다. 웹 경로는 Kafka에 직접 쓰지 않는다 | [ports.md 1·2절](ports.md), [ads.md 5절](ads.md) |
| 버전은 판 시작 때 고정한다 | 콘텐츠·모델·규칙 버전은 `GameMeta`에 고정하고 판 도중 바꾸지 않는다 | [ml_lifecycle.md 4절](ml_lifecycle.md), [content.md 3절](content.md) |
| 가짜로 먼저 만든다 | 상대 흐름이 없을 때는 가짜를 쓰고, 교체 때 코드를 고치지 않는다 | [parallel_dev.md](parallel_dev.md), [team_plan 4절](../team_plan.md) |

## 2. 실행 단위

통합 환경(VM)의 배치 원본은 기획서 6.3이다. 개발 환경은 각자 PC의 Docker Compose(`paldo-dev`, C-05-03)이고, AWS 범위는 결정 대기(P-05)다.

| 실행 단위 | 코드 | VM(기획서 6.3) | 담당 |
|---|---|---|---|
| 게임 웹(Django) | `server/`, `web/`, 같은 프로세스 안에 `engine/`. `intent/`, `ml/adapters/`도 같은 프로세스에서 import해 먼저 시험한다(결정 #4. 환경 실패나 p95 초과면 분리) | app-01 | B |
| 게임 아웃박스 발행기 | `server/infra/outbox_publisher.py` | app-01 | C |
| 광고 서버(Django 별도 서비스) | `ads/` | ad-01 | D |
| 광고 주기 작업: 예약 만료 해제, 환입 소비자, 광고 아웃박스 발행 | `ads/app/expire_reservations.py`, `ads/app/refund_consumer.py`, `ads/infra/outbox.py` | ad-01, 웹과 별도 프로세스(결정 #15) | D |
| LLM 서비스 + llama.cpp | `llm/service/`, `llm/graph.py` | llm-01 또는 노트북 3 Windows 직접 실행(E-06-07에서 결정) | E |
| MongoDB 레플리카셋 | - | mongo-01~03 | C |
| Kafka 3노드 | `pipeline/topics.yaml` | kafka-01~03 | C |
| Spark 배치 | `pipeline/spark/` | spark-master-01, spark-worker-01 | C |
| Delta(Garage, S3 호환) | - | lake-storage-01 | C |
| 광고 DB(MySQL Primary/Replica) | `ads/infra/schema.sql`, `ops/mysql/` | mysql-01, mysql-02 | C(DB), D(스키마) |
| MLflow + 학습 | `ops/mlflow/`, `ml/`, `intent/` 학습 | nifi-01 | C·D |
| k3s(시뮬레이션 Job, 재학습 CronJob, 운영 분석 배치) | `ops/k3s/` | spark-worker-02, 03 | C·D |
| Prometheus + Grafana | `ops/prometheus/`, `ops/grafana/` | metrics-01 | C |
| 배포 자동화 | - | bastion-01 | C |

## 3. 호출 표

열 설명: **방식** ①~④는 1절. **주소 변수**는 4.1절의 이름(결정 #7). **INT**는 처음 함께 판정하는 통합 회차.

### 3.1 게임 요청 경로

| # | 부르는 쪽 → 받는 쪽 | 방식 | 이름 | 계약 | 시간 제한·실패 | 주소 변수 | 그동안 쓰는 가짜 | 진짜로 붙는 태스크 → INT |
|---|---|---|---|---|---|---|---|---|
| G1 | 브라우저 → 게임 웹(B) | ② | api.md 2절 엔드포인트(htmx 조각 또는 JSON) | [api.md 1~4절](api.md) | 409 버전 충돌, 422 거부, 같은 `command_id` 재요청은 저장된 응답. 비로그인 401·로그인 화면 이동(api.md 2-1절) | - | 녹화 판 `fixtures/api/` | B-05-02 → INT-07 #1 |
| G1a | 게임 웹 계정(B) → 계정 MySQL | ④ DB 연결(Django 인증) | 가입·로그인·잠금·세션 | [privacy.md 5절](privacy.md), [api.md 2-1절](api.md) | DB 장애 시 로그인 불가(게임 화면 진입 불가), 오류 로그·지표 | `PALDO_AUTH_DB_HOST`, `PALDO_AUTH_DB_PORT`, `PALDO_AUTH_DB_NAME`, `PALDO_AUTH_DB_USER`, `PALDO_AUTH_DB_PASSWORD` | 로컬 Compose MySQL의 같은 스키마 이름 | C-06-03 4번 → B-06-04 → INT-07 |
| G2 | 게임 웹 응용(B) → 엔진(A) | ① `GameEngine` | `new_game`, `apply`, `player_view`, `observe` | [ports.md 2절](ports.md), [domain.md](domain.md) | 중복·버전 확인은 응용이 엔진 호출 전에 한다 | - | `ScriptedGameEngine` | A-05-02 → INT-07 #1 |
| G3 | 엔진(A) → 의심 모델(D) | ① `SuspicionModel` | `estimate(ObservationSnapshot)` | [ports.md 3절](ports.md) | 엔진 측 100ms. 실패·시간 초과는 그 결정만 규칙 v0로 대신하고 `policy_version`에 `-fallback` | - | `FakeSuspicionModel` | D-08-01·D-08-04(11/8) → INT-09 #6 |
| G4 | 게임 웹 응용(B) → 의도 해석기(D) | ① `IntentParser` | `parse(text, ParseContext)` | [intent.md](intent.md), [api.md 4절](api.md) | 계약에 시간 제한 없음. 같은 프로세스 호출로 먼저 시험(결정 #4), 지연은 B-09-02에서 측정 | - | `FakeIntentParser` | D-07-01(11/7 11:00) → B-07-01, BERT는 D-09-01 → B-09-02 → INT-09 #3·#4 |
| G5 | 게임 웹 응용(B) → 게임 저장소(C) → Mongo | ①→④ `GameRepository` | `create`, `load`, `find_command`, `commit`, `append_events` | [ports.md 1·2절](ports.md) | `commit`은 CAS(`VersionConflict`), 판 문서 한 번에 쓰기, `w=majority` | `PALDO_MONGO_URI` | `InMemoryGameRepository` | C-06-01 → INT-07 #1·#4 |
| G5b | 게임 웹 응용(B) → 진척 저장소(C) → Mongo | ①→④ `ProgressRepository` | `load`, `add_clue`, `unlock_ending`, `add_game` | [ports.md 표](ports.md), A-09-01 | `source_event_id` 기준 멱등 반영. 이벤트 저장 뒤 진척 반영 전 장애는 재처리로 복구(C의 `tools/replay_progress.py`, C-09-04, 결정 #19) | `PALDO_MONGO_URI` | `InMemoryProgressRepository` | C-06-01, A-09-01 → INT-11 #14 |
| G6 | 엔진·응용 → 콘텐츠(C 저장, E 내용) | ① `ContentSource` | `load(content_version)` | [content.md](content.md), [domain.md 9절](domain.md) | 판 시작 때 버전 고정, 이전 버전 계속 제공 | `PALDO_MONGO_URI` | `FixtureContentSource` | C-06-01(+ 적재 도구 `tools/load_content.py`, 결정 #8) → INT-11 #14 |
| G7 | 게임 웹 응용(B) → LLM 서비스(E) | ① `LineRenderer` → `server/infra/llm_renderer.py`가 ②로 전달 | `submit`, `poll`, `cancel` → `POST /v1/renders`, `GET /v1/renders/{job_id}`, `POST /v1/renders/{job_id}/cancel`(404는 `failed`, 결정 #3) | [ports.md 4절](ports.md), [api.md 5절](api.md) | 마감 `deadline_ms` 기본 15초, 넘으면 `timeout`·실제 생성 취소. 단계가 넘어가면 응용이 `cancel`. 화면은 폴백 먼저, 1초 폴링 | `PALDO_LLM_URL` | `FakeLineRenderer` | E-07-01(11/7) → B-07-01 #7 → INT-09 #5 |
| G8 | LLM 서비스 → llama.cpp | E 내부 | - | E-07-01, E-06-07 | 큐 우선순위 0 캐묻기 > 1 복기 > 2 운영 분석 | - | - | E-07-01 |
| G9 | 게임 웹 응용(B) → 모델 선택(C 레지스트리, B 적용) | ①→④ `ModelSelector` | `select`(판 시작 때만), `suspicion_model(v)`, `intent_parser(v)` | [ml_lifecycle.md 4절](ml_lifecycle.md), [ports.md 3절](ports.md) | 고른 버전을 `GameMeta.model_versions`에 고정. 이전 버전은 최대 2개 메모리 유지 | `PALDO_MLFLOW_URI` | `FakeModelSelector`, MLflow 전에는 로컬 `mlruns/` | D-08-04(11/8)가 `server/infra/model_selector.py` 최소 구현(`mlruns/`)을 만들고 v0 50판 재현(결정 #5). C-09-01이 레지스트리 조회로 교체. B-09-01(11/9)이 카나리아·버전 고정·업데이트 화면 → INT-11 #3 |
| G10 | 브라우저 → 게임 웹 광고 칸 → `AdGateway` → 광고 서버(D) | 브라우저→게임 웹 ②, 게임 웹→광고 서버 ② | `GET /ads/slot/{slot}`(htmx `load`) → `POST /ads/v1/serve`. 응답 조각의 클릭 링크는 게임 웹 `/ads/click`, 비콘은 웹소켓(G11) | [ports.md 5절](ports.md), [ads.md 3절](ads.md) | 연결 100ms, 전체 300ms, 동시 8, 재시도 없음, 연속 실패 5회면 30초 생략. 어떤 실패든 빈 광고 | `PALDO_ADS_INTERNAL_URL` | `FakeAdProvider` | D-08-03(11/8 오전) → B-08-03(`server/infra/ad_client.py`, 결정 #6) → INT-09 #2·#9 |
| G11 | 브라우저 → 게임 웹(웹소켓·중계 경로) → `AdGateway` → 광고 서버(D) | 브라우저→게임 웹 ②(웹소켓 `/ws/game/`, HTTP), 게임 웹→광고 서버 ② | `ad.impression` 메시지 → `POST /ads/v1/impression`, `GET /ads/click?t=` → `GET /ads/v1/click?t=` → 302 `/ads/landing/<creative_id>` → `GET /ads/landing/<creative_id>` | [api.md 7절](api.md), [ports.md 5절](ports.md), [ads.md 3·6절](ads.md) | 비콘은 면적 50% 이상 1초 이상 보일 때 1회. 중계는 serve와 같은 시간 제한, 재시도 없음, 실패한 비콘은 손실. 클릭은 판정·실패와 무관하게 항상 302 | `PALDO_ADS_INTERNAL_URL`, `PALDO_PUBLIC_ORIGIN` | `FakeAdProvider` 중계 가짜 | B-07-05(11/7 오전, 가짜) → B-07-03 → D-08-03 → B-08-03 → INT-09 #2. 브라우저는 게임 웹 출처에만 접속, CSRF 제외는 광고 서버 비콘·클릭 엔드포인트(결정 #2, 11/4 변경) |
| G13 | 복기: 게임 웹 → 엔진 `review_trace`(A) → LLM 복기 해설(E) → 화면 폴링(B), 승리 확률(D) | ① `review_trace(game)`, ② 해설 생성(LLM 서비스 우선순위 1), 화면은 폴링 | `GET /games/{id}/review`(종료 후만) | [api.md 2절](api.md), A-10-02, E-10-01, B-10-01, D-10-03 | 해설의 근거 ID는 모두 `review_trace`에 있어야 함, 실패 시 템플릿 해설. `win_prob`가 없으면 그래프 숨김 | `PALDO_LLM_URL` | `fixtures/review/review_trace_sample.json` | 11/10 10:00 A-10-02 형식·샘플 → B·E, 12:00 E-10-01 해설 응답 형식 → B-10-01. 해설은 렌더 폴링 재사용, 우선순위 1(결정 #17) |
| G12 | 게임 웹 화면 → 이미지 | ④ `content/images/manifest.json` 파일 읽기 + 정적 파일 | `asset_url(content_id)`, `/static/assets/<kind>/…`, 광고는 응답의 `image_path`(`/static/ads/…`) | B-05-03 #4·#5, E-05-05, [ads.md 2절](ads.md) | manifest에 없는 ID는 기본 이미지 + 경고 로그 | - | E의 임시 에셋(같은 경로) | E-05-05(11/5 12:00) → B 실제 에셋 덮어쓰기(B-09-05 ~ B-11-04, 11/4 담당 이전) → E-11-10(일괄 검수·교체 확정) → E-11-06 |

### 3.2 이벤트·데이터·학습 경로

| # | 부르는 쪽 → 받는 쪽 | 방식 | 이름 | 계약 | 시간 제한·실패 | 주소 변수 | 그동안 쓰는 가짜 | 진짜로 붙는 태스크 → INT |
|---|---|---|---|---|---|---|---|---|
| D1 | 게임 웹 응용 → 아웃박스(상태를 바꾸지 않는 이벤트) | ① `append_events` | `intent_attempt`, 카드 반응, `notebook_updated`, `survey_submitted`, `render_job` | [ports.md 1절](ports.md), [events.md 2절](events.md) | `dedup_key`로 중복 무시, 상태 버전 안 올림 | `PALDO_MONGO_URI` | `InMemoryGameRepository` | C-06-01 → INT-09 #7 |
| D2 | 게임 아웃박스 발행기(C) → Kafka | ③ | `paldo.game`, `paldo.observations`, `paldo.intent`, `paldo.llm`, `paldo.player`, `paldo.truth`(판 종료 후만) | [events.md 1·2절](events.md) | acks=all 후 발행 표시, 실패 시 재시도, 소비자는 `event_id`로 중복 제거 | `PALDO_KAFKA_BOOTSTRAP` | `fixtures/events/`, `tools/gen_synthetic.py` | C-06-02 → INT-07 #5 |
| D3 | Kafka → Spark → Delta(C) | ④ 배치 | bronze → silver(`TrainingRow`, 진실 조인) | [events.md 3절](events.md), C-07-01 | `event_id` MERGE로 멱등. 실행마다 Delta 버전·입력 오프셋 기록 | `PALDO_KAFKA_BOOTSTRAP`, `PALDO_S3_ENDPOINT` | - | C-07-01(11/7 14:00 → D-07-02) → INT-07 #7, INT-09 #1 |
| D4 | Delta silver → D 학습 | ④ 테이블 읽기 | 학습 행 | [events.md 3절](events.md), [ml_lifecycle.md 1절](ml_lifecycle.md) | 마을 관찰자 행만(필터는 D), 사람 데이터는 가명 단위 분할 | `PALDO_S3_ENDPOINT` | `fixtures/ml/`, 합성 데이터 | D-07-02 → INT-07 #7 |
| D5 | D 학습 → 평가 관문 → MLflow | ① `tools/ml_gate.py`가 `ml/gate.py` 호출, ④ 레지스트리 등록 | `candidate` → `canary` → `production`, 거부 태그 | [ml_lifecycle.md 2·3절](ml_lifecycle.md) | 데이터 버전·특징 버전·코드 커밋 태그가 없으면 등록 실패 | `PALDO_MLFLOW_URI` | 로컬 `mlruns/` | C-09-01, D-09-02 → D-10-01 → INT-11 #3 |
| D6 | 재학습 CronJob(C) → D 학습·관문·등록 | k3s Job | `ops/k3s/cronjobs/retrain.yaml` | [ml_lifecycle.md 5절](ml_lifecycle.md) | 감지는 C(경보), 롤백 결정·실행은 D, 사용자 테스트 중에는 E 승인 | `PALDO_MLFLOW_URI` | 수동 트리거 Job | C-10-01 → D-10-01 → INT-11 #3 |
| D7 | 시뮬레이터(D) → 엔진(A) | ① `GameEngine`(`engine/sim/runner.py`) | 봇 정책이 플레이어 자리 명령 생성 | [parallel_dev.md 1절](parallel_dev.md) | 같은 시드면 같은 결과 | - | `ScriptedGameEngine` | D-06-01, A-06-03(소량 판 jsonl) |
| D8 | 시뮬레이터 → 이벤트 저장 | ① `EventSink.append` → jsonl 또는 Kafka | 웹 경로는 `EventSink`를 쓰지 않음 | [ports.md 1절](ports.md) | - | `PALDO_KAFKA_BOOTSTRAP` | `ListEventSink`, jsonl | A-06-03(jsonl), 대량 실행은 C-08-03·D-08-01(Kafka 구현 `pipeline/sinks/kafka_sink.py`는 C-08-03, 결정 #10) |
| D9 | 운영 분석 에이전트(E) → 운영 조회(C) | ① `server/infra/ops_query.py` 함수 호출(읽기 전용) → Prometheus·Delta | 최근 N시간 승률, 판 길이, 폴백률, 의도 수정·취소율, 광고 CTR·소진율 | C-10-03, E-10-02 | 쓰기 권한 없음. 리포트 수치는 조회 결과와 일치해야 함 | `PALDO_PROMETHEUS_URL`, `PALDO_S3_ENDPOINT` | 합성 데이터 집계 | C-10-03(11/10) → E-10-02. k3s Job, 사용자 테스트·시연 중 금지(결정 #13) |
| D10 | Prometheus(C) → 모든 서비스 | ② pull | 각 서비스 `/metrics`, node_exporter(전 VM) | [tasks/README.md 5절](../tasks/README.md) | 배치·스크립트는 Pushgateway 또는 결과 JSON | - | - | 각 서비스 첫 태스크(B-05-01, D-05-03, E-07-01, C-06-02) → C-08-01 → INT-07 #9 |
| D12 | 판 종료(엔진·설문) → 밴딧(D) → 씐 자 전략 비율 → 다음 판 엔진 주입(A) | ④ 배치가 보상 계산 → 비율을 버전과 함께 Mongo 저장 → 판 시작 때 응용이 읽어 엔진 주입, `GameMeta.model_versions["bandit"]` 고정(결정 #16) | 거짓말 유형, 누명 대상, 대사 변형, 2명 판 팀 전략 비율 | D-09-02 #2·#3, A-08-01 #2, 기획서 6.2 | 보상식·가중치 상한·최소 판 수는 P-16. 같은 판 보상 중복 반영 금지(판 ID) | - | 고정 비율 | D-09-02(11/9) → INT-11 #3 |
| D11 | 개인정보 삭제 도구(C) → 모든 저장 위치 | ④ 각 저장소 접근 | Mongo, Kafka 보존, Delta(이전 버전 포함), 광고 MySQL, 계정 MySQL(계정 행), MLflow, `out/`, AWS 복사본 | [privacy.md](privacy.md), C-09-02 | 광고 기록은 B-06-03의 가명 대응표로 찾는다 | 각 저장소 변수 | - | C-09-02 → INT-11 #9 |

### 3.3 광고 경로

| # | 부르는 쪽 → 받는 쪽 | 방식 | 이름 | 계약 | 시간 제한·실패 | 주소 변수 | 그동안 쓰는 가짜 | 진짜로 붙는 태스크 → INT |
|---|---|---|---|---|---|---|---|---|
| A1 | 광고 서버(D) → MySQL | ④ DB 연결 | 예약·확정·해제·환입 + 아웃박스 행 | [ads.md 5절](ads.md) | 같은 트랜잭션, `charge_id` 유일 제약, 예약은 조건부 갱신 또는 행 잠금 | `PALDO_ADS_DB_HOST`, `PALDO_ADS_DB_PORT`, `PALDO_ADS_DB_NAME`, `PALDO_ADS_DB_USER`, `PALDO_ADS_DB_PASSWORD` | 메모리 `BudgetBook`, 오전에는 Compose MySQL | C-06-03(11/6 12:00) → D-06-02 → INT-07 #8 |
| A2 | 광고 아웃박스 발행 → Kafka | ③ | `ad.serve`, `ad.impression`, `ad.click`(키 `serve_id`) | [events.md 5절](events.md) | 소비자는 `event_id`로 중복 제거 | `PALDO_KAFKA_BOOTSTRAP` | `fixtures/ads/`, 합성 광고 트래픽 | D-06-02 → INT-07 #8 |
| A3 | Kafka → Spark 광고 silver·gold·대사(C) | ④ 배치 | silver(판정·조인), gold(캠페인 × 지면 × 시간대), 건별 대사 | [ads.md 6·7절](ads.md) | 광고 작업은 게임 집계와 다른 시간대·자원 한도(기획서 14.7) | `PALDO_KAFKA_BOOTSTRAP`, `PALDO_S3_ENDPOINT` | 생성기가 만든 집계 | C-08-02(11/8 13:00 → D-08-02) → INT-09 #2 |
| A4 | gold → CTR·페이싱 갱신(D) → 광고 서버 | ④ 갱신 작업이 값을 적재, 광고 서버가 읽음 | `ctr_version`, `policy_version`이 다음 serve 이벤트에 남음 | [ads.md 4·5절](ads.md) | 페이싱 조정 5분마다, 한 번에 ±0.2. MySQL 버전 행, 광고 서버 60초 재적재(결정 #9) | `PALDO_ADS_DB_*` | - | D-08-02 → INT-09 #2 |
| A6 | 광고 CTR 모델 수명주기와 자동 입찰(D) | ④ 학습 → 관문 → MLflow 등록 → 서빙 버전 → 카나리아·롤백. 입찰 갱신 작업이 gold를 읽어 입찰가 갱신 | `ctr_version`, `bid_version`이 다음 serve에 반영 | D-10-02 #2·#4, C-10-01(재학습 대상에 광고 CTR 모델), [ml_lifecycle.md](ml_lifecycle.md) | 입찰가는 상한·하한 안에서만, 상한 초과 갱신은 잘림 | `PALDO_MLFLOW_URI`, `PALDO_ADS_DB_*` | 베타 방식 CTR | D-10-02(11/10) → INT-11 #3·#8. 입찰값·`bid_version`도 MySQL 버전 행, 60초 재적재(결정 #9) |
| A5 | silver 재판정 → 환입 소비자(D) | ③ | `ad.rejudge` 토픽([events.md 5절](events.md), 결정 #11) | [ads.md 5절 "환입"](ads.md), D-06-02 #7 | 같은 `charge_id` 환입은 1회 | `PALDO_KAFKA_BOOTSTRAP` | - | C-08-02, D-06-02 → INT-11 #13 |

## 4. 공통 규칙

### 4.1 주소·비밀은 환경 변수로만 (이름은 결정 #7, C-05-03의 `.env.example`)
- 코드와 문서에는 IP·계정·비밀번호를 쓰지 않는다. 실제 값은 Git이 추적하지 않는 `.env`에 두고, `.env.example`에는 이름과 자리표시자만 둔다(C-05-03).
- 같은 이름을 개발(Compose)·VM·AWS에서 쓰고 값만 바꾼다.

| 변수 | 쓰는 곳 | 뜻 |
|---|---|---|
| `PALDO_MONGO_URI` | 게임 웹, 발행기, 삭제 도구 | Mongo 레플리카셋 연결 문자열 |
| `PALDO_KAFKA_BOOTSTRAP` | 발행기, 광고 발행, Spark, 소비자 | Kafka 부트스트랩 서버 목록 |
| `PALDO_S3_ENDPOINT`, `PALDO_S3_ACCESS_KEY`, `PALDO_S3_SECRET_KEY` | Spark, 학습, MLflow, 조회 | Garage(S3 호환) |
| `PALDO_MLFLOW_URI` | 학습, 관문, `ModelSelector` | MLflow 추적·레지스트리 |
| `PALDO_LLM_URL` | `llm_renderer.py` | LLM 서비스 기본 주소 |
| `PALDO_ADS_INTERNAL_URL` | 게임 웹 `AdGateway` | 광고 서버 내부 주소(serve와 비콘·클릭·랜딩 중계) |
| `PALDO_PUBLIC_ORIGIN` | 게임 웹(`ALLOWED_HOSTS`·`CSRF_TRUSTED_ORIGINS`·웹소켓 출처 검사·보안 쿠키), C 프록시 | 브라우저가 닿는 게임 웹 출처(예 `https://<게임 웹 주소>`). `https://`이면 보안 쿠키를 켠다(B-07-05). 브라우저용 광고 서버 주소 변수(옛 `PALDO_ADS_PUBLIC_URL`)는 11/4 결정 #2 변경으로 폐지 |
| `PALDO_ADS_DB_HOST`, `_PORT`, `_NAME`, `_USER`, `_PASSWORD` | 광고 서버 | 광고 MySQL. `_PORT`는 비어 있으면 3306(VM·AWS 기본). 로컬 Compose는 Windows MySQL과 겹치지 않게 호스트 포트를 바꿔 쓸 수 있고(이 PC는 3316), 그 값은 `.env`에만 둔다(11/4 PM 결정) |
| `PALDO_ADS_TOKEN_KEY` | 광고 서버 | serve 토큰 서명 키(ads.md 3절 "인프라 비밀 저장소") |
| `PALDO_AUTH_DB_HOST`, `_PORT`, `_NAME`, `_USER`, `_PASSWORD` | 게임 웹(계정) | 계정 저장소 MySQL(계정 전용 스키마·DB 계정, 광고와 분리, privacy.md 5절). `_PORT`는 비어 있으면 3306. 로컬 값은 `.env`에만(11/4 PM 결정) |
| `PALDO_SIGNUP_INVITE_CODE` | 게임 웹(가입) | 있으면 가입 때 이 초대 코드를 요구, 없으면 공개 가입. 파일럿은 설정하지 않는다(공개 가입, 11/4 PM 결정, BRD `BR-OQ-022` 닫힘). 값은 `.env`·비밀 저장소에만 |
| `PALDO_PROMETHEUS_URL` | 운영 조회 | Prometheus 조회 API |
| `DJANGO_SECRET_KEY` | 게임 웹, 광고 서버(각자 다른 값) | Django 비밀 키 |

### 4.2 로그·지표
- 형식·필수 필드·지표 이름 규칙은 [tasks/README.md 5절](../tasks/README.md)이 원본이다.
- 호출을 추적할 때 쓰는 키: HTTP는 `request_id`, 게임은 `game_id`·`command_id`·`event_id`, 자유 입력은 `attempt_id`, LLM은 `job_id`, 광고는 `serve_id`·`charge_id`.
- 내부 HTTP(G7, G10)에서 `X-Request-ID` 헤더를 넘기고 받는 서비스가 같은 값을 로그 `request_id`로 남긴다(결정 #14). 끝에서 끝까지 흐름(INT-09 #1·#2)을 한 ID로 잇기 위해서다.

### 4.3 이벤트 발행 주체
- 게임 이벤트는 엔진이 반환하고, 응용이 봉투를 씌워 저장소에 쓰고, 발행기가 Kafka로 보낸다. 엔진·응용·LLM 서비스는 Kafka에 직접 쓰지 않는다([ports.md 1절](ports.md)).
- `render_job`은 [ports.md 1절](ports.md)대로 같은 아웃박스에 `append_events`로 들어간다(`dedup_key` 예 `render:<job_id>:<status>`). 기록은 응용이 한다. `poll` 결과의 상태 변화와, 단계가 넘어가 미완료 작업을 `cancel`할 때의 최종 상태를 남겨 폴링이 멈춘 작업도 빠지지 않는다. 양자화 버전·폴백 여부는 `RenderResult.quantization`·`fallback`(contract-0.3)으로 받는다(결정 #18, B-07-01 #7).
- 광고 이벤트는 광고 서버가 MySQL 아웃박스로 발행한다. 게임 `EventEnvelope`를 쓰지 않는다([events.md 5절](events.md)).

### 4.4 버전 고정
- 판 시작 때 `content_version`, `model_versions`, 규칙·정책 버전을 `GameMeta`에 고정한다. 롤백해도 진행 중인 판은 그대로다.
- 모델은 자기 `feature_version`을 선언하고, `ModelSelector`는 엔진과 다른 특징 버전의 모델을 고르지 않는다([ports.md 3절](ports.md)).
- 광고 이벤트는 공통으로 `policy_version`, `ctr_version`, `contract_version`을 남기고, 노출·클릭 이벤트는 판정 버전 `judge_version`을 더 남긴다([events.md 5절](events.md)).

### 4.5 개인정보 경계
- 광고 서버에는 세션 가명 ID만 보낸다. 플레이어 가명 ID와 판 내용은 보내지 않는다([ads.md 9절](ads.md), [privacy.md](privacy.md)).
- 브라우저는 광고 서버에 직접 접속하지 않으므로(G11) 광고 서버는 참가자 접속 주소를 받지 않는다.
- 원문 자유 입력은 마스킹 후 이벤트에만 넣고, 로그에는 넣지 않는다.

## 5. 환경별 차이

| 환경 | 구성 | 다른 점 |
|---|---|---|
| 개발(각자 PC) | Compose `paldo-dev`: Mongo 1노드 레플리카셋, Kafka 1노드, MySQL 1노드, S3 호환 저장소, Prometheus, Grafana(C-05-03) + 로컬 Django(11/7 B-07-05부터 Daphne ASGI, HTTP) | 상대 흐름은 가짜. LLM은 `FakeLineRenderer` 또는 작은 모델(기획서 6.3 개발 시간대) |
| 통합·사용자 테스트(VM) | 2절 표 + app-01 게임 웹 앞 Caddy HTTPS 프록시(C-07-04, 자체 서명, wss 통과) | 실제 구현으로 교체. 사용자 테스트 중에는 llm-01·ad-01·app-01에 학습 작업을 올리지 않는다(기획서 6.3). 참가자는 첫 접속 때 인증서 경고를 한 번 넘긴다(C-07-04 안내) |
| 시연·외부 공개(AWS) | 결정 대기 P-05 | 범위가 정해지면 이 표에 추가한다. 게임 웹은 HTTPS(C-10-02 5번), 광고 서버는 공개하지 않는다 |

## 6. 통합 시점 요약

| 날짜 | 처음 진짜로 붙는 호출 | 판정 |
|---|---|---|
| 11/5 | 임시 에셋 manifest(G12) | E-05-05 → B·D |
| 11/6 | 광고 MySQL(A1), 광고 이벤트 Kafka(A2), 게임 이벤트 Kafka(D2), 실제 엔진 소량 판(D7) | INT-07 사전 |
| 11/7 | 엔진(G2), Mongo 저장소(G5), 기준선 의도 해석기(G4), LLM 서비스(G7), Spark → Delta → 첫 학습(D3·D4), 웹소켓 비콘·클릭 중계(G11, 가짜 광고, B-07-05), HTTPS 프록시(C-07-04) | INT-07 |
| 11/8 | 의심 모델 v0(G3)와 최소 모델 선택(G9), 실제 광고 서버(G10·G11), 광고 gold → 갱신(A3·A4), 대량 시뮬레이션(D8) | INT-09 준비 |
| 11/9 | BERT 해석기(G4), 모델 선택 적용(G9), 밴딧(D12), 끝에서 끝까지 흐름 1·2 | INT-09 |
| 11/10 | 운영 조회 → 운영 분석 에이전트(D9), 재학습 → 승격·롤백(D5·D6), 복기(G13), 광고 CTR 모델·자동 입찰(A6) | INT-11 준비 |
| 11/11 | 실제 에셋 교체(G12), 삭제 요청(D11), 환입(A5) | INT-11 |

## 7. 11/4 PM 결정 (영역 간 호출 19건)

계약·태스크에 없던 것을 PM이 11/4에 아래 "결정과 이유" 열대로 모두 승인했다. 각 결정은 "반영 위치" 열의 계약·태스크 문서에 반영했고, 시간이 는 태스크는 C-06-01(+0.25h), C-08-03(+0.25h), C-09-04(+0.5h), D-08-04(+0.5h)다.

| # | 항목 | 선택지 | 결정과 이유 | 반영 위치 |
|---|---|---|---|---|
| 1 | 광고 서버 Django 프로젝트 위치 | (a) `ads/project/`(settings·urls) + `ads/manage.py`로 독립 (b) `server/paldo_web`에 `ads.web` 앱을 넣고 설정 모듈만 분리 | **(a)**. 계약이 "Django 별도 서비스(ad-01)"이고 DB·비밀 키가 다르다. B와 D가 같은 settings 파일을 동시에 고치지 않는다 | D-05-03, C-10-02 |
| 2 | 브라우저 → 광고 서버(비콘·클릭)의 출처 처리 | (a) 광고 서버에 직접. 클릭은 링크 이동이라 출처 문제가 없다. 비콘은 `navigator.sendBeacon`으로 `text/plain` 본문(JSON 문자열)을 보내 사전 요청을 피한다 (b) 리버스 프록시로 같은 출처 (c) 게임 웹이 비콘을 받아 넘김 | **11/4 같은 날 (a) → (c)로 변경(웹소켓)**: 내부 사용자 테스트를 HTTPS로 하기로 해(BRD `BR-OQ-023`) 게임만 HTTPS면 HTTP 광고 서버로 가는 비콘이 혼합 콘텐츠로 막히고, 클릭은 HTTPS 화면에서 HTTP 주소로 빠져나간다. 그래서 브라우저는 게임 웹 출처에만 접속하고, 노출 비콘은 Django Channels·Daphne 웹소켓 `/ws/game/`, 클릭·랜딩은 게임 웹 HTTP 경로로 받아 게임 웹이 광고 서버에 내부 HTTP로 넘긴다(api.md 7절, ports.md 5절). 서버 간 API 형식은 그대로(`ads-0.3`). `PALDO_ADS_PUBLIC_URL` 폐지, `PALDO_PUBLIC_ORIGIN` 추가. 광고 장애 시험 범위가 중계까지 넓어진다(B-08-03 2번). 반영: B-07-05(신설), B-07-03, B-08-03, D-05-03, C-07-04(신설), C-10-02. 아래는 처음 (a)를 고른 이유와 그때의 조건이며 **폐기된 이력**이다(지금 유효한 조건은 api.md 7절·ports.md 5절·ads.md 3절). **(a)**. 새 구성요소가 없고 광고 트래픽이 게임 웹 부하에 섞이지 않는다. 조건: 광고 서버가 `text/plain` 본문을 직접 파싱하고, 비콘·클릭 엔드포인트는 serve 토큰으로 검증하므로 Django CSRF 검사에서 제외한다. `PALDO_ADS_PUBLIC_URL`(AWS는 HTTPS 공개 주소)이 필요하다. 서로 다른 출처에서 실제 동작을 확인한다(B-08-03). (c)도 게임 명령 경로 밖이면 기획서 14.7 위반은 아니지만 광고 장애 시험 범위가 넓어진다 | B-07-05, B-07-03, D-05-03, B-08-03, C-07-04, C-10-02 |
| 3 | LLM 서비스 HTTP 형식 | `POST /v1/renders`(`RenderRequest` JSON → 202), `GET /v1/renders/{job_id}`(`RenderResult` JSON), `POST /v1/renders/{job_id}/cancel`(→ 204). 작업 상태는 서비스 메모리. 재시작으로 작업이 없으면 404 → 렌더러가 `failed`로 보고, 화면은 폴백 | **이 형식으로 확정**. `LineRenderer` 메서드 3개와 1:1이라 렌더러가 얇다. 우선순위·마감은 서비스 큐가 맡는다. 렌더러의 HTTP 시간 제한 값은 E-06-07 벤치 뒤 정한다 | ports.md 4절, E-07-01, B-07-01 #7 |
| 4 | ML 추론의 프로세스 경계(의도 해석기·의심 모델) | (a) 게임 웹 프로세스 안에서 import(포트가 같은 프로세스 호출로 설계됨. 기획서 6.3의 "app-01 추론"은 VM 위치만 정한다) (b) 별도 추론 프로세스·서비스(HTTP) | **(a)를 먼저 시험**. 조건: 게임 웹(Django)과 PyTorch·transformers·LightGBM이 한 Python 환경에서 함께 설치되고(C-05-03 실측), 의심 모델 100ms 제한(D-08-01 #3)을 지킨다. 성립하지 않거나 p95 100ms를 넘으면 (b)안을 측정값과 함께 PM에 보고. 의도 해석의 시간 제한과 예외 처리(예: 예외 → `unparseable`)도 함께 정한다 | C-05-03, B-07-01, B-09-02, D-08-01 |
| 5 | `ModelSelector` 실제 구현 위치·담당·시점 | 포트 표는 "C(레지스트리), B(적용)"이지만 구현 파일·태스크가 없다. D-08-04(11/8)가 이미 Selector 설정으로 v0를 불러야 한다 | `server/infra/model_selector.py`. **11/8 D-08-04 인계 전에 `mlruns/`를 읽는 최소 구현**이 필요하다. 담당은 D-08-04(+0.5h, 모델 형식을 아는 쪽). B-09-01은 카나리아·버전 고정·업데이트 화면, C-09-01은 MLflow 레지스트리 조회로 교체 | D-08-04, B-09-01, C-09-01 |
| 6 | `AdProvider` HTTP 클라이언트 위치 | 포트 표는 "B(HTTP 클라이언트)"이지만 파일이 없다 | `server/infra/ad_client.py`, B-08-03 산출물. ports.md 5절 | B-08-03 |
| 7 | 환경 변수 이름 | 4.1절 표 | **`PALDO_` 접두 + 4.1절 목록**. C-05-03의 `.env.example`에서 확정 | C-05-03, 전 흐름 |
| 8 | 콘텐츠 적재 | `content/*.json` → Mongo(새 `content_version`) 도구가 없다 | `tools/load_content.py`를 C-06-01 범위로(+0.25h, 저장 형식을 아는 쪽). E는 파일만 만든다 | C-06-01, E-05-03, E-08-02 |
| 9 | CTR·페이싱·입찰가(`bid_version`) 값을 광고 서버에 반영하는 방식 | (a) 갱신 작업이 MySQL에 버전 행을 쓰고 광고 서버가 주기적으로 다시 읽음 (b) 광고 서버 내부 API로 밀어넣기 | **(a)**. ads.md 1절의 `ads/infra` "CTR·페이싱 값 적재"와 맞고, 광고 서버를 다시 띄워도 값이 남는다. 재적재 주기는 페이싱 조정(5분)보다 짧게(60초) | ads.md 4절, D-08-02, D-10-02 |
| 10 | 시뮬레이터 이벤트 경로 | (a) jsonl 파일 → bronze 적재 (b) Kafka `EventSink` 직접 | **(b)**. 기획서 6.2가 시뮬레이터 → Kafka이고 `EventSink` 구현 담당이 C다. 구현은 `pipeline/sinks/kafka_sink.py`(C-08-03, +0.25h). A-06-03 소량 판은 지금처럼 jsonl | C-08-03, D-08-01 |
| 11 | silver 재판정 무효 이벤트 토픽 | 이름이 없다 | `ad.rejudge`를 events.md 5절에 추가. 생산 C-08-02, 소비 D-06-02 `refund_consumer` | events.md 5절, ads.md 5절, C-08-02, D-06-02 |
| 12 | 외부 서비스가 필요한 테스트 실행 | `tests/infra/`(Mongo), `tests/ads/`(Django·MySQL)는 CI `core`(서비스 없음)의 discover에 걸리고, CI `django` 예시는 `server/tests/`만 돈다. 테스트 모듈이 드라이버·Django를 먼저 import하면 `skipUnless`에 닿기 전에 실패한다 | `core`는 이 두 폴더를 discover에서 빼거나(예: `load_tests`), 외부 의존 import를 테스트 함수 안으로 미룬다. 서비스 CI 작업이 `tests/infra/`, `tests/ads/`, `server/tests/`를 **경로별로 명시 실행**한다. 광고 테스트의 Django 설정은 #1과 함께 정한다. Kafka·Spark·Delta는 기존대로 INT에서 판정 | C-06-01, D-05-03, D-06-02, E-05-02 |
| 13 | 운영 분석 에이전트 실행 위치 | (a) k3s Job(기획서 6.3 "운영 분석 에이전트 배치") (b) llm-01 | **(a)**. `ops_query`를 라이브러리로 import하고, 생성은 LLM 서비스에 우선순위 2로 요청한다. k3s는 에이전트 실행 자원만 분리하고 **LLM 생성 자원은 캐묻기와 공유한다**. 큐 규칙은 캐묻기가 기다릴 때 낮은 우선순위 작업을 새로 시작하지 않을 뿐 실행 중인 생성을 멈추지 않으므로, 실행 시간대(사용자 테스트·시연 중 금지)를 정하고 혼합 부하에서 캐묻기 지연을 확인한다 | E-10-02, C-10-03, E-07-01 |
| 14 | 내부 HTTP 상관 ID | 계약에 없다 | 게임 웹이 G7·G10 호출에 `X-Request-ID`를 넘기고, 받는 서비스가 로그 `request_id`로 남긴다 | B-05-01, D-05-03, E-07-01 |
| 15 | 발행기·광고 주기 작업의 실행 형태 | (a) 각자 별도 프로세스 (b) Django 프로세스 안 스레드 | **(a)**. 게임 요청 경로와 분리되고, 중지·재개 시험(INT-11 #5)을 프로세스 단위로 할 수 있다. 예: `python -m server.infra.outbox_publisher` | C-06-02, D-06-02 |
| 16 | 밴딧 경로 | 보상 입력, 비율 저장 위치, 엔진 주입 방법이 정해지지 않았다 | 판 종료 때 응용이 밴딧을 직접 부르지 않는다(게임 경로 보호). 배치가 `paldo.game`·`paldo.player`에서 보상을 계산해 비율을 저장하고, 판 시작 때 응용이 비율을 읽어 엔진에 주입하고 `GameMeta`에 고정한다. 저장 위치는 Mongo, 버전은 `GameMeta.model_versions["bandit"]` | D-09-02, A-08-01, B-09-01 |
| 17 | 복기 해설 API·DTO | 해설 응답 형식은 E-10-01이 11/10 12:00에 정한다. 렌더 폴링 경로를 재사용할지 별도 경로인지 미정 | 기존 `LineRenderer`·`RenderOutput` 폴링을 우선순위 1로 재사용(새 경로를 늘리지 않음). 형식이 다르면 E-10-01이 api.md를 고치는 계약 변경으로 | api.md 5절, ports.md 4절, E-10-01, B-10-01 |
| 18 | `render_job` 기록 책임 | 응용이 본 상태만 기록하면 폴링이 멈춘 작업이 빠진다. 양자화 버전·폴백 여부 필드가 `RenderResult`에 없다 | 응용이 단계 전환 때 그 판의 미완료 작업을 `cancel`하면서 최종 상태를 기록(api.md 5절의 취소 시점 재사용). `RenderResult`에 `quantization`·`fallback`을 더했다(계약 변경, contract-0.3) | ports.md 4절, E-07-01, B-07-01 #7 |
| 19 | 진척 재처리 실행 주체 | A-09-01은 "이벤트 저장 뒤 진척 반영 전 장애 → 재처리 복구"를 요구하지만 누가 언제 재처리를 돌리는지 없다 | 판 종료 이벤트 기준으로 진척을 다시 반영하는 도구`tools/replay_progress.py`를 C가 C-09-04(+0.5h)에서 만들고 A-09-01 4번 시험에서 실행 | A-09-01, C-09-04 |

## 8. 원본 문서끼리 다르던 값 (11/4 PM 해결)

아래 6건은 PM 승인대로 한쪽으로 맞추고 원본 문서를 고쳤다.

| 항목 | 문서 A | 문서 B | 처리(11/4 PM) |
|---|---|---|---|
| LLM 실제 연결일 | team_plan 4절: A의 LLM 교체 11/9 | E-07-01(11/7) 인계 뒤 B-07-01 #7에서 연결 | 11/7로 맞춤(team_plan 4절 수정) |
| 광고 잠정 마감 시점 | ads.md 7절: 귀속일 다음 날 | C-08-02 #5: 당일 | ads.md 7절로 맞춤(C-08-02 #5 수정) |
| ML Python 버전 | 기획서 6.3 끝: 3.12 가상환경 권장, 시스템 3.14.4 | tech_stack.md(11/5 확정): ML·LLM도 3.14 단일(C-05-03 실측), 시스템 3.14.6 | tech_stack.md로 맞춤(기획서 6.3 수정) |
| 렌더 이벤트 필드 | E-07-01 #4: 양자화 버전·폴백 여부 포함 | `engine/ports.py` `RenderResult`: 해당 필드 없음 | `quantization`·`fallback` 추가(contract-0.3, 결정 #18) |
| 복기 샘플 위치 | A-10-02: `fixtures/review/review_trace_sample.json`을 산출 | parallel_dev.md 4절: 픽스처는 `tools/build_fixtures.py`가 만들고 손으로 고치지 않음 | `build_fixtures.py`가 생성(A-10-02 산출물 수정) |
| 게임 이벤트 대조식 | INT-11 #4: "상태 `seq` 합" | PRD: `next_seq` = 고유 발행 건수 = bronze 행 수 | PRD 정의로 맞춤(C-07-02 #1, INT-11 #4 수정) |
