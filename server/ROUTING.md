# 서버 라우팅

B-09-02 어댑터 수신: `composition._configure_parser` → 설정 `INTENT_PARSER_FACTORY` → `paldo_web.intent.make_bert_parser()` → D의 `intent.bert.BertIntentParser(run_dir)` → 기존 `ObservedIntentParser` → `InteractionService`다. 로컬 아티팩트 경로는 `INTENT_BERT_RUN_DIR`(환경 `PALDO_INTENT_BERT_RUN_DIR`)로 받는다. 기본 팩토리는 빈 문자열로 유지한다. 운영 URL·DTO 변경은 없으며 인수 범위와 실제 가중치 미검증은 [증빙](../docs/evidence/B-09-02.md)에 구분한다.

B-09-02 사전 부하: 기존 `tools/perf/api_bench.py --web-processes 2 --intent-every 1` → `tests/multiproc_bench.py` → 시험 전용 `asgi_bench_app.py` → `asgi_bench.py`·`intent_load.py`다. 시험 해석기는 `--intent-factory`로 고르고 기존 해석기 포트·계측을 사용한다. 공개 화면 버전으로 기존 `POST /games/<id>/utterances` 해석 후 같은 시도를 `cancel`하며 명령은 실행하지 않는다. 보조 기록은 시험 접두어의 Mongo `b_interactions`에 저장하고 종료 때 삭제한다. 운영 URL·DTO·화면·설정 기본값은 변경하지 않는다. 실제 엔진 부하 옵션은 D-08-01과 연결 대기다. [증빙](../docs/evidence/B-09-02.md)

B-10-07: 설정 PALDO_GAME_UI(vn 기본/classic) → paldo_web/ui_templates.py → meeting.html 또는 meeting_classic.html. meeting_parts/records.html은 공개 기록, meeting_actions.html·utterance_input.html은 기존 명령 전송을 유지한다. ui-stage.js는 폭 900px 기준 배치와 정보 창, ui-vn.js는 읽기·비교·확인을 맡는다. CSS 공통 토큰은 web/static/css/theme.css다.

공식 그림은 ui_assets.py → assets.asset_url → manifest. 여성 전신·초상은 같은 ID의 v5 파일이다. GET /ui-review는 로그인한 vn에서 manifest의 정식 그림(현재 로고·회의/밤 배경 포함 21개)과 임시 자원 상태를 표시하고 classic은 404다. 수량은 manifest에서 계산한다. 시안 후보 카탈로그는 읽지 않는다. 보존한 전체 HTML 시안과 실제 화면의 적용 범위는 web/UI_APPLY.md에서 구분한다.

종료 탭은 같은 판 URL에서 결과·복기·단서를 표시한다. GET review의 htmx는 review_panel.html, 일반 HTML은 review.html, JSON DTO·소유권·진행 중 403은 유지한다. 서버·웹 디자인 및 web/PM_UI_REVIEW.md·UI_ASSET_PLAN.md·UI_APPLY.md를 함께 갱신한다.

B-10-02: 기존 GET /games/{game_id} 전체/htmx 화면 → meeting.html → village_guide → api/village_assets.py → manifest 지도·regions·초상. 하단 asset_attribution → SOURCES.md 첫 출처 줄. 이미지는 기존 api/assets.py가 제공한다. 새 URL·명령·DB는 없다.

B-09-06은 [VN 표시 규칙](../docs/contracts/vn.md)을 정의하며 URL·HTTP 메서드·서버 호출 경로를 추가하지 않는다. 회의·밤의 기존 화면 및 render 폴링을 B-10-07에서 연결한다. 11/9 PR #304로 확정됐다.

B-08-02: 홈 `accounts/views.py`와 일반 판 시작 `app/open_start.py`의 `open_preset_ids()`가 `app/presets_enabled.json`을 매번 읽는다. 초기 공개는 `base6`만이며, 파일에서 허용한 프리셋만 새 일반 판을 시작한다. 회차 판 배정과 이미 시작한 판의 재전송은 기존 정책을 유지한다. `POST /games`의 `mudang_variant` → 판 메타 → `GameOutput.mudang_variant` → `meeting.html` 초상으로 이어진다.

공동 확인은 현재 dev 빌드의 기존 홈·시작·명령·판 조회 경로로 6인 1판을 완주하고 E가 재확인한다. 후보 태그가 없어도 진행 가능한 B-08-02 조건이며, 11/10 테스트 서버 태그 리허설은 B-09-03에서 확인한다(PR #272 일정 변경).

명령 거절은 `api/views.py` → `RequestTelemetryMiddleware`의 HTTP 접근 로그 `reject` 필드에 `RejectCode.value`만 추가한다(422). 수락·동일 수락 요청 재생에는 없으며 요청 본문은 기록하지 않는다. `api/meeting_view.py` → `meeting_actions.html` → `game.js`가 지난 밤·진술 두 개 선택을 안내·제한하며 최종 판정은 기존 엔진이다. 시험 경로는 `tests/record_large8.py` → 공개 8인 녹화 → `test_preset_screens.py`·`preset_render_bench.py`다. [B-08-02 증빙](../docs/evidence/B-08-02.md)

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 계측의 요청 격리·SQL 원문 비노출 시험은 `server/tests/test_bench_metrics.py`다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

최신 dev의 성능 CLI는 `--proxy-profile`이 있으면 C의 `server/tests/proxy_bench.py`로 보내고, 나머지는 기존 `server/tests/api_bench.py`로 보낸다. B의 `--compare-asgi` 분기는 그대로 유지한다. C의 완료된 판 단일 GET·프록시 비교는 B의 동시 시작·명령·광고 장애 시험과 별개다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

B-07-05 성능 시험은 `tools/perf/api_bench.py --compare-asgi` → `server/tests/api_bench.py` → `asgi_bench.py` → 시험 전용 `asgi_bench_app.py`·`asgi_bench_settings.py` 순서다. 하위 프로세스의 WSGI 또는 실제 Daphne에 루프백 HTTP로 기존 `POST /games`·명령을 보내며, 혼합 부하에서는 `GET /ads/slot/lobby_banner`와 인증 `/ws/game/` 비콘을 함께 보낸다. 운영 URL·조립은 바꾸지 않는다.

B-06-04 비노출 검증: 가입·로그인 → 기존 `/games`·명령 → `GET /ads/slot/{slot}` → `api/ad_slots.py` → `ensure_ad_identity` → `AdGateway.fetch` → `HTTPAdProvider` → D Django 광고 API다. `test_accounts.py`의 같은 인증 시나리오를 `test_account_http_privacy.py`가 재사용하고 `ad_privacy_host.py`의 별도 루프백 D WSGI 프로세스가 수신 본문·헤더·D 로그를 검사한다. 광고 저장소만 메모리인 시험 전용 호스트이며 운영 조립·URL 변경은 없다.

B-06-02 시험 진입점은 `tools/perf/api_bench.py` → `server/tests/api_bench.py` → 기존 `/games`·`/games/{id}/commands` → 실제 Mongo 조립이다. URL·운영 핸들러는 바꾸지 않는다. `server/tests/mongo_commands.py`는 같은 API의 실제 저장·20개 경합을 검사한다.

운영 팩토리는 `server.infra.game_factory.build_game_service` → `paldo_web/mongo.py` 실행 자원 → `infra/interaction_store.py` 보조 저장을 연결한다. B-09-01: 판 시작 모델 버전 고정·카나리아 규칙은 응용 `app/model_versions.py`, 레지스트리 조회는 인프라 `infra/model_registry.py`(C-09-01 전 임시 `StaticModelRegistry`), 폴백 지표는 `paldo_web/engine_metrics.py`, 업데이트 내역 패널은 `game.html`의 `update_history.html`(태그 `update_history`). B-06-02 시험 조립과 분리되며 설정·실행 순서는 [웹 모듈](../docs/modules/web.md#사용법)에 있다.

`POST /admin/sessions` → `app/test_sessions.py`는 최신 엔진의 합성 규칙 버전(`rule-0.7.decision-1`)을 회차와 판 메타에 그대로 전달한다. `tests/test_sessions.py`가 등록·시작과 잘못된 버전 거부를 확인한다.

dev `f24f6a8` 반영 후에도 이 경로와 API 계약은 같다. 재검증 결과는 [B-06-02 증빙](../docs/evidence/B-06-02.md)에 둔다.

`GET /games/{id}/renders/{job_id}`의 htmx 응답은 공개 추궁 답변의 발언 ID·작업 ID를 대조해 `speech_line.html`로 렌더한다. 11/8 PM 결정에 따라 최초·폴링·완료·취소 재조회에 Kanana 표기를 붙이지 않는다. JSON 응답 DTO와 폴링 종료 조건은 유지한다.

요청은 [paldo_web/urls.py](paldo_web/urls.py)에서 계정·관리자, 게임 API, 상태 확인과 정적 파일 경로로 나뉜다.

| 요청 | 처리 경로 |
|---|---|
| `GET /` | [accounts/views.py](accounts/views.py)의 계정 메뉴. 자기 가명 ID, 동의한 사용자의 "새 게임"(일반 판 시작 폼, 폼마다 새 `start_token`)과 진행 중인 일반 판 이어 하기, 배정된 회차의 다음 고정 시드 판을 표시한다. |
| `GET·POST /accounts/signup`, `GET·POST /accounts/login`, `POST /accounts/logout` | [accounts/urls.py](accounts/urls.py) → [accounts/views.py](accounts/views.py). 가입은 가명과 동의를 저장하고, 로그인과 로그아웃은 Django 세션을 관리한다. |
| `GET·POST /admin/login/`, `/admin/` | 로그인은 일반 계정과 같은 [잠금 규칙](accounts/lockout.py)을 통과한다. 관리자 화면은 Django admin이 제공한다. |
| `GET·POST /admin/sessions` | [api/sessions.py](api/sessions.py) → [app/test_sessions.py](app/test_sessions.py) → [infra/test_sessions.py](infra/test_sessions.py). 운영자가 고정 회차를 조회하고 등록한다. |
| `POST /games` | [api/views.py](api/views.py) → [api/consent.py](api/consent.py)에서 읽은 가명·동의로 [판 시작](app/usecases.py)을 요청한다. 시작 정책 [app/open_start.py](app/open_start.py)가 `session_id`가 없으면 일반 판(공개 프리셋·서버 랜덤 시드·배포 버전), 있으면 회차 배정 정책([app/test_sessions.py](app/test_sessions.py))으로 나눈다(E-08-14). 기본 조립(`paldo_web/composition.py`)과 운영 Mongo 조립(`paldo_web/mongo.py`)이 같은 정책을 끼운다. `tests/test_open_start.py`가 확인한다. htmx는 입력 형식에 관계없이 200 회의 조각을 받고, 일반 HTML 폼은 302로 이동한다. |
| `/games/{id}/…`, `/players/me/progress` | [api/urls.py](api/urls.py) → [api/views.py](api/views.py)·[api/interactions.py](api/interactions.py) → [app/](app/). |
| `POST /games/{id}/utterances`, `POST /games/{id}/utterances/{attempt_id}` | [api/interaction_forms.py](api/interaction_forms.py)가 폼을 공통 DTO로 변환한다. [api/interactions.py](api/interactions.py) → [app/interactions.py](app/interactions.py). [paldo_web/composition.py](paldo_web/composition.py)가 선택한 해석기를 조립할 때 주입한다. JSON은 DTO, htmx는 카드 또는 현재 회의 조각이다. |
| `GET /games/{id}/renders/{job_id}` | [app/interactions.py](app/interactions.py)의 소유권·완료 상태 검사 뒤 JSON 또는 `speech_line.html`을 반환한다. `speech_line.html`은 발언만 갱신하고 완료하면 폴링을 멈춘다. 회의 진술표는 [api/meeting_view.py](api/meeting_view.py)가 공개 주장으로 만든다. |
| `PUT /games/{id}/notebook` | [api/participant_forms.py](api/participant_forms.py)가 JSON·폼을 `SaveNotebookInput`으로 검증한다. `InteractionService.notebook`이 의심도·투표 확신도를 저장하고 204를 반환한다. 동일 값은 같은 날짜·단계에서만 생략하며 다음 날·결선은 별도 기록한다. |
| `POST /games/{id}/survey` | `SubmitSurveyInput`의 응답·전체 건너뛰기를 검증하고 종료 후 저장한다. 최초 JSON은 204, 최초 htmx는 200 `survey_done.html`을 반환한다. 같은 요청은 204이며 htmx는 화면을 새로 읽는다. 다른 내용은 409, 잘못된 입력 조합은 400이다. |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | [paldo_web/probes.py](paldo_web/probes.py). `paldo_engine_suspicion_fallback_total`은 [paldo_web/engine_metrics.py](paldo_web/engine_metrics.py)가 저장된 엔진 이벤트로 올린다(B-09-01). |
| `GET /static/…` | [api/assets.py](api/assets.py). |
| `/ws/game/` | [paldo_web/asgi.py](paldo_web/asgi.py)의 출처·세션 인증 → [api/routing.py](api/routing.py) → [api/consumers.py](api/consumers.py). `hello`의 폐기 사유는 [api/ad_metrics.py](api/ad_metrics.py)에 집계하고, `ad.impression`은 [app/ad_events.py](app/ad_events.py)로 중계한다. |
| `GET /ads/click?t=…`, `GET /ads/landing/{creative_id}` | [api/ad_relay.py](api/ad_relay.py) → [app/ad_events.py](app/ad_events.py) → [app/ads_port.py](app/ads_port.py). 클릭·랜딩 실패는 홈으로 보내지 않고 게임 조회 없는 안내와 돌아가기를 같은 응답으로 보인다(B-08-03 E 보완). 랜딩 ID는 합성 소재의 `syn:`을 받는다. 중계 로그에 `request_id`와 `serve_id`(토큰 본문에서 읽음, 서명 검증은 광고 서버)를 남긴다. |
| `GET /ads/slot/{slot}` | [api/ad_slots.py](api/ad_slots.py) → B-07-05의 광고 게이트웨이 조립 → `AdGateway.fetch`. 게임 명령과 분리해 광고 HTML 조각만 반환한다. 미제공·실패는 200 빈 조각이다. |

B-07-02의 수첩·설문 쓰기와 `GET /games/{id}`의 공개 화면 복원은 `InteractionService.participant_records`를 거친다. 저장 계획은 보조 저장의 `effects`, 공개 표시 값은 `state`에 보존하고 C 게임 아웃박스의 동일 키 재생으로 복구한다. C의 별도 `outbox_publisher`가 `paldo.player`로 발행한다. API·이벤트 형식은 그대로다.

실제 검증은 `tests/mongo_participant.py` → 기존 HTTP → B 응용·실제 Mongo → C 발행기 → TLS Kafka 수신이다. `tests/participant_worker.py`는 새 프로세스 재시작·경합, `tests/kafka_local.py`는 로컬 격리 TLS 브로커 준비 전용이다. 운영 진입점은 이 시험 모듈을 사용하지 않는다.

- [전체 API 계약](../docs/contracts/api.md)

자유 입력의 직전 상대는 응용이 공개 캐묻기 발언에서 보완한다. 재입력 경로도 시도에 보존된 선택 인물·근거·직전 상대를 전달하며, 확인 요청은 선택한 카드의 누락 슬롯과 근거 모호성만 검사한다.
- [서버 설계](DESIGN.md) · [서버 시험](tests/README.md) · [웹 화면 라우팅](../web/ROUTING.md)

E-09-10 다중 프로세스: Caddy(`ops/proxy/Caddyfile`, upstream 8000·8002) → Daphne 2개(`ops/mysql/service_runtime.py web --port`) → 같은 URL 경로. 채널 레이어 설정은 `paldo_web/channel_layers.py`, 동시성 구조와 한계는 [서버 설계](DESIGN.md) "동시성 구조와 한계", 비교 부하는 `tests/multiproc_bench.py`.

## B-06-03 실제 Mongo 연결

명령 저장은 `GameService.command` → B의 `WebMongoGameRepository.commit` → C의 `MongoGameRepository.commit` 순서다. Mongo 연결 오류를 B에서 정규화한 뒤 응용이 같은 commit을 한 번 재시도하고, 이미 저장됐다면 CAS 충돌 → 명령 조회 → 저장 응답 재생으로 끝난다.

호스트 시작 조립 → `paldo_web/mongo.py` → C의 `infra/mongo_repo.py`·`progress_store.py`·`content_store.py`와 B의 `infra/test_sessions.py`. `configure()` 뒤 기존 `/admin/sessions`·`POST /games`·`GET /games/{id}`·명령·진척 경로가 같은 실행 자원을 쓴다. URL·요청/응답 DTO는 바꾸지 않는다. 고정 회차 메타와 콘텐츠 버전을 재조회하며 Django 계정·세션은 기존 default DB를 유지한다.

B-06-03의 `/admin/sessions` → 고정 회차 등록 → 홈의 배정/이어 하기 → `POST /games`·`GET /games/{id}` 경로는 그대로다. 최신 dev에서 회차·소유자 가명·동의·광고 대응표·재접속 보존을 재검증했다. [증빙](../docs/evidence/B-06-03.md).

## B-08-01 결과·진척·결말 선택

`GET /players/me/progress`는 기존 JSON DTO를 유지하고 HTML 요청에는 로비용 `progress.html`, htmx 요청(판 종료 화면 단서 탭)에는 `progress_panel.html` 조각을 반환한다. `GET /games/{id}/review`도 htmx 요청이면 종료 뒤 `review_panel.html`(B-10-01 복기 탭)을, 그 밖에는 `ReviewOutput` JSON을 반환한다. 복기를 처음 열 때 응용 `GameService._ensure_narration`이 해설 작업을 멱등 등록하고(E-10-01, 기능 꺼짐이면 생략), `GET /games/{id}/review/narration`은 htmx면 `review_narration.html` 폴링 조각, 그 밖에는 `ReviewNarrationOutput` JSON이다. 계층: 표현 `server/api/views.py`·`story_view.narration_context` → 응용 `server/app/review_narration.py`(등록·조회·처리, 포트 `Narrator`·`ReviewJobStore`) → 도메인 `llm/review_narration.py`. 인프라: 생성 서버 HTTP `server/infra/kanana_narrator.py`, 저장 `server/infra/review_jobs.py`(`<접두어>review_jobs`), 조립 `server/infra/game_factory.py`(`attach_narration`), 작업 프로세스 `manage.py review_worker`, 실측 `manage.py review_narration_perf`. 설정 `PALDO_REVIEW_LLM_URL`·`PALDO_REVIEW_DEADLINE_S`. `story_view.py`는 본인 진척의 수집 단서만 문안과 연결한다. 홈의 일반 판·고정 회차 폼은 `ending_choice.html`에서 열린 결말을 고르고 `POST /games`의 폼·JSON을 같은 `StartGameInput`으로 처리한다. 일반 판의 `start_token`·초상·공개 프리셋 경로는 유지한다. 종료된 `GET /games/{id}`·명령 응답은 기존 회의 조각에 `game_end.html`(결과 `game_result.html`·복기·단서 탭)을 포함한다. 공개 결과는 `PlayerView`, 선택 결말·콘텐츠 버전은 소유권 확인한 메타에서 읽는다.

B-08-01 버전 문안 경로: 기존 결과·진척 요청 → `server/api/story_view.py` → `server/story_catalog.json`의 해당 버전. 콘텐츠 갱신 뒤 전체 화면·htmx·반복 조회도 같은 결말 문안과 수집 단서 원문을 표시한다. URL·JSON DTO는 유지한다.

## B-08-03 HTTP 광고 제공자

기존 GET /ads/slot/{slot}·웹소켓 ad.impression·GET /ads/click·GET /ads/landing/{creative_id} → 기존 광고 응용/AdGateway → paldo_web/composition.py의 환경 기반 조립 → infra/ad_client.py → 내부 광고 HTTP 4경로다. URL·DTO·지면의 빈 응답 규칙은 바꾸지 않는다. 클릭 중계의 Location은 가상 랜딩 상대 경로이며 내부 광고 출처를 브라우저에 내보내지 않는다.

시험 진입점은 tools/perf/ad_fault_test.py → tests/ad_fault_bench.py → tests/asgi_bench.py·asgi_bench_app.py다. HTTP 대역은 tests/ad_http_host.py이며 운영 요청과 분리한다. [증빙](../docs/evidence/B-08-03.md).

광고 클릭 실패 → 홈의 accounts/views.py는 진척 1회·판별 최대 1회 조회를 요청 안에서 재사용한다. 다음 요청은 다시 조회하며 홈의 기존 회차·일반 판·결말 선택 표시를 유지한다.

## B-10-08 계정 디자인·시험 계측

계정 GET/POST·홈·게임 시작 URL과 시작 정책은 유지한다. `accounts/views.py`는 배정된 미완료 판 여부를 `test_round_pending`으로 전달하고 계정의 `account-start.js`가 일반/테스트 영역을 하나씩 펼친다. `accounts/base.html`이 판 시작 전 로고와 계정 전용 CSS를 연결하고 게임 기본 템플릿은 로고를 연결하지 않는다. 배경은 `api/assets.py`의 기존 `asset_url`로 공식 manifest의 정식 night를 읽는다. 자원은 기존 공식 manifest·`web/static/` 허용 경로로 제공한다.

`tests/ui_theme_preview.py --font-probe`는 `tests/ui_font_probe.py`를 시험 미들웨어에만 주입한다. 로그인 GET에만 글꼴 Timing 표본을 추가하며 운영 설정·URL은 바꾸지 않는다. 기록 도구는 `tools/perf/account_assets.py`다. [웹 라우팅](../web/ROUTING.md#b-10-08-계정-디자인로고글꼴)


## 확정 표정 에셋 교체 (2026-11-10)

A-10-06의 기쁨·당황 16종 전신과 같은 수의 파생 초상을 기존 manifest ID·경로에 등록했다. 기본 인물 파일·API·공개 사건 선택 로직은 변경하지 않았다. 복구한 출처 기록과 제출 상태는 docs/evidence/A-10-06.md를 따른다.

A-11-04의 슬픔 8종 전신과 같은 수의 파생 초상을 기존 manifest ID·경로에 등록했다. 기본 인물 파일·API·공개 사건 선택 로직은 변경하지 않았다. 복구한 출처 기록과 제출 상태는 docs/evidence/A-11-04.md를 따른다.

### 공개 사건 표정 자동 연결 (B-10-07, 2026-11-10)

A-10-06·A-11-04의 확정 그림을 공개 사건에 연결했다. 앞 절의 에셋 배치 검수와 별도로, 현재 대사·사건 ID마다 표정을 계산한다. 감싸진 대상은 다음 자기 대사에 기쁨, 의심·모순 지적·공개된 탁한 점괘의 대상은 다음 자기 대사에 당황, 희생자 발표 뒤 첫 대사·고백·감금 당사자는 슬픔이다. 다음 대사에서 기본으로 돌아오며, 겹치면 슬픔 > 당황 > 기쁨이다. 승인된 예외로 투표 생존 기쁨과 아침 첫 대사 슬픔이 겹친 한 줄은 기쁨이다.

표정 선택은 PlayerView의 공개 기록만 받고 역할·진위·현재 생존 상태·개인 점괘는 입력하지 않는다. 기색은 공개 기록의 shown=True인 주장에만 문구와 당황을 붙인다. 표정 파일이 미등록·임시·유실이면 같은 인물의 기본 그림으로 대체하고 남/여 무당 선택을 유지한다. API·명령·DTO·엔진·이미지 파일·계약·기획·결정 문서는 바꾸지 않았다. 에셋 규격표 7절은 B-10-07 1-3에 이미 승인된 11/9 규칙으로 동기화했다.

[표정 연결 검수·한계](../docs/evidence/B-10-07.md#공개-사건-표정-연결-2026-11-10). 실운영 DB·VM 판정이나 B-10-07 전체 완료를 뜻하지 않는다.

| 경로 | 책임·호출 |
|---|---|
| `meeting_view.meeting_context` | 기존 `/games/{id}` HTML·htmx 응답의 표시 줄과 공개 기록 → `vn_expressions.expression_cues` |
| `vn_expressions.py` | 표시 순서로 한 줄 표정·공개 기색 ID 계산. 도메인/DB/별도 HTTP 호출 없음 |
| `vn_script.reaction_target` | 공개 대상·근거 진술에서 모순 지적/의심 대상 선택, 기존 즉시 반응과 공유 |
| `ui_assets.vn_sprite_url` → 템플릿 `vn_sprite` | 공식 manifest 조회, 실제 표정 없으면 기본 sprite, 무당 선택 보존 |

### B-11-01 캐릭터·도움말 표시 보완 (2026-11-11)

기존 게임 조회 → 회의 템플릿 → `web/static/game.css` 경로를 그대로 쓴다. 캐릭터 hover/active 투명 배경과 모바일 도움말 배치만 보완한다. 서버 URL·API·DTO·명령 처리·응답은 변경하지 않았다. [검증](../docs/evidence/B-11-01.md).
