# B 웹 영역 안내

B-10-02: server/api/village_assets.py는 manifest 고정 지도·공개 주민 초상·출처 요약을 제공한다. template_tags.py의 포함 태그 → web/templates/village_assets.html·asset_credits.html → meeting.html이다. 게임 규칙·API·이미지 파일은 변경하지 않는다. [증빙](../evidence/B-10-02.md).

B-09-06 산출물은 [VN 연출 규칙과 UI 적용 범위](../contracts/vn.md)다. 기존 기준·B 제안·E 검토를 구분하며 운영 경로는 바꾸지 않는다. 화면 구현은 B-10-02·B-10-01·B-10-07의 경계를 따른다.

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 계측의 요청 격리·SQL 원문 비노출 시험은 `server/tests/test_bench_metrics.py`다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 게임 웹(Django 뷰·화면·웹소켓)과 응용 유스케이스, 게임 쪽 광고 포트.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/web.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/B_web.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 요청과 파일 연결은 [서버 라우팅](../../server/ROUTING.md)과 [웹 화면 라우팅](../../web/ROUTING.md)에서 본다.
- 이 문서는 B 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, B 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `server/app/` | 응용 | B | 유스케이스와 DTO(`dto.py`) |
| `server/api/`, `web/` | 표현 | B | Django 뷰, 템플릿, htmx 조각, 뷰 모델(`viewmodels.py`), 광고 웹소켓 Consumer(`consumers.py`) |
| `web/templates/`, `web/static/` | 표현 | B | 회의 화면과 htmx 조각, CSS·스크립트·기본 이미지 |
| `server/accounts/` | Django 인증 | B | 계정 폼·가명 프로필·로그인 잠금·운영 명령 |
| `web/templates/accounts/`, `web/templates/admin/` | 표현 | B | 계정 화면과 가명으로 표시하는 관리자 화면 |
| `server/paldo_web/` | Django 프로젝트 | B | 서버 설정, URL 등록, 요청 계측, DB 상태 확인과 Prometheus 지표 |
| `server/tests/` | 테스트 | B | 요청 계측·상태 확인·DB 장애·게임 API·명령 경합 시험 |
| `server/app/ads_port.py` | 응용(게임 쪽) | B | `AdProvider` 포트와 보호 래퍼 `AdGateway` |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 공개 프리셋·무당 초상·8인 화면 | `server/app/presets_enabled.json` → `open_start.py`·`server/accounts/views.py` → `web/templates/accounts/portrait_choice.html`·`meeting.html`. 공개 녹화 생성 `server/tests/record_large8.py`, HTTP 화면 회귀 `test_preset_screens.py`, 렌더 측정 `preset_render_bench.py` |
| 화면이나 API 추가 | 표현 `server/api/`, 응용 `server/app/dto.py`, `docs/contracts/api.md` |
| 게임 JSON API 경로와 실행 흐름 | `server/api/urls.py`, `server/api/views.py`, `server/api/interactions.py` → `server/app/usecases.py`, `server/app/interactions.py` |
| 회의 화면과 에셋 연결 | `server/api/views.py`, `server/api/meeting_view.py`, `server/api/assets.py`, [화면 라우팅](../../web/ROUTING.md) |
| 자유 입력 카드·추궁 답변 폴링 | `server/api/interactions.py`, `interaction_forms.py`, `interaction_view.py` → `server/app/interactions.py`; `web/templates/speech_cards.html`, `speech_line.html` |
| 수첩·투표 확신도·판 종료 설문 | `server/api/participant_forms.py`, `server/api/interactions.py`, `web/templates/notebook.html`, `survey.html`, `survey_done.html`. B `participant_records`의 저장 계획 복구 → C 아웃박스 → `paldo.player`. 실제 시험은 `server/tests/mongo_participant.py`, 새 프로세스는 `participant_worker.py`, TLS 로컬 브로커는 `kafka_local.py` |
| 명령 재전송·동시 요청·실제 Mongo 부하 | `tools/perf/api_bench.py` → `server/tests/api_bench.py`. 실제 경합은 `server/tests/mongo_commands.py`. [서버 시험 안내](../../server/tests/README.md), 결과 `docs/evidence/perf/B-06-02/` |
| WSGI·Daphne 및 광고·비콘 혼합 성능 | 같은 CLI의 `--compare-asgi` → `server/tests/asgi_bench.py` → 하위 프로세스 `asgi_bench_app.py`·`asgi_bench_settings.py`. 판정 회귀 `test_asgi_bench.py`, 결과 `docs/evidence/perf/B-07-05/`. 대상 VM 검증은 별도 |
| 가입·로그인·로그아웃과 계정 복구 | `server/accounts/urls.py`, `server/accounts/views.py`, `web/templates/accounts/`, [웹 모듈 사용법](../modules/web.md#사용법) |
| Mongo 게임·진척·콘텐츠·회차 조립 | `server/paldo_web/mongo.py` → C 저장소 3종·B 회차 저장소. B의 `WebMongoGameRepository`가 commit 연결 오류를 정규화해 응용의 1회 재시도·CAS 재생으로 연결. 실제 시험은 `server/tests/mongo_game_sessions.py` |
| 테스트 회차 등록과 고정 판 배정 | `server/api/sessions.py`, `server/app/test_sessions.py`, `server/infra/test_sessions.py`, [서버 라우팅](../../server/ROUTING.md) |
| 광고 비콘·클릭·랜딩 중계 | ASGI `server/paldo_web/asgi.py` → 표현 `server/api/consumers.py`·`ad_relay.py` → 응용 `server/app/ad_events.py` → 포트 `server/app/ads_port.py`. 실제 HTTP 인프라 `server/infra/ad_client.py`는 PALDO_ADS_INTERNAL_URL로 연결(B-08-03, D 인수·실제 배치 검증 대기) |
| 웹소켓 재연결·비콘 폐기 사유 | `web/static/js/paldo_ws.js` → `server/api/consumers.py` → `server/api/ad_metrics.py`. 클라이언트·Consumer·실제 Daphne 검증은 [B-07-05 증빙](../evidence/B-07-05.md) |
| 광고 지면을 게임 화면에 붙이기 | `server/api/ad_slots.py`·`ad_slot_tags.py`, `web/templates/ad_slot.html·ad_placeholder.html`, `web/static/js/ad_slots.js` → `server/app/ads_port.py`. 웹소켓 중계는 병합된 B-07-05 조립 사용 |
| 결과·진척·결말 선택 화면 | `server/api/story_view.py` → 버전별 공개 문안 `server/story_catalog.json`, `server/api/views.py`, 판 종료 탭 `web/templates/game_end.html`(결과 `game_result.html`, 복기 `review_panel.html`, 단서 `progress_panel.html`)·`web/static/js/game_end.js`·`web/static/css/result.css`, 로비 `progress.html`·`ending_choice.html`, 선택형 도전 목록 `story_view.preset_choices`·`accounts/home.html` |

B-08-03 시험: tools/perf/ad_fault_test.py → server/tests/ad_fault_bench.py → 기존 실제 Daphne·api_bench 게임 명령 부하. 로컬 대역과 D 인수 서버 결과를 구분하며 실제 배치 조건은 보류한다.
