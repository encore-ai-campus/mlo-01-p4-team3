# 웹 라우팅

B-09-02 D 어댑터 수신은 서버의 해석기 팩토리 조립만 추가한다. 기존 자유 입력·카드·폴링 URL과 템플릿 연결은 그대로이며 화면 계약 회귀 결과와 실물 모델 대기는 [증빙](../docs/evidence/B-09-02.md)에 기록한다.

B-10-07: 설정 PALDO_GAME_UI(vn 기본/classic) → paldo_web/ui_templates.py → meeting.html 또는 meeting_classic.html. meeting_parts/records.html은 공개 기록, meeting_actions.html·utterance_input.html은 기존 명령 전송을 유지한다. ui-stage.js는 폭 900px 기준 배치와 정보 창, ui-vn.js는 읽기·비교·확인과 버튼 설명 상자를 맡는다. 행동 결과·아침 진술 대사 줄은 `server/api/vn_script.py` → `meeting_view.py`의 `vn_lines` → records.html의 숨은 `.vn-script`로 들어온다(문장 틀은 vn_script.py 맨 위). 투표 결과 발표 장면도 vn_script.py, 화면 잠금은 ui-vn.js. 첫 판 안내 대사는 `server/api/tutorial_script.py`(도메인성 순수 함수) → 첫 판 판단은 `server/app/usecases.py` `guide_info`(응용) → `meeting_view.py`가 줄을 끼움 → `meeting_parts/vn_line.html`. 아침 실종 발표(까치)는 `vn_script.announcement_lines`, 진술표 카드 문장은 `vn_script.claim_card_text`. 판 도중 원한 단서 알림은 판정 `server/app/clue_notice.py`(응용, 순수 함수) → `guide_info`가 함께 돌려줌 → `meeting_view._clue_vn_lines`가 고백 발언 뒤에 줄을 붙임(족제비 문장은 `tutorial_script.CLUE_*`). 의심·모순 지적 즉시 반응은 `vn_script.reaction_lines`(문장 `REACTION_*`, 성향은 응용 `usecases.temperaments`가 판 콘텐츠에서 읽음) → `meeting_view`의 `reaction_lines` → records.html이 플레이어 발언 뒤에 줄을 붙임. 무당 회의 발언 문장(`vn_script.MUDANG_LINES`)과 발언 문장의 괄호 조사 표시는 `vn_script.speech_display_text` → `ui_viewmodels.build_ui_vm`(발언 기록·대사창 원본)과 `interaction_view.line_context`(폴링 조각)에서 보일 때만 적용한다. 수첩 한 번 누름 표시는 `notebook_marks.html`(주민 창·수첩 창) → ui-vn.js가 기존 수첩 저장 경로로 보낸다. 버튼 설명 문구는 `server/api/template_tags.py`의 `UI_TIPS`를 `{% tip %}`로 붙인다. 의존 방향: 템플릿·JS(표현) → meeting_view·vn_script(viewmodel) → 엔진 공개 기록. CSS 공통 토큰은 web/static/css/theme.css다.

공식 그림은 ui_assets.py → assets.asset_url → manifest. 여성 전신·초상은 같은 ID의 v5 파일이다. GET /ui-review는 로그인한 vn에서 manifest의 정식 그림(현재 로고·회의/밤 배경 포함 21개)과 임시 자원 상태를 표시하고 classic은 404다. 수량은 manifest에서 계산한다. 시안 후보 카탈로그는 읽지 않는다. 보존한 전체 HTML 시안과 실제 화면의 적용 범위는 UI_APPLY.md에서 구분한다.

종료 탭은 같은 판 URL에서 결과·복기·단서를 표시한다. GET review의 htmx는 review_panel.html, 일반 HTML은 review.html, JSON DTO·소유권·진행 중 403은 유지한다. 서버·웹 디자인 및 web/PM_UI_REVIEW.md·UI_ASSET_PLAN.md·UI_APPLY.md를 함께 갱신한다.

B-10-02: 기존 게임 전체/htmx 응답의 meeting.html → village_assets.html(고정 지도·전체 초상 펼치기)·asset_credits.html(하단 출처). 좌표·URL은 manifest, 출처는 SOURCES.md에서 읽는다. 펼치기는 HTML details로 처리하며 서버 명령을 보내지 않는다. 새 URL·API는 없다.

B-09-06의 [VN 규칙](../docs/contracts/vn.md)은 PR #304에서 확정됐다. B-10-07 부분 인계에서 기존 회의·밤 경로에 화면을 연결했고 새 명령·서버 읽기 저장 경로는 추가하지 않았다. 첫 등장·추방/처형·엔딩 전신과 표정은 E 후속 구현으로 남는다.

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

B-07-05 네트워크 부하는 화면 조작 대신 실제 HTTP 게임 API·로비 광고 조각·인증 광고 웹소켓을 호출한다. 광고 지면·비콘의 기존 경로와 응답을 유지하며 UI 템플릿·브라우저 스크립트는 변경하지 않는다. 실행 경로와 조건은 [서버 시험 안내](../server/tests/README.md#b-07-05-실제-http웹소켓-성능-비교)를 참조한다.

B-06-04의 가입·로그인·로비·판 종료 URL은 유지한다. 광고 지면 GET은 서버에서 광고용 UUID를 붙인다. 같은 계정 시나리오를 D Django 광고 API에 실제 HTTP로 보내 로그인 ID·계정 가명·판 ID의 수신·로그 비노출까지 로컬 검증했다. VM 운영 배치·브라우저 네트워크 확인은 B-08-03(E)의 별도 범위다.

B-06-02의 실제 Mongo 검증은 기존 `POST /games`·`POST /games/{id}/commands`를 사용한다. 새 화면·URL은 추가하지 않는다. 측정 도구와 범위는 [서버 시험 안내](../server/tests/README.md#b-06-02-로컬-시험)와 [성능 모듈](../docs/modules/perf.md)에 둔다.

dev `f24f6a8`의 엔진·에셋 갱신을 반영한 재검증도 같은 경로를 사용한다. 브라우저 화면 성능은 이 JSON API 측정에 포함하지 않는다.

11/8 PM 결정(B-08-02)에 따라 회의 최초·재조회 화면(`meeting.html`)과 추궁 답변 폴링 조각(`speech_line.html`)에서 Kanana 표기를 제거했다. 완료·실패·취소 뒤 재조회에도 표기가 없고, 폴링의 `outerHTML` 교체·종료 조건은 유지한다.

진행 판 양자택일(11/10 사용자 결정): 홈 시작 폼 → `accounts/open_start_actions.html`(나란한 두 버튼 "진행 중인 판 이어 하기"·"중단하고 새로 시작"과 경고 한 줄, 문구는 `server/accounts/views.py` `OPEN_GAME_TEXT`, 버튼 설명은 `UI_TIPS`) → `POST /games`(`replace_open=1`) → 응용 `server/app/usecases.py` `start`·`abandon_game` → 도메인 `engine/domain/abandon.py`(중단 규칙). `replace_open` 없이 진행 판이 있으면 폼은 `/?open=choose`로 돌아오고 JSON은 409다. 의존 방향: 템플릿·뷰(표현) → 응용 → 도메인.

홈 시작 폼 → `accounts/portrait_choice.html`은 여성·남성 무당 초상을 라디오 버튼으로 선택한다. `mudang_variant`를 기존 시작 API로 보내며 `meeting.html`은 응답 값으로 무당 초상을 표시한다. 오래된 녹화 판의 값 없음은 여성 기본 초상이다. 선택지는 서버의 `presets_enabled.json`을 따르며 초기에는 `base6`만 보인다. 8인 녹화 화면은 시험에서만 임시 설정으로 공개한다.

B-08-02 공동 확인은 현재 dev 빌드의 홈 → 6인 게임 시작 → 진행 → 종료를 함께 확인하고 E가 재확인한다. 후보 태그를 선행 조건으로 두지 않으며, 11/10 테스트 서버 리허설 화면 확인은 B-09-03이다.

B-09-07: 자유 입력 카드(`speech_cards.html`)는 응용 계층(`server/app/interactions.py`)이 현재 단계 합법 명령으로 거른 카드만 받는다(0장이면 `unsupported_intent`와 "지금 할 수 있는 행동"). 아침·밤에는 `utterance_input.html` 입력 칸이 비활성이다. 심문 양식의 진술 체크박스는 `data-night`·`data-slot`을 가지며 `game.js`가 행적 질문의 밤·시간 칸을 맞춘다. 같은 날·단계에 다시 그린 양식은 직전 대상·질문 종류를 고른다(`views._previous_interrogation`). 회의 화면은 `meeting_actions.html`·`utterance_input.html`(발언 카드 자리 포함)을 `.command-bar`(명령·자유 입력 묶음)로 감싼다. 이 검토 브랜치에서는 11/9 B-10-07 기준으로 무대 안 창이 하단 sticky 배치를 대체한다. 설문 없이 떠난 끝난 판은 홈(회차 복귀·새 판 시작 폼) 요청에서 `participant_forms.backfill_surveys`가 같은 요청의 진척·판 조회로 응용 계층 `backfill_skipped_surveys`를 부르고, 18:00 마감 직후에는 관리 명령 `server/accounts/management/commands/backfill_surveys.py`가 응용 계층 `backfill_all_skipped_surveys`로 일괄 채운다. 결과 화면을 떠날 때 브라우저가 보내는 요청은 없다. 10번: 진술 선택은 의심·감싸기·모순 지적·심문 양식과 해당 카드 수정에만 접힌 `details.evidence-picker`(요약 `.evidence-count`에 고른 개수)로 두고, `game.js`가 개수 갱신·"왜 의심하는가"에서 숨김·"왜 말이 다른가"에서 열기를 한다. 자유 입력은 진술을 고르지 않는다(서버 `selected_cards` 처리는 빈 값으로 남김). 선택형 도전 입력은 E-09-08이 실제 옵션과 함께 붙인다.

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

## B-08-01 결과·진척·결말, B-10-01 복기(판 종료 화면)

- 홈의 일반 판·고정 회차 시작 폼은 `ending_choice.html`을 포함한다. 퇴마는 항상, 한풀이는 해금된 것만 고를 수 있다. 판 구성 목록은 `presets_enabled.json`에 공개된 선택형 도전이 있을 때만 `선택형 도전` 묶음으로 보인다(`story_view.preset_choices`).
- 판이 끝나면 기존 `GET /games/{id}` 전체 화면·회의 조각의 `#meeting` 안에 `game_end.html`(결과·복기·원한 단서 탭)을 넣는다. 탭 전환은 `web/static/js/game_end.js`, 모양은 `web/static/css/result.css`(B-10-07 `--pd-*` 이름, 대체값 포함).
- 결과 탭: `game_result.html`(승패·종료 사유·결말 문안)·`result_banner`·종료 설문. 복기 탭: 열 때 `GET /games/{id}/review`(htmx) → `review_panel.html`(정체·숨긴 비밀·이 판 단서·`review_side`). 단서 탭: 열 때 `GET /players/me/progress`(htmx) → `progress_panel.html`.
- 일반 요청은 그대로다: 복기는 `ReviewOutput` JSON, 진척은 로비용 전체 화면 `progress.html`. 복기 행동 목록은 `ReviewOutput.timeline`(E-10-10 `review_trace`, NPC 결정 단위)을 `story_view.review_context`가 화면 행으로 바꾼 것이다. 기록이 없는 옛 판은 빈 목록 안내 한 줄.
- 고칠 곳: 문구·배치 → 위 템플릿, 복기·진척 화면 값 → `server/api/story_view.py`(표현 보조), 요청 분기 → `server/api/views.py`.
- 복기 해설 LLM 요약(E-10-01): `review_panel.html`이 `review_narration.html`을 넣는다. 생성 중이면 2초마다 `GET /games/{id}/review/narration`(htmx, `outerHTML`)을 폴링하고, 확정되면 검사를 통과한 LLM 문장만 "마을 사람들의 속마음" 묶음에 보인다(전부 템플릿이면 묶음 숨김). 화면 값은 `story_view.narration_context`, 규칙은 도메인 `llm/review_narration.py`, 흐름은 응용 `server/app/review_narration.py`.
- "Powered by Kanana": `asset_credits.html`(`{% asset_attribution %}`) 첫 줄 `.kanana-credit`, 기능(`REVIEW_LLM_URL`)이 켜졌을 때만. 문장별 표시 없음(11/11 사용자 결정). 판 화면(`meeting.html`)과 일반 복기 화면(`review.html`)에 같은 조각이 들어간다.

B-08-01 버전 문안 경로: 기존 결과·진척 요청 → `server/api/story_view.py` → `server/story_catalog.json`의 해당 버전. 콘텐츠 갱신 뒤 전체 화면·htmx·반복 조회도 같은 결말 문안과 수집 단서 원문을 표시한다. URL·JSON DTO는 유지한다.

## B-08-03 광고 내부 HTTP

브라우저 경로는 기존 /ads/slot/{slot}·/ws/game/·/ads/click·/ads/landing/{creative_id}를 유지한다. 게임 서버 내부에서만 AdGateway → server/infra/ad_client.py가 광고 서버로 중계한다. 세 지면과 실패 화면을 로컬 HTTP 대역으로 검사하며 실제 배치에서 직접 광고 출처 요청 0건을 확인하는 것은 D 인수 후 범위다.

## B-10-08 계정 디자인·로고·글꼴

기존 `/`·`/accounts/login`·`/accounts/signup`·로그아웃·게임 시작 URL과 POST 필드는 그대로다. `accounts/base.html` → `site_header.html(show_branding=True)`·`css/accounts.css`; `accounts/scene_intro.html` → `assets.asset_url('background:night')`로 정식 그림을 읽는다. 게임 헤더에는 `show_branding`을 전달하지 않는다.

| 파일·경로 | 책임·직접 연결 |
|---|---|
| `static/css/theme.css` → `static/fonts/fonts.css` | 공통 제목·본문 글꼴 토큰. 게임·계정 기본 UI에서 같은 토큰 사용 |
| `static/fonts/pretendard-subset.css` → `woff2-dynamic-subset/` | 공식 문자 범위별 로컬 WOFF2. 외부 CDN 요청 없음 |
| `static/css/accounts.css` | 판 시작 전 계정 화면의 시안 배치·버튼·오류·반응형. 무당 모습 안내는 두 열 전체 폭, 그림 위 제목은 시안 줄높이·기본 자간. 게임 무대 배치는 수정하지 않음 |
| `accounts/home.html` → `static/js/account-start.js` | 일반/배정 테스트의 details를 하나씩 펼침. 서버의 `test_round_pending`으로 기본 영역 결정. 두 기존 시작 양식 보존 |
| `content/images/branding/tiger_emblem.png` | manifest `branding:tiger_emblem` → 기존 정적 파일 경로. 원화 정규화·등록은 `tools/assets/branding.py` |
| `server/tests/ui_theme_preview.py --font-probe` → `ui_font_probe.py` | 명시적 시험 모드의 로그인 응답에만 Navigation/Resource Timing 표본 추가 |
| `tools/perf/account_assets.py` | 시험 DOM 표본과 디스크 글꼴 합계를 공통 증빙 JSON으로 기록 |

로드 측정용 숨김 출력은 시험 미들웨어에서만 넣으며 운영 `MIDDLEWARE`·URL·템플릿에는 넣지 않는다. [웹 디자인](DESIGN.md#b-10-08-로그인홈-시안-적용) · [증빙](../docs/evidence/B-10-08.md)


## 확정 표정 에셋 교체 (2026-11-10)

A-10-06의 기쁨·당황 16종 전신과 같은 수의 파생 초상을 기존 manifest ID·경로에 등록했다. 기본 인물 파일·API·공개 사건 선택 로직은 변경하지 않았다. 복구한 출처 기록과 제출 상태는 docs/evidence/A-10-06.md를 따른다.

A-11-04의 슬픔 8종 전신과 같은 수의 파생 초상을 기존 manifest ID·경로에 등록했다. 기본 인물 파일·API·공개 사건 선택 로직은 변경하지 않았다. 복구한 출처 기록과 제출 상태는 docs/evidence/A-11-04.md를 따른다.

### 공개 사건 표정 자동 연결 (B-10-07, 2026-11-10)

A-10-06·A-11-04의 확정 그림을 공개 사건에 연결했다. 앞 절의 에셋 배치 검수와 별도로, 현재 대사·사건 ID마다 표정을 계산한다. 감싸진 대상은 다음 자기 대사에 기쁨, 의심·모순 지적·공개된 탁한 점괘의 대상은 다음 자기 대사에 당황, 희생자 발표 뒤 첫 대사·고백·감금 당사자는 슬픔이다. 다음 대사에서 기본으로 돌아오며, 겹치면 슬픔 > 당황 > 기쁨이다. 승인된 예외로 투표 생존 기쁨과 아침 첫 대사 슬픔이 겹친 한 줄은 기쁨이다.

표정 선택은 PlayerView의 공개 기록만 받고 역할·진위·현재 생존 상태·개인 점괘는 입력하지 않는다. 기색은 공개 기록의 shown=True인 주장에만 문구와 당황을 붙인다. 표정 파일이 미등록·임시·유실이면 같은 인물의 기본 그림으로 대체하고 남/여 무당 선택을 유지한다. API·명령·DTO·엔진·이미지 파일·계약·기획·결정 문서는 바꾸지 않았다. 에셋 규격표 7절은 B-10-07 1-3에 이미 승인된 11/9 규칙으로 동기화했다.

[표정 연결 검수·한계](../docs/evidence/B-10-07.md#공개-사건-표정-연결-2026-11-10). 실운영 DB·VM 판정이나 B-10-07 전체 완료를 뜻하지 않는다.

| 경로 | 책임·직접 연결 |
|---|---|
| `meeting_parts/records.html` | 공개 발언 ID → `vn_expressions` → `vn_sprite`로 그림 URL, 기색 문구는 폴링 본문 밖에 보존 |
| `meeting_parts/vn_line.html` | 진술·즉시 반응·감금·피해자 컷 ID → 같은 표정 조회. 마스코트는 기존 경로 |
| `js/ui-vn.js` | 지금 줄의 그림만 표시하고 공개 기색 문구를 대사·낭독에 포함. 읽기 ID/seen 저장 방식 유지 |
| `game.css .vn-guide-skip` | PC 행동 패널 안에 가려지던 안내 버튼을 대사창 쪽으로 배치. 1.95 이상 화면은 기존 오른쪽 배치 |

### B-11-01 캐릭터·도움말 표시 보완 (2026-11-11)

기존 `vn_scene.html`의 캐릭터 버튼과 `ui_help.html`의 도움말 대화상자가 `web/static/game.css`를 사용한다. 기존 `ui-vn.js`의 캐릭터 읽기·도움말 열기/닫기 처리는 그대로이며 새 URL·탭·스크립트는 추가하지 않았다. [검증](../docs/evidence/B-11-01.md).

### 행적표 (B-10-07, 2026-11-11)

기록 창 진술표 앞 행적표: `server/api/alibi_grid.py`(표현, 순수 함수 `build_alibi_grid`, PlayerView 공개 진술·실종·감금만 읽음) → `meeting_view.py`의 `alibi_grid`·`alibi_grid_duo` → `meeting_parts/alibi_grid.html`. VN은 무대 버튼(`vn_scene.html`, 기록 다음 `data-stage-open="meeting-alibi-grid"`) → `meeting.html`의 무대 패널 `#meeting-alibi-grid`(ui-stage.js가 다른 패널과 같은 창으로 만듦, 명령 뒤 `#meeting` 교체 때 함께 갱신). classic은 무대 버튼이 없어 records.html 진술표 앞에서 include. CSS는 `css/theme.css`(VN)와 `classic-game.css`(classic)에 같은 규칙. JS 없음(밤마다 `<details>`). 문구·칸 규칙은 alibi_grid.py, 배치는 템플릿을 고친다. 격자에는 진술 카드 속성(`data-record-*`, `.vn-source`)을 쓰지 않는다(ui-vn.js가 진술 카드로 모음).
