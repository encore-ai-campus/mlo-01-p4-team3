# 이벤트·학습 데이터 계약

코드 기준: `engine/domain/events.py`(봉투, 토픽), `contracts/datasets.py`(학습 행, 분할 키, 라벨 출처). 예시: `fixtures/events/sample_events.jsonl`, `fixtures/ml/observations_sample.jsonl`.

## 1. 봉투 (`EventEnvelope`)
- `event_id = game_id:seq`(5자리)다. `seq`는 판 안에서 0부터 빈틈없이 증가한다. 소비자는 `event_id`로 중복을 제거한다.
- 모든 이벤트에 들어가는 필드:
  - 버전: `contract_version`, `rule_version`, `engine_version`, `content_version`, `npc_policy_versions`, `model_versions`
  - 재현: `seed`, `preset_id`
  - 출처: `source`(human, bot, synthetic, scripted), `load_test`
- 사람 데이터 필드(`player_pseudonym`, `session_id`)는 `source=human`일 때만 채운다(privacy.md).

## 2. 토픽

| 토픽 | 이벤트 | 소비자 |
|---|---|---|
| `paldo.game` | 상태 전이 이벤트(감금, 밤, 발언 등) | C 적재, D 분석 |
| `paldo.observations` | `observation`, `decision`(같은 snapshot_id) | D 학습, C 적재 |
| `paldo.intent` | `intent_attempt` | D 의도 학습, E 검수 |
| `paldo.llm` | `render_job` | E 품질, C 지표 |
| `paldo.player` | `notebook_updated`, `survey_submitted` | D 평가, E 분석 |
| `paldo.truth` | `truth_revealed`(**판 종료 후에만**) | C 라벨 조인 |

- **`survey_submitted` 본문**(P-29, api.md 2-2절): `skipped`(bool), `fun`·`unfair`·`npc_plausible`(1~5, 건너뛰면 셋 다 `null`), `comment`(마스킹 후, 건너뛰면 `""`), `mask_version`. 저장·아웃박스·Kafka·적재 단계에서 `null` 점수를 0이나 기본값으로 바꾸지 않는다.
  - 점수 평균·보상·학습에는 `skipped=false`인 이벤트만 쓴다.
  - 응답률은 `skipped=false` 건수 ÷ 설문 이벤트 수(= 판 수)다. 건너뜀은 분모에만 들어간다.

## 3. 학습 데이터와 진실의 경계
- **스냅샷은 행동 직전에 찍는다**:
  - `observation` 이벤트는 그 결정을 내리기 직전의 관찰자 시점 상태다(`ObservationSnapshot`). 결정 이벤트(`decision`)는 같은 `snapshot_id`를 참조한다.
  - 스냅샷에는 관찰자의 자기 지식과 믿음만 들어간다. 진실은 들어가지 않는다.
- **진실은 판이 끝난 뒤 따로 들어온다**:
  - `truth_revealed`는 `phase=ended` 뒤에만 `paldo.truth`로 발행한다.
  - 라벨은 C의 배치가 `game_id`로 조인해 `TrainingRow.label`과 `label_joined_at`을 채운다.
  - 중단된 판(`completed=false`)에는 라벨을 붙이지 않는다.
- **학습 대상 관찰자**: 씐 자 확률 모델은 마을 주민 관찰자의 행만 쓴다. 씐 자 관찰자는 정답을 알고 있기 때문이다. 이 필터는 학습 쪽(D)에서 건다.
  - 마을 주민 관찰자 필터는 `decision`에도 같은 `snapshot_id` 기준으로 적용한다. 씐 자 결정(밤 명령, `avoid_partner` 등)은 판 도중 `paldo.game`에 싣지 않는다. 플레이어·봇 결정도 같은 토픽(`paldo.observations`)으로 간다(11/6 PM 결정).
- **생성 문장의 계보**: LLM이 만든 의도 예시는 `lineage_root`에 원본 예시 ID를 담는다. 분할할 때 같은 계보를 같은 쪽에 넣는다.
- **분할 키(`SplitKey`)**:
  - 사람 데이터는 `user`(가명) 단위로 나눈다. 한 사람의 판과 문장은 한 분할에만 들어간다.
  - 봇·합성 데이터는 `game_id` 단위로 나눈다.
  - 시간 검증이 필요하면 `time_bucket`을 쓴다.
- **라벨 출처와 신뢰도(`LabelSource`)**:

  | 라벨 출처 | 신뢰도와 사용 범위 |
  |---|---|
  | `human_annotation` | 높음. 평가 세트에 쓸 수 있다 |
  | `player_edited` | 중간 |
  | `player_confirmed` | 낮음 |
  | `auto_executed` | 학습 제외 또는 약한 라벨 |
  | `authored`, `generated`, `generated_reviewed` | 시드와 증강 |

- **고정 평가 세트**: `test_human` 분할은 사람이 주석한 고정 세트다. 학습, 튜닝, 문턱값 조정에 쓰지 않는다. 버전을 붙여 동결하고 동결 묶음의 `manifest.json`(파일별 바이트 수·sha256, `tools/ut_freeze.py`)을 기록한다(11/8 사용자 결정: 사용자 테스트 집계는 게임 웹 Mongo·계정 DB에서 직접 하고 Delta 버전 번호 대신 이 기록을 쓴다).
- 합성(`synthetic`) 데이터와 녹화(`scripted`) 데이터는 형식 검증과 파이프라인 시험에만 쓴다. 모델 성능 판단에는 쓰지 않는다.

## 4. 기획서 대비 결정
- 기획서 8.2에는 "행동 직전 스냅샷"만 있고, 진실 조인 시점과 토픽 분리는 없었다. 그래서 진실 전용 토픽을 두고 종료 후에만 발행하도록 정했다. 진실이 실수로 학습 특징에 섞이는 것을 막기 위해서다.

## 5. 광고 이벤트 (`ads/domain/events.py`, 상세 ads.md)

| 토픽 | 이벤트 | 기록 시점 |
|---|---|---|
| `ad.serve` | `AdServeEvent` | 경매 응답 시점. 노출이 아니다. 광고 없음도 기록한다 |
| `ad.impression` | `AdImpressionEvent` | 비콘 도착 시점, 또는 클릭이 노출 증거가 된 시점(`implied_by_click`) |
| `ad.click` | `AdClickEvent` | 클릭 리다이렉트를 지나는 시점 |
| `ad.rejudge` | `AdRejudgeEvent`(`event_id`, 원본 `source_event_id`, `charge_id`, `invalid_reason`, `judge_version`, `occurred_at_ms`, `source`, `contract_version`). 키 `charge_id`. 광고 서버 아웃박스가 아니라 Spark 재판정 작업이 직접 발행하고, `event_id`는 `rj:<charge_id>:<judge_version>`으로 결정적이라 다시 실행해도 같은 이벤트다 | Spark silver 재판정이 확정 과금 건을 무효로 바꾼 시점(C-08-02). 소비자는 광고 서버 환입 소비자(D-06-02), 11/4 PM 결정 #11. 환입 행 금액은 원래 금액의 부호 반전, 원과금당 1회(ads.md 5절, `ads-0.6`) |

- **공통 필드**:
  - 식별: `event_id`(MERGE 키), `request_id`, `serve_id`
  - 시각: `occurred_at_ms`(발생), `received_at_ms`(도착)
  - 대상: `slot`, `campaign_id`, `creative_id`, `session_pseudonym`
  - 출처: `source`(human, synthetic), `load_test`
  - 버전: `policy_version`, `ctr_version`, `bid_version`(자동 입찰 입찰가 버전, 없으면 빈 문자열), `contract_version`(`ads-0.4`부터). 노출·클릭 이벤트의 버전은 그때의 캐시 값이 아니라 serve 기록(`ServeRecord`)에 고정된 경매 시점 값을 복사한다
  - 경매 당시 값: `billing`, `price_mw`
- **serve 이벤트**:
  - 요청 맥락 `context`(`AdRequest.context` 복사: `preset_id`, `outcome`(win, lose, 없음), `returning`, `hour`). 노출·클릭 이벤트에는 싣지 않고, silver가 `serve_id`로 serve에 조인해 붙인다(11/5 PM 결정, `ads-0.4`). 맥락 조인은 분석·학습용이고 판정·과금을 바꾸지 않는다
  - 맥락 값 검증: 광고 서버가 serve 시점에 `preset_id`는 프리셋 이름(`engine/domain/model.py` `PRESETS`), `outcome`은 열거값, `hour`는 0~23만 받고 그 밖은 비운다(자유 문자열로 식별 값이 들어오지 않게. 세션 가명 ID 말고 개인 식별 값은 넣지 않는다, privacy.md)
  - 후보 목록: 순위 CTR, 평균 CTR, eCPM, 탈락 사유
  - 경매 결과: `threshold_ecpm_mw`(T), `rank_ctr`, `capped`
  - 예약: `reserved_charge_id`, `reserved_mw`(1건 금액: CPC 청구 CPC, CPM `floor(청구 CPM/1000)`, ads.md 2절, `ads-0.6`)
- **노출·클릭 이벤트**:
  - 판정: `status`(pending, valid, invalid), `invalid_reason`, `judge_version`
  - 같은 HTTP 이벤트가 `pending`에서 최종 판정으로 바뀌면 최종 행은 새 `event_id`를 쓰고 `source_event_id`로 pending 행을 가리킨다. 같은 행의 Kafka 재발행은 기존 `event_id`를 유지한다.
  - 과금: `charge_id`, `charge_mw`(확정했을 때만. 같은 건의 `reserved_mw`와 같다. 11/7 PM 결정으로 나머지 이월을 폐지해 1건 금액이 그 건의 `billing`·`price_mw`만으로 정해진다, `ads-0.6`. `ads-0.5` 이하 이벤트의 CPM `charge_mw`는 나머지 이월이 반영된 값이라 새 식으로 다시 계산하지 않는다)
  - 재판정·환입: `source_event_id`(원본 이벤트 ID)
  - 클릭에만: `suspect`
- `source=synthetic` 이벤트는 합성 전용 캠페인(`syn:`)만 가리킨다. 실제 집계와 학습 평가 보고에서는 분리한다.
- 광고 이벤트는 게임 `EventEnvelope`를 쓰지 않는다. 광고 서버가 MySQL 아웃박스로 발행한다(`ad.rejudge`만 Spark 재판정 작업이 직접 발행).
