# 저장소 구조 계약 (디렉터리 트리)

- 목적: 작업자가 새 파일을 **어디에 만들지** 미리 알게 한다. 태스크 문서의 산출물 경로를 한곳에 모은 것이다.
- 원본 우선순위: 태스크 문서(`docs/tasks/*.md`)의 산출물 경로 > 이 문서. 둘이 다르면 태스크 문서를 따르고 이 문서를 고친다.
- 계층 규칙과 의존 방향: [루트 README "계층과 의존 방향"](../../README.md#계층과-의존-방향). 이 문서는 위치만 적는다.
- 영역 사이 호출 방법: [integration.md](integration.md).
- 표기: `(있음)` = 11/4 기준 저장소에 있음. `[ID]` = 그 태스크가 만든다. `결정 #N` = 11/4 PM 영역 간 호출 결정([integration.md 7절](integration.md#7-114-pm-결정-영역-간-호출-19건))으로 태스크에 더한 경로.

## 1. 전체 트리

- 패키지만 미리 생성(11/5): `engine/sim/`, `engine/domain/npc/`, `intent/`, `ml/`(`suspicion/`, `adapters/`), `llm/`(`service/`), `pipeline/`(`sinks/`), `server/infra/`, `ads/app/`, `ads/infra/`, `tests/engine/`·`tests/sim/`·`tests/intent/`. 안은 `__init__.py`뿐이고, 파일은 표의 태스크가 만든다. 폴더를 다시 만들지 않는다. Django 앱(`server/paldo_web/`, `server/accounts/`, `ads/project/`, `ads/web/`)과 `tests/ads/`·`tests/infra/`는 담당 태스크가 만든다(startapp, 서비스 테스트 `load_tests`).

```
paldo/
├─ README.md                       라우팅 문서 1단(영역 안내, 공용 폴더·작업 위치) (있음). 영역 상세는 docs/routing/<흐름>.md [P-27]
├─ .env.example                    환경 변수 이름 + 자리표시자만 [C-05-03]
├─ .gitignore                      out/, .env, .venv/ 등 (있음)
├─ .github/                        저장소 운영 · E
│  ├─ ISSUE_TEMPLATE/, labels.yml, pull_request_template.md (있음)
│  └─ (workflows/ 없음)            GitHub Actions CI는 11/5 폐지 [E-05-02]
├─ .githooks/pre-push              push 전 로컬 검사(tools/precheck.py) [E-05-02]
│
├─ engine/                         도메인 · A (표준 라이브러리만)
│  ├─ ports.py                     엔진이 바깥에 기대는 포트 (있음)
│  ├─ domain/
│  │  ├─ model.py, content.py, events.py, serde.py   (있음)
│  │  ├─ engine.py                 GameEngine 구현 [A-05-01, A-05-02, A-06-01]
│  │  ├─ params.py                 밸런스 상수 [A-05-01]
│  │  ├─ observation.py            관측 투영·스냅샷 [A-06-02]
│  │  ├─ npc/                      beliefs.py, policy_villager.py, policy_possessed.py [A-07-01], vote.py [A-07-02]
│  │  ├─ duo.py [A-08-01], summarize.py [E-08-08], progress.py [E-09-08], review.py [E-10-10]
│  │  └─ decision_params.json      결정 층 파라미터 [A-08-03]
│  ├─ testing/fakes.py             엔진 포트의 가짜(광고 가짜는 ads/testing/fakes.py) (있음)
│  └─ sim/                         도메인 사용 · D: bots.py, runner.py, report.py [D-06-01]
│
├─ server/                         게임 웹 Django 프로젝트 · B (infra는 C·E)
│  ├─ manage.py                    [B-05-01]
│  ├─ paldo_web/                   settings.py, middleware.py [B-05-01], asgi.py(HTTP·웹소켓 분기) [B-07-05]
│  ├─ accounts/                    계정(표현·인프라): models.py(계정·가명), validators.py(비밀번호 규칙), lockout.py(5회 실패 15분 잠금), views.py(가입·로그인·로그아웃) [B-06-04]
│  ├─ api/                         표현: viewmodels.py (있음), views.py [B-05-02], consent.py [B-06-03], routing.py·consumers.py(웹소켓 받기·검증) [B-07-05]
│  ├─ app/                         응용: dto.py, ads_port.py (있음, 중계 메서드 [B-07-05]), usecases.py [B-05-02], ad_events.py(광고 신호 중계) [B-07-05], presets_enabled.json [B-08-02]
│  ├─ infra/                       인프라(C 저장소·발행기·조회, E LLM 렌더러, B 광고 HTTP 클라이언트·모델 선택 적용)
│  │  ├─ mongo_repo.py, content_store.py   [C-06-01]
│  │  ├─ outbox_publisher.py       [C-06-02]
│  │  ├─ llm_renderer.py           LineRenderer 구현 [E-08-09, 11/7 P-31 (나)로 취소]
│  │  ├─ ops_query.py              운영 지표 조회(읽기 전용) [C-10-03, 11/7 취소]
│  │  ├─ ad_client.py              AdProvider HTTP 구현(serve·비콘·클릭·랜딩 중계) [B-08-03] 결정 #2·#6
│  │  └─ model_selector.py         ModelSelector 최소 구현 [D-08-04] → 카나리아·고정 [B-09-01], 레지스트리 [C-09-01] 결정 #5
│  └─ tests/                       Django 테스트: test_api.py [B-05-02], test_idempotency.py [B-06-02], test_accounts.py [B-06-04], test_ws.py [B-07-05]
├─ web/
│  ├─ templates/                   meeting.html와 htmx 조각 [B-05-03], 이후 화면 [B-06-01 …]
│  └─ static/js/paldo_ws.js        웹소켓 연결·재연결·비콘 대기열 [B-07-05]
│
├─ ads/                            광고 서버 · D (별도 Django 서비스, ad-01)
│  ├─ manage.py, project/          광고 서버 전용 Django 설정·URL [D-05-03] 결정 #1
│  ├─ domain/                      model.py, rules.py, events.py (있음)
│  ├─ app/                         serve.py, beacon.py, click.py [D-05-03]
│  │                               expire_reservations.py, refund_consumer.py [D-06-02]
│  │                               ctr_update.py, pacing_update.py [D-08-02]
│  │                               bid_update.py [D-10-02]
│  ├─ infra/                       mysql_ledger.py, mysql_store.py, outbox.py, schema.sql [D-06-02], optimization.py [D-08-02]
│  ├─ web/                         Django 앱(내부 API, 비콘, 클릭, 가상 랜딩, admin) [D-05-03]
│  └─ testing/                     fakes.py(FakeAdProvider), traffic_server.py(D-06-03 격리 HTTP 시험 호스트)
│
├─ intent/                         의도 해석기 · D
│  ├─ baseline.py, matcher.py, parser.py   [D-05-01]
│  └─ README.md                    기준선 ↔ BERT 전환 방법 [D-07-01]
├─ ml/                             모델 · D
│  ├─ suspicion/                   features.py, train.py, evaluate.py [D-07-02]
│  ├─ adapters/suspicion_model.py  SuspicionModel 구현 [D-08-01]
│  ├─ adapters/run_model_games.py  모델 v0 판 실행 진입점 [D-08-01]
│  ├─ adapters/reproduce_model_games.py  같은 모델·시드 두 실행 재현 비교 [D-08-04]
│  ├─ gate.py, bandit.py           평가 관문·P-16 보상·버전 고정 공급자 [D-09-02]
│  ├─ mongo_bandit.py              전체 이벤트 export 배치·Mongo CAS·C 삭제 인계 [D-09-02]
│  └─ ad_ctr.py                    광고 CTR LightGBM [D-10-02]
├─ llm/                            LLM · E
│  ├─ bench/run_bench.py           [E-06-07]
│  ├─ prompt.py, validator.py, eval_set.py  추궁 프롬프트·검증기·평가 세트, 순수 함수(표준 라이브러리만) [E-07-07]
│  ├─ mvp_metrics.py               MVP 지표(성공률·지연·일관성) [E-07-07]
│  ├─ colab/paldo_llm_mvp.ipynb    MVP 코랩 노트북, 출력 셀 비움 [E-07-06]
│  ├─ service/                     HTTP 서비스 [E-08-09, 11/7 P-31 (나)로 취소]
│  └─ graph.py                     LangGraph 흐름 [E-08-09, 11/7 P-31 (나)로 취소]
│
├─ pipeline/                       데이터 파이프라인 · C
│  ├─ topics.yaml, consumers/      [C-06-02]
│  ├─ sinks/kafka_sink.py          시뮬레이터용 EventSink Kafka 구현 [C-08-03] 결정 #10
│  ├─ spark/                       ingest_bronze.py, build_silver.py, join_labels.py [C-07-01]
│  │                               ads_silver.py, ads_gold.py, ads_reconcile.py [C-08-02]
│  └─ README.md                    집계 정의 [C-07-01, C-08-02]
├─ ops/                            배포·관측 · C
│  ├─ inventory_check.md           [C-05-01]
│  ├─ compose/                     dev.yml, README.md(팀원별 점검표) [C-05-03]
│  ├─ mysql/                       [C-06-03]
│  ├─ proxy/                       Caddyfile, README.md(HTTPS·wss, 인증서 경고 안내) [C-07-04]
│  ├─ prometheus/ (alerts.yml), grafana/dashboards/*.json   [C-08-01]
│  ├─ k3s/                         jobs/sim-job.yaml, images/sim/Dockerfile [C-08-03], cronjobs/retrain.yaml [C-10-01]
│  ├─ mlflow/                      [C-09-01]
│  ├─ aws/                         cost_estimate.md [C-09-03], 배포 스크립트 [C-10-02]
│  └─ runbook/                     vm_roles.md, schedule.md [C-05-02], privacy.md [C-09-02],
│                                  rollback.md [C-10-01], aws.md [C-10-02], README.md [C-11-03]
│
├─ content/                        콘텐츠 데이터 · E
│  ├─ bundle_v1.json, README.md    [E-05-03]
│  ├─ intent/examples_v1.jsonl     [E-06-02]
│  └─ images/                      <kind>/<id>.png(임시 [E-05-05]·실제 [A-09-05 …] 같은 경로), manifest.json [E-05-05], SOURCES.md(생성 기록) [A-08-04]
├─ contracts/datasets.py           데이터셋 계약 (있음)
├─ fixtures/                       생성물(`tools/build_fixtures.py`) (있음) + review/review_trace_sample.json [E-10-10]
│
├─ tools/                          도구 · 공용
│  ├─ build_fixtures.py, build_ads_fixtures.py, gen_synthetic.py, gen_synthetic_ads.py,
│  │  tasks_md.py, tasks_to_issues.py   (있음)
│  ├─ check_evidence.py, check_docs.py, check_secrets.py, secrets_allowlist.txt, evidence_result.py [E-05-02]
│  ├─ run_engine_games.py, check_handoff_a06.py [A-06-03], optimize_decision.py [A-08-03]
│  ├─ load_content.py [C-06-01] 결정 #8, replay_progress.py [C-09-04] 결정 #19
│  ├─ reconcile_game_events.py [C-07-02], ml_gate.py [C-09-01], privacy_delete.py, privacy_retention.py [C-09-02]
│  ├─ ad_traffic_sim.py [D-06-03], ad_scenarios.py [D-08-02], demo_retrain.sh [D-10-01]
│  ├─ ad_ctr_scenario.py [D-10-02]   시나리오 6 이벤트 재생 입력과 정답 채점
│  ├─ build_llm_bundle.py [E-07-06] LLM MVP 제출 묶음 zip(out/llm_mvp/, 커밋 제외)
│  ├─ perf/                        engine_bench.py [A-05-01], vm_probe.sh [C-05-01], api_bench.py [B-06-02], ad_fault_test.py [B-08-03]
│  └─ assets/                      make_placeholders.py [E-05-05], check_assets.py [E-08-07], cutout.py [A-08-04]
│
├─ tests/                          unittest 루트(`python -m unittest discover -s tests -t .`)
│  ├─ contract/, tools/            (있음)
│  ├─ engine/ [A], sim/ [D-06-01], intent/ [D-05-01], ads/ [D-05-03], infra/ [C-06-01], assets/ [E-05-05, E-08-07, A-08-04]
│  └─ fixtures/secrets/            탐지 시험용 가짜 값(검사 제외 경로). 개별 예외는 tools/secrets_allowlist.txt [E-05-02]
│
├─ docs/                           문서 (3절)
└─ out/                            실행 산출물(커밋 제외): logs/, perf/, handoff/a06/, ad_scenarios/, ad_truth/(합성 광고 정답, 평가 전용 [D-06-03]), ad_traffic/(HTTP 관측 이벤트·요약 [D-06-03])
```

## 2. 폴더별 계층·담당 요약

| 폴더 | 계층 | 담당 | 실행 위치(통합 환경) |
|---|---|---|---|
| `engine/` | 도메인 | A(시뮬레이터 `engine/sim/`은 D) | 게임 웹 프로세스 안, 시뮬레이터 Job 안(라이브러리) |
| `server/api/`, `web/` | 표현(HTTP 뷰·웹소켓 Consumer) | B | app-01(Daphne 프로세스 1개, 앞에 HTTPS 프록시 `ops/proxy/`) |
| `server/app/` | 응용 | B | app-01 |
| `server/infra/` | 인프라 | C(저장소·발행기·조회), E(LLM 렌더러), B(광고 HTTP 클라이언트·모델 선택 적용) | app-01 |
| `ads/` | 광고 서버 전 계층 | D | ad-01 |
| `intent/`, `ml/` | 인프라(모델) | D | 추론은 app-01(게임 웹과 같은 프로세스로 먼저 시험, 결정 #4), 학습은 nifi-01·k3s |
| `llm/` | 인프라(별도 서비스) | E | llm-01(또는 노트북 3 Windows 직접 실행) |
| `pipeline/` | 인프라(배치) | C | spark-master-01, spark-worker-01 |
| `ops/` | 배포·관측 설정 | C | 각 VM |
| `content/` | 데이터 | E | `tools/load_content.py`로 Mongo에 적재해 사용(결정 #8) |
| `content/images/` 실제 파일, `tools/assets/cutout.py` | 데이터(에셋) | A 흐름 `[에셋]` 태스크(실제 에셋 제작, 11/5 B에서 이전. 담당 D, 11/5 PM 결정으로 담당 변경). 규격·임시·검사는 E | 정적 파일 |
| `contracts/`, `fixtures/` | 계약·생성물 | 공용 | - |
| `tools/` | 도구 | 공용 | 개발 PC, Bastion |

VM 이름과 배치의 원본은 `ops/runbook/vm_roles.md`다(11/6 기획서 v1.1 6.3에서 옮김). 실제 노트북 대응은 저장소 밖 비공개 문서에 있다.

## 3. 문서 폴더

| 경로 | 내용 | 기준 |
|---|---|---|
| `docs/paldo_design_v1.md`, `docs/team_plan.md`, `docs/brd.md`, `docs/prd.md` | 기획서, 팀 계획, 요구 문서 | - |
| `docs/design_notes.md` | 기획서 보조 기록(설계 결정 기록, 미확정 사항, 서버 배치 요약, 이전 판 절 번호 대응표) | - |
| `docs/contracts/` | 계약(이 문서 포함) | [parallel_dev.md 4절](parallel_dev.md) 변경 절차 |
| `docs/tasks/` | 흐름별 태스크와 완료 조건 | [tasks/README.md](../tasks/README.md) |
| `docs/routing/<흐름>.md` | 라우팅 문서 2단(A~E 영역별 폴더 안내·작업별 위치) | 루트 README "영역 안내", tasks/README.md 10절 [P-27] |
| `docs/evidence/<ID>.md` | 태스크 증빙 | tasks/README.md 4절 |
| `docs/evidence/perf/<ID>/<이름>.json` | 측정 결과 JSON | tasks/README.md 4절 "결과 JSON 형식" |
| `docs/modules/<모듈>.md` | 모듈 문서(무엇을·왜·사용법·설정값·한계) | [modules/README.md](../modules/README.md) |
| `docs/troubles/<흐름>.md` | 트러블 기록(A~E, 발표 트랙 S) | tasks/README.md 10절 |
| `docs/reports/` | llm_bench.md [E-06-07], user_test.md [E-10-03], ops_agent/ [E-10-02, 11/7 P-31 (나)로 취소], ads_scenarios.md [D-10-02], presets.md [D-10-03] | - |
| `docs/presentation/` | A.md~D.md(흐름별 기록), outline.md [S-10-01], paldo_final.pptx [S-11-06] | P_presentation.md |
| `docs/assets/` | style_guide.md, spec.md [E-05-05] | E_pm.md 에셋 |
| `docs/user_test/plan.md` | 사용자 테스트 일정·절차(명단 없음) [E-06-04] | privacy.md |

## 4. 테스트 배치 규칙

| 테스트 | 위치 | 실행 |
|---|---|---|
| 계약 테스트(표준 라이브러리만) | `tests/contract/` | CI `core` |
| 흐름별 단위 테스트 | `tests/<영역>/`(engine, sim, intent, ads, infra, assets, tools) | CI `core`(unittest discover) |
| Django 테스트 | `server/tests/` | CI `django`(`python manage.py test`) |
| 측정 스크립트 | `tools/perf/` | 수동·통합. 결과는 `docs/evidence/perf/` |

- 새 테스트 폴더에는 빈 `__init__.py`를 둔다. 없으면 unittest가 그 폴더를 건너뛴다.
- 테스트 파일 이름은 `test_*.py`다.
- 태스크가 정한 위치는 `tests/infra/`(Mongo, C-06-01), `tests/ads/`(광고 Django·MySQL, D-05-03·D-06-02), `server/tests/`(게임 Django)다. 그런데 CI `core`는 서비스 없이 `tests/` 전체를 discover하고, CI `django` 예시는 `server/tests/`만 돈다(E-05-02 워크플로 예시). 그래서 `core`는 `tests/infra/`·`tests/ads/`를 discover에서 빼고(그 폴더의 `load_tests`가 `PALDO_SERVICE_TESTS` 없으면 빈 묶음), 서비스 CI 작업이 세 폴더를 경로별로 실행한다(결정 #12, E-05-02).

## 5. 11/4 PM 결정 (이 문서 관련)

번호는 [integration.md 7절](integration.md#7-114-pm-결정-영역-간-호출-19건) 표와 같다. 여기서는 위치에 관한 것만 요약한다.

| # | 항목 | 결정 |
|---|---|---|
| 1 | 광고 서버 Django 프로젝트 위치 | `ads/project/`(settings·urls) + `ads/manage.py`. 게임 웹 프로젝트와 설정을 공유하지 않는다 |
| 5 | `ModelSelector` 실제 구현 파일 | `server/infra/model_selector.py` |
| 6 | `AdProvider` HTTP 클라이언트 파일 | `server/infra/ad_client.py` |
| 2 | 브라우저 광고 신호 경로(11/4 변경) | 웹소켓 표현 `server/api/consumers.py` → 응용 `server/app/ad_events.py` → 포트 `server/app/ads_port.py` → 인프라 `server/infra/ad_client.py`. ASGI 진입점 `server/paldo_web/asgi.py`, 프록시 `ops/proxy/` |
| 8 | 콘텐츠 적재 도구 | `tools/load_content.py` |
| 10 | 시뮬레이터 Kafka `EventSink` | `pipeline/sinks/kafka_sink.py` |
| 19 | 진척 재처리 도구 | `tools/replay_progress.py`(C-09-04) |
| 12 | 외부 서비스가 필요한 테스트 실행 | `core`에서 제외하고 서비스 CI에서 경로별로 명시 실행(integration.md 7절 #12) |

## 6. 허용 폴더(검사 기준)

`python -m tools.check_structure`가 아래 펜스 블록을 읽어 git이 추적하는 파일의 폴더를 검사한다([ci.md](../modules/ci.md)). 1절 트리를 폴더 단위로 옮긴 것이다(11/5 PM 결정).

- 패턴은 한 줄에 하나다. `dir/` = 그 폴더 자체만 허용하고 하위 폴더는 따로 적어야 한다. `dir/**` = 그 폴더와 아래 전부를 허용한다. `#` 뒤는 설명이다.
- 루트에는 파일만 둔다(README.md, .env.example, .gitignore, .gitattributes). 루트 폴더는 아래 목록에 있는 것만 허용한다.
- `**`를 준 곳: 하위 구조를 그 영역 도구나 프레임워크가 정하는 곳이다. 예를 들어 Django 앱의 `migrations/`, `templates/`, 문서·픽스처·테스트·콘텐츠 폴더, ops의 서비스별 설정이 그렇다. 나머지 코드 폴더는 1절에 적은 깊이까지만 허용한다.
- 1절 트리와 이 목록이 어긋나면 이 목록이 검사 기준이다. 둘 다 같은 변경에서 고친다.
- **폴더를 새로 만들려면** PM 승인을 받은 뒤 이 절과 1절 트리를 먼저 고친다([parallel_dev.md 4절](parallel_dev.md#4-규칙) 계약 변경 절차와 같다). 승인 없이 목록만 고쳐 검사를 통과시키지 않는다.
- 로컬 CI(`interim submit`)는 `--diff <base>`로 새로 생긴 폴더만 본다. 로컬 검사 `tools/precheck.py`(push 때 `.githooks/pre-push`)는 `--all`로 전체를 본다.

```
.github/**
.githooks/                 # pre-push 훅(E-05-02, 11/5 CI 폐지)
engine/
engine/domain/
engine/domain/npc/
engine/testing/
engine/sim/
server/
server/paldo_web/**        # Django 프로젝트 설정
server/accounts/**         # Django 앱(migrations 등)
server/api/
server/app/
server/infra/
server/tests/**
web/**                     # templates/, static/js/
ads/
ads/project/**             # 광고 서버 Django 설정
ads/domain/
ads/app/
ads/infra/
ads/web/**                 # 광고 서버 Django 앱
ads/testing/
intent/
ml/
ml/suspicion/
ml/adapters/
llm/
llm/bench/
llm/colab/
llm/service/**
pipeline/
pipeline/consumers/**
pipeline/sinks/
pipeline/spark/
ops/
ops/compose/**
ops/mysql/**
ops/proxy/**
ops/prometheus/**
ops/grafana/**
ops/k3s/**
ops/mlflow/**
ops/privacy/**
ops/aws/**
ops/runbook/**
content/**
contracts/
fixtures/**
tools/
tools/perf/
tools/assets/
tests/**
docs/**
requirements/
```
