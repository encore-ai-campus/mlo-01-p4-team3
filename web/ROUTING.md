# 웹 화면 라우팅

B-10-02: 기존 게임 전체/htmx 응답의 meeting.html → village_assets.html(고정 지도·전체 초상 펼치기)·asset_credits.html(하단 출처). 좌표·URL은 manifest, 출처는 SOURCES.md에서 읽는다. 펼치기는 HTML details로 처리하며 서버 명령을 보내지 않는다. 새 URL·API는 없다.

B-09-06의 [VN 규칙](../docs/contracts/vn.md)은 기존 회의·밤 경로의 후속 표시 설계다. 새 URL·명령·서버 읽기 저장 경로를 추가하지 않는다. 실제 화면 연결은 B-10-07에서 진행하며 현재는 E 검토 대기다.

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

B-07-05 네트워크 부하는 화면 조작 대신 실제 HTTP 게임 API·로비 광고 조각·인증 광고 웹소켓을 호출한다. 광고 지면·비콘의 기존 경로와 응답을 유지하며 UI 템플릿·브라우저 스크립트는 변경하지 않는다. 실행 경로와 조건은 [서버 시험 안내](../server/tests/README.md#b-07-05-실제-http웹소켓-성능-비교)를 참조한다.

B-06-04의 가입·로그인·로비·판 종료 URL은 유지한다. 광고 지면 GET은 서버에서 광고용 UUID를 붙이며 로그인 ID·계정 가명·판 ID를 광고 포트로 옮기지 않는지 계정 시나리오 시험으로 확인한다. 실제 HTTP 광고 서버 연결은 B-08-03 검증 범위다.

B-06-02의 실제 Mongo 검증은 기존 `POST /games`·`POST /games/{id}/commands`를 사용한다. 새 화면·URL은 추가하지 않는다. 측정 도구와 범위는 [서버 시험 안내](../server/tests/README.md#b-06-02-로컬-시험)와 [성능 모듈](../docs/modules/perf.md)에 둔다.

dev `f24f6a8`의 엔진·에셋 갱신을 반영한 재검증도 같은 경로를 사용한다. 브라우저 화면 성능은 이 JSON API 측정에 포함하지 않는다.

11/8 PM 결정(B-08-02)에 따라 회의 최초·재조회 화면(`meeting.html`)과 추궁 답변 폴링 조각(`speech_line.html`)에서 Kanana 표기를 제거했다. 완료·실패·취소 뒤 재조회에도 표기가 없고, 폴링의 `outerHTML` 교체·종료 조건은 유지한다.

홈 시작 폼 → `accounts/portrait_choice.html`은 여성·남성 무당 초상을 라디오 버튼으로 선택한다. `mudang_variant`를 기존 시작 API로 보내며 `meeting.html`은 응답 값으로 무당 초상을 표시한다. 오래된 녹화 판의 값 없음은 여성 기본 초상이다. 선택지는 서버의 `presets_enabled.json`을 따르며 초기에는 `base6`만 보인다. 8인 녹화 화면은 시험에서만 임시 설정으로 공개한다.

B-08-02 공동 확인은 현재 dev 빌드의 홈 → 6인 게임 시작 → 진행 → 종료를 함께 확인하고 E가 재확인한다. 후보 태그를 선행 조건으로 두지 않으며, 11/10 테스트 서버 리허설 화면 확인은 B-09-03이다.

B-09-07: 자유 입력 카드(`speech_cards.html`)는 응용 계층(`server/app/interactions.py`)이 현재 단계 합법 명령으로 거른 카드만 받는다(0장이면 `unsupported_intent`와 "지금 할 수 있는 행동"). 아침·밤에는 `utterance_input.html` 입력 칸이 비활성이다. 심문 양식의 진술 체크박스는 `data-night`·`data-slot`을 가지며 `game.js`가 행적 질문의 밤·시간 칸을 맞춘다. 같은 날·단계에 다시 그린 양식은 직전 대상·질문 종류를 고른다(`views._previous_interrogation`). 회의 화면은 `meeting_actions.html`·`utterance_input.html`(발언 카드 자리 포함)을 `.command-bar`(화면 아래 sticky, 높이 50vh 이하, 안쪽 스크롤)로 감싼다. B-10-07 무대 안 선택지 패널이 이 막대를 대체한다.

`meeting_actions.html`의 밤 선택은 0~오늘−1이며, `game.js`는 모순 질문에 관련 진술 두 개를 고르기 전 제출을 막는다. 두 개 미만의 선택 가능한 진술만 있으면 해당 질문 선택지도 비활성화한다. 정상 질문은 같은 명령 경로와 `#meeting` 교체를 사용한다.

자유 입력에서 인물을 생략하면 서버가 공개 기록의 직전 캐묻기 상대를 전달한다. 재입력은 기존 선택 문맥을 유지한다. 다중 카드의 확인 버튼은 카드마다 활성 여부를 판단한다.

| 요청 | 화면 또는 파일 | 동작 |
|---|---|---|
| `GET /` | `accounts/home.html` | 가입·로그인 메뉴를 제공한다. 로그인 뒤 자기 가명 ID와 로그아웃 폼, 배정된 다음 판을 표시한다. |
| `GET·POST /accounts/signup` | `accounts/signup.html` | 가입 폼과 동의를 표시한다. 가입에 성공하면 계정 메뉴로 이동한다. |
| `GET·POST /accounts/login`, `GET·POST /admin/login/` | `accounts/login.html` | 두 경로가 같은 폼과 로그인 잠금 규칙을 쓴다. |
| `POST /accounts/logout` | `accounts/login.html` | 세션을 폐기하고 로그인 화면으로 이동한다. |
| `GET /admin/` | `admin/base_site.html` | 관리자 화면에 로그인 ID 대신 가명 또는 운영자 표시를 사용한다. |
| `GET·POST /admin/sessions` | `test_sessions.html` | 운영자가 회차와 고정 시드 목록을 등록하고 조회한다. |
| `POST /games` | `accounts/home.html`의 시작 폼 → `GET /games/{id}` 또는 `meeting.html` | 일반 HTML 폼은 302로 회의 화면으로 이동한다. htmx는 폼·JSON 입력 모두 200 회의 조각을 바로 받는다. JSON 응답 요청은 게임 DTO를 받는다. |
| `GET /games/{id}` | `game.html` → `meeting.html` | 전체 화면 또는 회의 조각에 진술표·공개 투표·감금 결과·본인 점괘를 표시한다. JSON 요청은 DTO를 받는다. |
| `POST /games/{id}/commands` | `meeting_actions.html` → `meeting.html` | 대상별 투표·결선·점치기 버튼과 낮의 점괘 공개 폼이 명령을 보내면 htmx가 `#meeting`을 교체한다. |
| `POST /games/{id}/utterances` | `utterance_input.html` → `speech_cards.html` | 공개 인물·근거와 자유 입력을 보내 카드 0~N장을 표시한다. 모호한 근거는 후보 주장 중 두 개를 선택해 수정할 수 있다. 빈 입력은 204로 현재 화면을 유지한다. |
| `POST /games/{id}/utterances/{attempt_id}` | `speech_cards.html` → 카드 또는 `meeting.html` | 확인·수정은 한 명령을 실행한다. 재입력·취소·버튼 전환·해당 없음은 소비하지 않는다. 버튼 전환은 `reaction=switch_to_buttons`를 전송해 시도를 닫는다. 오래된 카드 실행은 409 현재 회의로 교체한다. |
| `GET /games/{id}/renders/{job_id}` | `speech_line.html` | 1초 간격으로 발언 영역의 해당 문장만 교체한다. 완료되거나 단계가 바뀌면 폴링을 멈춘다. 진술표는 `meeting.html`에서 공개 주장으로 표시한다. |
| `PUT /games/{id}/notebook` | `notebook.html` | 인물별 의심도와 투표·결선 단계의 확신도를 저장한다. 204 뒤 저장 안내를 표시하고 재조회하면 값을 복원한다. 다음 날·결선에서 같은 값을 저장해도 새 기록이 남는다. |
| `POST /games/{id}/survey` | `survey.html` → `survey_done.html` | 종료 화면의 제출·건너뛰기 폼이 같은 경로를 쓴다. 최초 성공은 `#survey`를 완료 조각으로 교체하고, 동일 요청의 204는 화면을 새로 읽어 완료 안내를 표시한다. |
| `GET /static/assets/…`, `GET /static/ads/…` | `content/images/manifest.json` | 등록된 이미지 파일을 제공한다. |
| `GET /static/game.css`, `GET /static/js/…` | `web/static/` | 화면 스타일과 행동 폼 스크립트를 제공한다. |
| `/ws/game/` | `static/js/paldo_ws.js` | 로그인된 전체 페이지에서 연결 하나를 열고 광고 비콘을 전송한다. 연결·재연결 때 `hello`로 대기열 초과와 페이지 이탈의 폐기 수를 보낸다. 게임 요청은 기존 HTTP를 유지한다. |
| `GET /ads/click`, `GET /ads/landing/{creative_id}` | 서버 광고 중계 | 클릭은 같은 출처 랜딩으로 이동하고 실패하면 홈으로 돌아간다. 랜딩 실패에는 돌아가기 링크를 표시한다. |

- [서버 라우팅](../server/ROUTING.md)
- [ad_slots.js](static/js/ad_slots.js)는 노출 조건을 확인해 B-07-05의 `paldoWS.impression`으로 비콘을 보낸다. 클릭은 `/ads/click?t=` 게임 웹 중계 경로다.
- [웹 화면 설계](DESIGN.md)

## 광고 지면

`ad_slot_tags.py`가 `ad_placeholder.html`의 크기를 공통 `SLOT_SIZES`에서 읽는다. 로비 `accounts/home.html`은 `lobby_banner`, 종료된 `meeting.html`은 `result_banner`만 배치한다. `GET /ads/slot/{slot}`은 `ad_slot.html`을 반환한다. 소재가 없으면 200 빈 조각으로 기존 내용을 비우며, 복기 `review_side`는 조각까지만 제공한다(B-10-01에서 화면 배치). 로그인된 공통 화면은 `paldo_ws.js`와 `ad_slots.js`를 한 번 로드한다. 회의·투표·밤에는 광고 칸이 없다.

## 고정 회차와 명령 재전송

확신도 화면은 5단계 버튼으로 0·25·50·75·100을 보내며 서버는 계약대로 0~100 정수를 허용한다. B-07-02의 수첩·투표 확신도는 기존 `PUT /games/{id}/notebook`, 설문·전체 건너뛰기는 `POST /games/{id}/survey`를 사용한다. 새로고침은 기존 `GET /games/{id}`로 최근 저장 값·완료 표시를 복원한다. 저장 응답 유실 복구는 서버가 처리하며 브라우저의 재전송용 임의 ID나 새 경로는 추가하지 않는다. [서버 설계](../server/DESIGN.md#b-07-02-인수계약-확인)

고정 회차의 시작·이어 하기 URL은 그대로다. B-06-03의 `paldo_web/mongo.py`가 설치한 실제 저장소를 사용하면 서버 재시작 후 홈 → 같은 `GET /games/{id}`로 복원한다. 검증 명령과 연결 조건은 [서버 시험 안내](../server/tests/README.md#b-06-03-실제-mongo-게임회차-통합-시험)에 둔다.

B-06-03의 `POST /games/{id}/commands` 저장 응답 유실은 서버에서 동일 저장을 한 번 재시도하고, 저장된 응답이 있으면 기존 JSON·htmx 응답 경로로 돌려준다. 클라이언트 재전송은 같은 `command_id`를 유지한다.

B-06-03의 `/admin/sessions` → 고정 회차 등록 → 홈의 배정/이어 하기 → `POST /games`·`GET /games/{id}` 경로는 그대로다. 최신 dev에서 회차·소유자 가명·동의·광고 대응표·재접속 보존을 재검증했다. [증빙](../docs/evidence/B-06-03.md).

## B-08-01 결과·진척·결말

- 홈의 일반 판·고정 회차 시작 폼은 `ending_choice.html`을 포함한다. 열린 결말만 선택할 수 있고 준비 중인 도전은 비활성이다. 초상 선택과 일반 판 시작 UUID는 기존 필드로 함께 전달한다.
- 기존 `GET /games/{id}`의 전체 화면·회의 조각은 종료 시 `game_result.html`에 공개 승패·종료 사유와 선택 결말을 표시한다. 감금 기록·광고·설문 경로는 유지한다.
- 홈·결과의 단서 링크는 `GET /players/me/progress` → `progress.html`로 이동한다. 일반 API 요청은 기존 `ProgressOutput` JSON이다.

B-08-01 버전 문안 경로: 기존 결과·진척 요청 → `server/api/story_view.py` → `server/story_catalog.json`의 해당 버전. 콘텐츠 갱신 뒤 전체 화면·htmx·반복 조회도 같은 결말 문안과 수집 단서 원문을 표시한다. URL·JSON DTO는 유지한다.

## B-08-03 광고 내부 HTTP

브라우저 경로는 기존 /ads/slot/{slot}·/ws/game/·/ads/click·/ads/landing/{creative_id}를 유지한다. 게임 서버 내부에서만 AdGateway → server/infra/ad_client.py가 광고 서버로 중계한다. 세 지면과 실패 화면을 로컬 HTTP 대역으로 검사하며 실제 배치에서 직접 광고 출처 요청 0건을 확인하는 것은 D 인수 후 범위다.
