# D ML·광고 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 시뮬레이터, 의도 해석기, 씐 자 확률 모델, 광고 서버.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/sim.md`, `docs/modules/intent.md`, `docs/modules/ml.md`, `docs/modules/ads.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/D_ml_ads.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 D 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, D 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").
- 다른 영역 문서에 있는 D 작업: 광고 비콘·클릭 중계(진입점 B, [`B.md`](B.md)), 끝난 실제 에셋 파일·생성 기록 A-07-04·A-08-04(규격·검사와 함께 E, [`E.md`](E.md). 남은 에셋은 11/10 B), E-11-10 외부 검수.

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `engine/sim/` | 도메인 사용 | D | 플레이어 봇 4종(`bots.py`), GameEngine 포트 실행기(`runner.py`), 결과 지표 표(`report.py`). 결정 층 최적화는 A가 `engine/domain`에서 맡는다 |
| `intent/`, `ml/` | 인프라(모델) | D | 의도 해석기, 씐 자 확률 모델(예정) |
| `ads/domain/` | 광고 도메인 | D | 경매, 2등 가격, 예산 예약·확정·해제, 페이싱, CTR, 유효 판정, 대사, 지면 규격(`SLOT_SIZES`)(표준 라이브러리만) |
| `ads/app/`, `ads/infra/`, `ads/web/` | 광고 응용, 인프라, 표현 | D | 요청·비콘·클릭, Django 뷰·admin, MySQL 원장·판정·아웃박스, 별도 만료·Kafka 작업자 |
| `ads/project/`, `ads/manage.py` | 광고 서비스 설정·진입점 | D | 게임 웹과 독립된 Django 설정·URL·WSGI |
| `tests/ads/` | 서비스 테스트 | D | MySQL 캠페인·admin과 광고 HTTP·예산·판정 검증 |
| `ads/testing/` | 테스트 지원 | D | `FakeAdProvider`, 운영 API를 실행하는 격리 합성 HTTP 시험 호스트 `traffic_server.py` |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 모델 교체·승격·롤백 | `docs/contracts/ml_lifecycle.md` |
| 사람 의도 재학습·대역 봇 보정·격리 롤백 | `tools/demo_retrain.sh`, `ml/suspicion/player_calibration.py`, `ml/lifecycle_demo.py`, [D-10-01 증빙](../evidence/D-10-01.md) |
| 평가 관문·전략 밴딧 배치·B/C 연결 | `ml/gate.py`, `ml/bandit.py`, `ml/mongo_bandit.py`, `docs/reports/D-09-02-handoff.md`, 증빙 `docs/evidence/D-09-02.md` |
| 로컬 BERT IntentParser 주입 | `intent/bert.py`, `intent/README.md`, `docs/reports/D-09-02-handoff.md` |
| 씐 자 모델 로컬 첫 학습·평가 | `ml/suspicion/features.py`(실제 엔진→TrainingRow), `train.py`(게임 분할·학습·MLflow), `evaluate.py`(softmax·지표), `tests/test_suspicion.py`, `docs/evidence/D-07-02.md` |
| 학습 미사용 씐 자 전략 평가 | `ml/suspicion/strategies.py`(거짓 행적 유형 고정), `train.py --heldout-rows`(판·seed 분리 및 별도 지표) |
| 승인된 의도 예시로 CPU BERT 미세조정·기준선 비교 | `intent/finetune.py`, CLI `tools/perf/intent_finetune.py`, 증빙 `docs/evidence/D-07-03.md`, 모델·계보 기록 `mlruns/intent_bert/`(Git 제외) |
| BERT 반례·사람 평가 | `intent/human_eval.py`, CLI `tools/perf/intent_human_eval.py`, 증빙 `docs/evidence/D-09-01.md` |
| 플레이어 봇 실행·같은 시드 재현·지표 표·처리량 측정 | `engine/sim/`, CLI `tools/perf/sim_bench.py`, 테스트 `tests/sim/`, 증빙 `docs/evidence/D-06-01.md` |
| 경매·과금·광고 판정 규칙 변경 | 광고 도메인 `ads/domain/`, `docs/contracts/ads.md` |
| 광고 서버 실행·admin·HTTP 검사·지연 측정 | 표현 `ads/manage.py`·`ads/web/`, 응용 `ads/app/`, 설명 `docs/modules/ads.md`, 측정 `tools/perf/ads_bench.py` |
| 광고 원장·만료·발행·환입 검증 | `ads/infra/`, `ads/manage.py ad_worker`, `tests/ads/`, 증빙 `docs/evidence/D-06-02.md` |
| CTR·5분 페이싱 갱신·60초 재적재·시나리오 1~5 | `ads/app/ctr_update.py`, `ads/app/pacing_update.py`, `ads/infra/optimization.py`, `ads/manage.py ad_update`, `tools/ad_scenarios.py`, 계획 `docs/reports/D-08-02-plan.md`, 증빙 `docs/evidence/D-08-02.md` |
| 맥락 CTR·카나리아·CPC 자동 입찰·시나리오 6 | `ml/ad_ctr.py`, `ml/gate.py`, `ads/app/bid_update.py`, `ads/manage.py ad_update`, `pipeline/spark/ads_gold.py --export-optimization`, `tools/ad_ctr_scenario.py`, 증빙 `docs/evidence/D-10-02.md` |
| 합성 광고 HTTP 트래픽·피로·무효 클릭·재처리 검증 | `tools/ad_traffic_sim.py`, `ads/testing/traffic_server.py`, `tools/perf/ad_traffic_bench.py`, `tests/tools/test_ad_traffic_sim.py`, 설명 `docs/modules/ads.md` |
| 모델 포트·모델 판 실행·폴백 검사 | `ml/adapters/suspicion_model.py`, `ml/adapters/run_model_games.py`, `tests/sim/test_suspicion_adapter.py`, 증빙 `docs/evidence/D-08-01.md` |
| 실제 모델 웹 2프로세스 부하 | `tools/perf/api_bench.py --web-processes 2 --mlruns <디렉터리> --suspicion-version <버전>`, `server/tests/multiproc_bench.py` |
