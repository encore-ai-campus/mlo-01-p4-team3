# 포트 계약 (엔진 ↔ 바깥)

코드 기준: `engine/ports.py`. 가짜 구현: `engine/testing/fakes.py`. 모든 가짜 구현은 `tests/contract/test_fakes.py`로 계약 준수를 확인한다.

| 포트 | 구현 담당 | 가짜 | 호출하는 쪽 |
|---|---|---|---|
| `GameEngine` | A | `ScriptedGameEngine`(녹화 판 재생) | B 응용, D 시뮬레이터 |
| `SuspicionModel` | D | `FakeSuspicionModel` | A 엔진(NPC 추정 층) |
| `IntentParser` | D | `FakeIntentParser` | B 응용 |
| `LineRenderer` | E(LLM), C(배포) | `FakeLineRenderer` | B 응용 |
| `GameRepository` | C(Mongo) | `InMemoryGameRepository`(기준 동작) | B 응용 |
| `ProgressRepository` | C | `InMemoryProgressRepository` | B 응용 |
| `ContentSource` | C(저장), E(내용) | `FixtureContentSource` | A·B |
| `ModelSelector` | C(레지스트리), B(적용) | `FakeModelSelector` | B 응용(판 시작) |
| `EventSink` | C | `ListEventSink` | **시뮬레이터·배치 전용** |
| `AdProvider`(`server/app/ads_port.py`) | D(광고 서버), B(HTTP 클라이언트) | `ads/testing/fakes.py::FakeAdProvider` | B 응용(광고 칸 지연 로드) |

## 1. 이벤트 처리 (통일 규칙)
- 엔진은 이벤트를 **반환만 한다**(`TransitionResult.events`). 직접 발행하지 않는다.
- 웹 경로: B의 응용 계층이 `DomainEvent`를 `EventEnvelope`로 감싼다. 이때 판 메타의 버전들과 `seq`, `event_id = game_id:seq`를 채운다. 그다음 `GameRepository.commit`으로 **상태, 진실, 아웃박스, 명령 기록을 한 번에** 쓴다. Kafka 발행은 C의 아웃박스 발행기가 한다.
- 시뮬레이터·배치 경로(아웃박스가 없는 곳)만 `EventSink.append`로 직접 보낸다. 웹 경로는 `EventSink`를 쓰지 않는다.
- **상태를 바꾸지 않는 이벤트**(`intent_attempt`, 카드 반응, `notebook_updated`, `survey_submitted`, `render_job`)는 `GameRepository.append_events(game_id, events, dedup_key)`로 같은 아웃박스에 넣는다.
  - 상태 버전은 올리지 않는다. 따라서 해석 직후에 발급한 카드는 계속 유효하다.
  - `dedup_key` 예: `attempt:<attempt_id>:parsed`, `attempt:<attempt_id>:<reaction>`, `render:<job_id>:<status>`.
- `seq`와 `event_id`는 **저장소가 판 문서의 `next_seq` 카운터로 매긴다**(commit과 append_events 공용). 호출자가 넣은 값은 무시한다.

## 2. 명령 처리 책임 경계

| ⁠계&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | ⁠당&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; | ⁠칙&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; |
|---|---|---|
| ① 중복 명령 확인 | B(응용) | `find_command(game_id, command_id)`가 있으면 저장된 응답을 그대로 반환한다(`replayed=true`). 엔진은 호출하지 않는다 |
| ② 버전 확인 | B(응용) | 저장된 `state_version` ≠ `expected_state_version`이면 엔진을 호출하지 않고 `state_version_conflict`와 현재 화면을 반환한다 |
| ③ 전이 | A(엔진) | `apply`가 수락하거나 거부한다. 거부는 저장하지 않는다(상태 불변, 소비 없음) |
| ④ 원자 저장 | C(저장소) | `commit(expected_version, …)`은 비교 후 교체(CAS)다. 저장된 버전 ≠ expected이면 `VersionConflict`를 낸다. 새 버전은 expected+1이다. 상태, 진실, 아웃박스, 명령 기록을 **한 문서에 한 번에** 쓴다 |
| ⑤ 경합 처리 | B(응용) | ④에서 `VersionConflict`가 나면 ①을 다시 확인한다. 같은 `command_id`가 이미 저장됐으면 그 응답을 반환하고, 아니면 409 `state_version_conflict`를 반환한다 |

- **재시도 규칙**:
  - 서버는 명령을 자동으로 다시 적용하지 않는다. 플레이어는 이전 화면을 보고 결정했기 때문이다.
  - 네트워크 같은 일시 오류로 commit 결과를 모르면, 같은 인자로 한 번만 다시 commit한다. 첫 시도가 이미 성공했다면 `VersionConflict`가 나고, ⑤에서 저장된 응답이 반환된다.
  - 클라이언트는 같은 `command_id`로 재전송해도 안전하다.
- **기획서 대비 결정**: 진실(`GameTruth`)도 같은 판 문서의 별도 필드에 둔다. 기획서는 판 문서에 상태와 아웃박스만 적었다. 진실이 상태와 함께 원자적으로 바뀌어야 하기 때문이다(고백, 밤 처리). 화면용 직렬화와 로그 이벤트에서는 제외한다.

## 3. 의심 모델 출력 (`SuspicionEstimate`)
- **후보와 순서**: 스냅샷 `candidates` 그대로다. 관찰자와 무당을 뺀 활동 주민이며, ID 순으로 정렬돼 있다. 출력 `candidates`도 같은 순서여야 한다.
- **`probs`**: 무당이 무고하다는 조건 아래, 후보가 씐 자일 주변확률이다(0~1). 합은 `n_possessed_remaining`(1 또는 2)이다.
- **`pair_probs`**: 남은 창귀가 2명일 때만 있다. 키는 `"a|b"`(ID 정렬), 합은 1이다. `probs[c]`는 c가 들어간 쌍들의 합이다.
- **`contributions`**: 후보 → `EvidenceGroup` → 로그 오즈 기여값이다(양수일수록 의심). 성향 가중치(`Temperament.group_weights`)는 모델이 아니라 엔진의 결정 층이 곱한다.
- **무당 의심 결합(A 규칙 v0)**:
  - 무당 의심도는 q = q_max × (100 − trust_in_mudang)/100이다.
  - 주민 후보의 최종 의심도는 (1 − q) × probs[c]다.
  - q_max는 A의 규칙 상수이며(초기 0.5), 시뮬레이션으로 조정한다.
- **실패 처리**:
  - `ModelUnavailable`, `FeatureVersionMismatch`, 시간 초과(엔진 측 100ms)가 나면 그 결정만 규칙 v0 의심도(`beliefs.suspicion`)로 대신한다.
  - 이때 decision 이벤트의 `policy_version`에 `-fallback`을 붙인다. 판은 계속 진행한다.
- **버전 호환**:
  - 모델 산출물은 자기 `feature_version`을 선언한다. `ModelSelector`는 엔진의 특징 버전과 다른 모델을 고르지 않는다.
  - 판 시작 시 고른 버전을 `GameMeta.model_versions`에 고정한다. 판 도중에는 바꾸지 않는다(ml_lifecycle.md).
- 응용 계층은 판마다 `ModelSelector.suspicion_model(v)`과 `intent_parser(v)`로 **고정된 버전의 인스턴스**를 얻는다. 진행 중인 판이 쓰는 이전 버전은 그 판이 끝날 때까지 메모리에 유지한다(최대 2개 버전). `ParseResult.versions["model"]`이 판 메타와 다르면 경보를 낸다.
- 고백자 등 확정 무고는 후보에서 이미 빠져 있다(domain.md 6-1). 모델은 받은 후보에만 확률을 준다.

## 4. LLM 비동기 작업 (`LineRenderer`)
- 엔진이 `RenderRequest`를 만든다(`TransitionResult.render_requests`). 사실 문장(`fact_sentence`)과 폴백 문장은 엔진이 완성한다. LLM은 말투만 입힌다.
- 수명주기: `queued → running → done | failed | timeout | cancelled`. 폴백이 확정된 뒤 도착한 결과는 `discarded`가 된다.
- **작업 ID와 상태 버전**:
  - `job_id`는 전이마다 새로 만든다.
  - 결과는 `speech_id`에 붙인다. 결과가 도착했을 때 그 발언이 이미 폴백으로 확정됐다면 버린다(discarded).
  - 붙이는 데 판 상태의 잠금은 필요 없다. 발언 문장은 표시용이고, 규칙 상태에는 영향이 없다.
- **마감**: `deadline_ms`(기본 15초, 11/5 벤치마크 후 조정)를 넘기면 `timeout`이 된다. 렌더러는 실제로 생성을 취소해야 한다(llama.cpp 요청 중단).
- **취소**: 판이 다음 단계로 넘어가면 응용 계층이 그 판의 미완료 작업을 `cancel`한다. 화면에는 폴백이 남는다.
- **큐 우선순위**: 0 = 캐묻기(대화 중), 1 = 복기, 2 = 운영 분석이다. 같은 우선순위끼리는 먼저 온 순서대로 처리한다. 캐묻기 작업이 기다리고 있으면 우선순위 2 작업을 새로 시작하지 않는다.
- **폴백 동등성**: 폴백 문장은 `fact_sentence`를 그대로 포함한다. 따라서 생성 실패 여부와 관계없이 플레이어가 얻는 정보가 같다. 생성문이 사실 문장의 장소, 시간, 인물을 바꾸거나 빼면 E의 검증기가 `failed`로 처리한다.
- 작업 기록은 `render_job` 이벤트(`paldo.llm`)로 남긴다. 상태, 지연, 대기 시간, 모델·프롬프트·양자화 버전, 폴백 여부가 포함된다. 값은 `RenderResult`(`quantization`, `fallback` 필드는 contract-0.3에서 추가)로 응용에 오고, 기록은 응용이 `poll` 결과의 상태 변화와 단계 전환 때 `cancel`한 작업의 최종 상태를 `append_events`로 남긴다(11/4 PM 결정 #18).
- **전송**(11/4 PM 결정 #3): 실제 렌더러 `server/infra/llm_renderer.py`는 LLM 서비스 HTTP로 옮긴다. `POST /v1/renders`(`RenderRequest` JSON → 202), `GET /v1/renders/{job_id}`(`RenderResult` JSON), `POST /v1/renders/{job_id}/cancel`(→ 204). 서비스에 작업이 없으면(404) `failed`로 본다. 주소는 `PALDO_LLM_URL`([integration.md](integration.md) G7).
- **복기 해설**도 같은 포트·같은 폴링을 우선순위 1로 쓴다(11/4 PM 결정 #17).

## 5. 광고 포트 (`AdProvider`, 게임 경로 보호)
- 게임 화면은 광고 없이 먼저 그리고, 저장도 먼저 끝낸다. 광고 칸은 페이지가 그려진 뒤 htmx가 별도 요청(`GET /ads/slot/{slot}`, `hx-trigger="load"`)으로 채운다. 게임 명령 처리 경로에서는 광고를 부르지 않는다.
- 그 요청 안에서 `AdGateway.fetch(req)`를 호출한다. 규칙은 다음과 같다.

  | 항목 | 값(실측 후 조정) |
  |---|---|
  | 연결 시간 제한 | 100ms |
  | 전체 시간 제한 | 300ms |
  | 동시 호출 상한 | 8. 넘으면 기다리지 않고 `busy`로 빈 광고 |
  | 재시도 | 없음 |
  | 회로 차단 | 연속 실패 5회면 30초 동안 호출을 생략한다(`circuit_open`). 그 뒤 첫 호출로 회복을 시험한다 |

- 어떤 실패든 **빈 광고**(`filled=false`)를 돌려주고 예외를 올리지 않는다. 화면은 광고 칸을 비운다. 결과 코드(ok, empty, timeout, error, busy, circuit_open)는 지표로 남긴다.
- 가짜 `FakeAdProvider(latency_ms, fail, empty)`로 지연, 무응답, 실패를 주입해 시험한다(`tests/contract/test_ads.py::GamePathProtection`).
- 허용치(기획서 14.7): 광고 장애 중 게임 API p95 증가가 50ms 이하이고, 게임 오류율이 늘지 않아야 한다.
- 실제 `AdProvider` HTTP 구현은 `server/infra/ad_client.py`(B, B-08-03)다. 주소는 `PALDO_ADS_INTERNAL_URL`이고, 호출마다 `X-Request-ID`를 넘긴다(11/4 PM 결정 #6·#14).
- **비콘·클릭·랜딩 중계**(11/4 PM 결정 #2 변경, B-07-05): 브라우저는 광고 서버에 직접 접속하지 않는다. 게임 웹이 같은 포트로 넘긴다. 메서드 이름과 인자는 아래가 기준이고, B-07-05가 `server/app/ads_port.py`와 `FakeAdProvider`에 추가한다.

  | `AdProvider` 메서드 | 광고 서버 호출(ads.md 3절) | 실패 시 |
  |---|---|---|
  | `send_impression(serve_token, visible_ratio, visible_ms, occurred_at_ms, request_id, connect_timeout_ms, total_timeout_ms) -> bool` | POST `/ads/v1/impression` | `False`(비콘 손실로 처리, 재시도 없음) |
  | `send_click(serve_token, request_id, connect_timeout_ms, total_timeout_ms) -> str \| None` | GET `/ads/v1/click?t=`. 리다이렉트를 따라가지 않고 `Location`(랜딩 경로)만 돌려준다 | `None` → 화면은 `/`로 이동 |
  | `fetch_landing(creative_id, request_id, connect_timeout_ms, total_timeout_ms) -> str \| None` | GET `/ads/landing/<creative_id>`(HTML) | `None` → 대체 조각 |

- `AdGateway.report_impression`·`click`·`landing`이 위 메서드를 감싼다. serve와 같은 시간 제한(연결 100ms, 전체 300ms), 재시도 없음, 예외를 올리지 않음. 회로 차단기는 serve와 함께 쓰고(같은 서버의 상태), 동시 호출 상한 8은 중계용으로 따로 둔다(비콘이 serve 자리를 빼앗지 않게). 중계는 게임 명령 처리 경로 밖이다(웹소켓 Consumer와 `/ads/click`·`/ads/landing` 요청). 결과는 `paldo_ad_relay_total{kind,result}`.
- **비동기 경계**: 웹소켓 Consumer는 비동기(`AsyncJsonWebsocketConsumer`)이고 `AdGateway`는 동기다. Consumer는 이벤트 루프에서 `AdGateway`를 직접 부르지 않고 `asgiref.sync.sync_to_async(..., thread_sensitive=False)`로 스레드에서 실행한다(동기 호출이 Daphne 이벤트 루프를 막아 게임 HTTP 요청까지 멈추지 않게). 중계 동시 상한 8을 넘는 비콘은 기다리지 않고 `ack.relayed=false`로 끝낸다. `/ads/click`·`/ads/landing`은 동기 Django 뷰다.
- **전달 헤더**: 중계 요청은 헤더를 새로 만든다. 보내는 것은 `X-Request-Id`와 필요한 `Content-Type`뿐이고, 참가자의 `Cookie`·`Authorization`·`X-Forwarded-For`·`User-Agent`·`Referer`는 복사하지 않는다(ads.md 9절, 테스트로 확인).
- **회로 차단기가 열렸을 때**: 노출 비콘은 `ack.relayed=false`, 클릭은 302 `/`(게임 화면), 랜딩은 대체 조각이다. 광고 서버가 응답하지 못하는 동안 클릭 기록이 남지 않는 것은 비콘 손실과 같게 본다(ads.md 8절).
