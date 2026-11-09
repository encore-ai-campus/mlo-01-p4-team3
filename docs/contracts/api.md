# API 계약 (B)

- **기준**: 응용 계층 유스케이스의 입출력 DTO(`server/app/dto.py`)다. 엔진과 표현 계층은 이 DTO를 공유한다.
- **화면 구성**: Django 템플릿 + htmx로 만든다. 화면 안의 작은 상태(수첩 슬라이더, 발언 카드 편집)는 Alpine.js가 맡는다.

## 1. 응답 형식
- 같은 경로가 두 형식으로 응답한다.
  - htmx 요청(`HX-Request: true`)에는 **부분 HTML**(이름 있는 템플릿 조각)을 돌려준다.
  - 그 밖의 요청(`Accept: application/json`)에는 DTO를 **JSON**으로 돌려준다. 테스트, 시뮬레이터, 녹화 판 픽스처가 이 형식을 쓴다.
- JSON은 `engine.domain.serde.to_jsonable(출력 DTO)`로 만든다. 입력 JSON은 `from_jsonable(입력 DTO)`로 엄격하게 검증한다(알 수 없는 필드는 400).
- 템플릿은 **화면용 뷰 모델**(`server/api/viewmodels.py`)만 받는다. 뷰 모델은 DTO와 별개이며, 플레이어에게 허용된 정보만 담는다. 템플릿에 DTO나 엔진 객체를 직접 넘기지 않는다.
- 녹화 판(`fixtures/api/recorded_game_base6.json`)은 JSON 그대로 유지한다. 단계 i의 `view`는 `state_version = i`인 `PlayerView`다.

## 2. 엔드포인트

| 메서드·경로 | 입력 DTO | 출력 DTO | htmx 조각 |
|---|---|---|---|
| POST `/games` | `StartGameInput` | `GameOutput` | `meeting` 전체 |
| GET `/games/{id}` | - | `GameOutput` | `meeting` |
| POST `/games/{id}/commands` | `SubmitCommandInput` | `CommandOutput` | `meeting` 또는 `reject_toast` |
| POST `/games/{id}/utterances` | `ParseUtteranceInput` | `ParseUtteranceOutput` | `speech_cards` |
| POST `/games/{id}/utterances/{attempt_id}` | `ResolveUtteranceInput` | `CommandOutput` 또는 `ParseUtteranceOutput` | `meeting` 또는 `speech_cards` |
| GET `/games/{id}/renders/{job_id}` | `PollRenderInput` | `RenderOutput` | `speech_line` |
| PUT `/games/{id}/notebook` | `SaveNotebookInput` | 204 | - |
| POST `/games/{id}/survey` | `SubmitSurveyInput` | 204 | `survey_done` |
| GET `/games/{id}/review` | - | `ReviewOutput`(종료 후만, 그 전에는 403) | `review` |
| GET `/players/me/progress` | - | `ProgressOutput` | `progress` |

### 2-1. 계정 (11/4 PM 결정, B-06-04)
- 위 표의 모든 엔드포인트와 화면은 **로그인 필수**다. 비로그인 요청은 JSON이면 401, 화면 요청이면 로그인 화면으로 이동한다.
- `/games/{id}/…`는 그 판을 시작한 가명 ID의 계정만 쓸 수 있다. 다른 계정의 판이나 그 판에 속하지 않은 `attempt_id`·`job_id`는 404다. 이 검사는 3절의 멱등 재요청 응답과 409 판정보다 먼저 한다.
- 계정 엔드포인트는 Django 인증 폼이며 응용 계층 DTO를 쓰지 않는다. Django 세션 인증, CSRF 검사 유지. 규칙은 [privacy.md](privacy.md) 5절.

| 메서드·경로 | 입력 | 결과 |
|---|---|---|
| GET·POST `/accounts/signup` | 로그인 ID, 비밀번호, 비밀번호 확인, 동의(privacy.md 3절), 초대 코드(`PALDO_SIGNUP_INVITE_CODE`가 있을 때만, 파일럿은 설정 안 함) | 계정 생성, 가명 ID 발급, 로그인 상태로 이동. 가입 요청 수 제한 없음(11/6 삭제, privacy.md 5절) |
| GET·POST `/accounts/login` | 로그인 ID, 비밀번호 | 세션 시작. 실패·잠금 규칙은 privacy.md 5절 |
| POST `/accounts/logout` | - | 세션 종료 |

- `StartGameInput.player_pseudonym`은 클라이언트가 보내지 않는다. 표현 계층이 로그인 계정의 가명 ID로 채운다. 요청 본문에 이 필드가 있으면 400이다. `GET /players/me/progress`의 `me`도 로그인 계정의 가명 ID다.

### 2-2. 판 종료 설문 (11/6 PM 결정, P-29, B-07-02)
- `SubmitSurveyInput`은 응답과 전체 건너뛰기를 같은 `POST /games/{id}/survey`로 받는다. 문항은 [user_test/plan.md](../user_test/plan.md) 7절.

| 경우 | `skipped` | `fun`·`unfair`·`npc_plausible` | `comment` |
|---|---|---|---|
| 응답 | `false`(생략하면 `false`) | 셋 다 1~5 정수 | 선택, 4,000자 이하 |
| 전체 건너뛰기 | `true` | 셋 다 `null` | `""` |

- 위 두 경우가 아닌 조합(점수 일부만, `skipped=true`인데 점수나 의견이 있음, `skipped=false`인데 점수가 `null`)은 400이다. `skipped`가 없는 기존 응답 요청은 그대로 통과한다.
- 설문은 판마다 한 번이다. 같은 내용을 다시 보내면 204이고 이벤트를 더 만들지 않는다. 처음과 다른 내용(건너뛴 뒤 응답으로 바꾸기 포함)은 409다.
- 건너뛰어도 `survey_submitted` 이벤트를 1건 낸다(INT-09 7번: 판 수 = 설문 이벤트 수). 이벤트 본문은 [events.md](events.md) 2절.
- 입력 형식과 화면·재전송 동작은 [설문 처리 경로](../../server/ROUTING.md)와 [B-07-02 검증](../evidence/B-07-02.md)에서 확인한다.

### 2-3. 판 시작: 일반 판과 회차 판 (11/8 사용자 결정, E-08-14)
- 로그인·동의한 사용자는 누구나 홈의 "새 게임"으로 바로 판을 시작한다. 운영자가 시드나 참가자 명단을 정하지 않는다.
- `StartGameInput.session_id`는 선택이다. 생략·`null`·`""`이면 **일반 판**, 값이 있으면 **회차 판**(B-06-03 테스트 회차)이다.

| 항목 | 일반 판(`session_id` 없음) | 회차 판(`session_id` 있음) |
|---|---|---|
| 프리셋 | 공개 프리셋만(지금 `base6`). 그 밖은 400 `preset_not_open` | 회차 계획의 다음 프리셋 |
| 시드 | 서버가 난수로 정해 판 메타에 남긴다. 요청에 `seed`가 있으면 400 `seed_not_allowed` | 회차 계획의 고정 시드(`seed`는 생략하거나 계획과 같아야 함) |
| 버전 3개 | 배포 코드의 `rule_version`·`engine_version`, 운영 설정의 활성 `content_version`. 설정이 없으면 503 `start_policy_unavailable` | 회차 등록 값 |
| 중복 시작 | `start_token`(UUID) 필수, 없거나 형식이 틀리면 400. 같은 계정이 같은 값을 다시 보내면(그사이 공개 프리셋·콘텐츠 버전 설정이 바뀌어도) 같은 판을 돌려준다 | 회차 계획 판 ID로 같은 판을 돌려준다 |
| 거부 | - | 없는 회차·배정되지 않은 회차는 404. 일반 판으로 바꾸지 않는다 |

- 가명·이벤트·수첩·설문은 두 경우 모두 같은 경로로 남는다. 일반 판의 이벤트 봉투 `session_id`는 `null`이다.
- 홈 화면은 시작 폼마다 새 `start_token`을 넣고, 끝나지 않은 일반 판은 "이어 하기"로 보여 준다.

## 3. 명령과 충돌
- 모든 명령에는 클라이언트가 만든 `command_id`(UUID)와 `expected_state_version`이 있다. 화면의 각 폼은 현재 `state_version`을 hidden 필드로 갖는다.
- HTTP 상태 코드는 다음과 같다.
  - 수락: 200, `accepted=true`
  - 같은 `command_id` 재요청: 200, `replayed=true`(저장된 응답 그대로)
  - 엔진 거부(`illegal_phase`, `missing_slot`, `limit_exceeded` 등): 422. `reject`와 현재 화면을 함께 보내며, 소비는 없다.
  - 버전 충돌: 409, `reject=state_version_conflict`. 현재 화면을 함께 보내며, 클라이언트는 화면을 바꿔 끼운다. 서버는 명령을 자동으로 재적용하지 않는다(ports.md 2절).

## 4. 자유 입력 수명주기
1. 해석: `POST /utterances`를 보내면 `attempt_id`를 발급한다. 결과는 카드 0~N장과 실패 상태다. 이 단계에서는 **소비가 없다.** `intent_attempt` 이벤트를 기록한다.
2. 카드: 다중 의도이면 카드가 여러 장 나오고, 플레이어는 **하나만** 고른다. 빠진 슬롯이 있는 카드는 실행 버튼을 비활성화하고, 해당 칸을 고르게 한다.
3. 반응(`ResolveUtteranceInput.reaction`):
   - `confirm`: 고른 카드를 `command_id`와 함께 명령으로 제출한다. 엔진이 수락하면 소비된다.
   - `edit`: 수정한 명령(`edited_command`)을 제출한다. 라벨 출처는 `player_edited`가 된다.
   - `retry`: 새 문장으로 다시 해석한다. 새 `attempt_id`를 발급하고, 이전 시도는 `superseded_by`로 연결한다.
   - `cancel`, `switch_to_buttons`, `none_of_these`: 실행하지 않는다. 소비가 없고, 시도는 닫힌다.
4. 실패 상태(`ParseFailure`)와 화면 처리:

   | 실패 상태 | 화면 처리 |
   |---|---|
   | `empty_input` | 아무 일 없음 |
   | `unsupported_intent` | 지원 예시를 보여 주고 버튼 입력으로 전환한다 |
   | `missing_target` | 대상을 고르게 한다 |
   | `ambiguous_evidence` | 근거 후보 카드 중에서 고르게 한다 |
   | `unparseable` | 다시 입력하게 한다 |

5. 시도가 해석한 시점의 `state_version`이 바뀌면, 그 시도로는 실행할 수 없다(409). 다시 입력해야 한다.
6. 단계 거르기(B-09-07): 응답 카드(`cards`)에는 현재 단계에서 플레이어가 낼 수 있는 명령만 남는다(`is_legal_kind`). 투표·결선 단계의 `suspect` 후보는 같은 대상의 `vote` 카드로 바꾸고(결선이면 결선 후보 대상만), 뜻이 뒤집히는 후보(`defend` 등)는 버린다. 거른 뒤 카드가 0장이면 `unsupported_intent`로 안내하고 `suggestions`에 현재 단계의 플레이어 행동 종류를 담는다. `intent_attempt` 이벤트의 `candidates`·`failure`는 해석기 원래 결과 그대로다. 아침·밤 화면은 입력 칸을 비활성으로 둔다.

## 5. LLM 답변 폴링
- 게임 추궁 답변에는 `Powered by Kanana`를 표시하지 않는다(11/8 PM 결정, B-08-02 ③). 게임 문장은 템플릿·Claude Code 사전 작성 변형을 사용한다. 최초 화면·폴링 조각·완료·취소 재조회 모두 동일하며, 별도 Kanana 실험의 표기 의무와 구분한다.
- 캐묻기가 수락되면, 응답 화면의 해당 발언 자리에 폴백 문장이 먼저 표시된다. 그 자리에 `hx-get="/games/{id}/renders/{job_id}" hx-trigger="every 1s"` 조각이 붙는다.
- `RenderOutput.final`이 true이면(done, timeout, failed, cancelled, discarded) 폴링 속성이 없는 조각으로 바꿔 폴링을 멈춘다. done이 아니면 `text`는 폴백 문장이다.
- 판이 다음 단계로 넘어가면 응용 계층이 미완료 작업을 취소한다. 그 뒤의 폴링 응답은 `final=true`(폴백)다.
- 복기 해설(`GET /games/{id}/review`의 해설 칸)도 같은 폴링 조각(`/games/{id}/renders/{job_id}`)을 쓴다(11/4 PM 결정 #17). LLM 서비스와의 전송은 [ports.md 4절](ports.md).

## 6. 화면 정보 제한
- 플레이어 화면에 들어가는 것은 `PlayerView`의 정보뿐이다(domain.md 7절).
- 템플릿, 뷰 모델, JSON 어디에도 진실, NPC 믿음, 다른 NPC의 개인 지식을 넣지 않는다. `tests/contract/test_fixtures.py::RecordedGame.test_view_has_no_truth`가 녹화 판에 금지 키가 없는지 검사한다.

## 7. 웹소켓과 광고 중계 (11/4 PM 결정, B-07-05)
- 브라우저는 게임 웹 출처 하나에만 접속한다. 광고 노출 비콘은 웹소켓으로, 클릭·랜딩은 HTTP로 게임 웹에 보내고, 게임 웹이 광고 서버에 넘긴다([ports.md 5절](ports.md), [ads.md 3절](ads.md)). 게임 명령·화면은 지금처럼 HTTP(htmx)이고 웹소켓으로 옮기지 않는다. LLM 답변 폴링(5절)도 그대로다.
- **연결**: `/ws/game/`(HTTPS 환경에서는 `wss://`), 페이지당 1개, 로그인 필수(2-1절). 구성은 `ProtocolTypeRouter` → `OriginValidator`(허용 출처는 `PALDO_PUBLIC_ORIGIN` 전체 값 하나. `AllowedHostsOriginValidator`는 호스트만 비교해 스킴·포트가 다른 출처를 통과시키므로 쓰지 않는다) → `AuthMiddlewareStack` → `URLRouter`. 채널 계층은 `InMemoryChannelLayer`이고 게임 웹은 Daphne 프로세스 1개로 띄운다. InMemory 계층은 프로세스끼리 공유되지 않지만 그룹 방송을 쓰지 않으므로 이 범위에서는 문제가 없다. 프로세스를 늘리려면 채널 계층 교체가 먼저다.

| 상황 | 처리 |
|---|---|
| 비로그인 | 연결 수락 직후 닫힘 코드 4401 |
| 연결 중 로그아웃·세션 만료 | `ad.impression`을 처리하기 전에 세션이 아직 있는지 확인하고, 없으면 닫힘 코드 4401 |
| `PALDO_PUBLIC_ORIGIN`과 스킴·호스트·포트 중 하나라도 다른 출처, `Origin` 없음 | 핸드셰이크 거부 |
| 메시지 4,096바이트 초과 | 닫힘 코드 1009 |
| 형식 위반, 알 수 없는 `type`, 연결당 초당 5개 초과(`hello`는 세지 않음) | `error` 응답, 연결 유지 |

- **메시지**(JSON 텍스트 프레임):

| 방향 | 형식 | 뜻 |
|---|---|---|
| 브라우저 → 게임 웹 | `{"type": "hello", "dropped": 3, "dropped_by_reason": {"queue_full": 2, "page_exit": 1}}` | 연결·재연결 직후 1회. 직전 연결 이후 브라우저가 버리고 아직 보고하지 않은 비콘 수. 값은 모두 0 이상 정수이고 `dropped = queue_full + page_exit`. 버린 것이 없으면 `{"type": "hello", "dropped": 0, "dropped_by_reason": {"queue_full": 0, "page_exit": 0}}`(P-29) |
| 브라우저 → 게임 웹 | `{"type": "ad.impression", "msg_id": "<UUID>", "token": "<serve 토큰>", "visible_ratio": 0.0~1.0, "visible_ms": 정수, "occurred_at_ms": 정수}` | 노출 비콘(면적 50% 이상 1초 이상, serve마다 1회). `msg_id` 외의 필드는 ads.md 3절 비콘 본문과 같다 |
| 게임 웹 → 브라우저 | `{"type": "ack", "msg_id": "<UUID>", "relayed": true}` | 받음. `relayed`는 광고 서버 중계 성공 여부. 실패해도 다시 보내지 않는다(비콘 손실, ads.md 8절) |
| 게임 웹 → 브라우저 | `{"type": "error", "msg_id": "<UUID 또는 null>", "code": "bad_message"}` | 거부. `code`는 `bad_message`·`unknown_type`·`rate_limited` 중 하나. 연결은 유지 |

- **끊김 처리**: 브라우저가 자동으로 다시 연결한다(1·2·4·8·16·30초 간격, 이후 페이지가 열려 있는 동안 30초마다). 끊긴 동안의 비콘은 대기열(최대 20개, 넘으면 가장 오래된 것을 버림)에 두었다가 다시 연결되면 초당 4개 이하로 보낸다(연결당 상한 5개 아래). `ack`를 받기 전에 끊긴 비콘은 대기열 앞에 다시 넣어 다시 보낸다(광고 서버가 같은 serve의 두 번째 비콘을 `duplicate`로 판정하므로 중복 집계가 없다, ads.md 6절). `rate_limited`로 거부된 비콘은 다시 보내지 않고 버리되 브라우저 보고에는 넣지 않는다(서버가 거부할 때 센다). 닫힘 코드 4401이면 다시 연결하지 않는다(다음 페이지 이동 때 로그인 화면으로 간다). serve 토큰 만료(30분)는 광고 서버가 판정한다. 대기열이 넘쳐 버린 수(`queue_full`)와 페이지 이동·닫기 때 남은 대기열을 버린 수(`page_exit`)는 `sessionStorage`에 사유별로 쌓았다가 다음 연결의 `hello`로 보내고, `hello`를 보낸 뒤 0으로 되돌린다. 탭을 닫으면 `sessionStorage`도 사라져 보고하지 못하므로 이 지표는 손실의 **하한**이다. 실제 손실 판단은 광고 서버의 serve 대비 노출 비율로 한다.
- **버린 비콘 지표**(P-29): `paldo_ad_beacon_dropped_total{reason}` 하나만 둔다. 총수 지표는 따로 두지 않고, 총수가 필요하면 이 지표를 reason 전체로 더한다.

| reason | 세는 쪽 | 시점 |
|---|---|---|
| `queue_full` | 게임 웹이 `hello.dropped_by_reason`을 받아 더함 | 다음 연결의 `hello` |
| `page_exit` | 같음 | 같음 |
| `rate_limited` | 게임 웹이 `ad.impression`을 거부할 때 직접 더함(`hello`로는 받지 않음) | 거부 즉시 |

- `hello`의 형식 위반(음수·정수 아님, `dropped`와 사유 합계 불일치, `dropped_by_reason` 없음, 알 수 없는 사유)은 지표에 더하지 않고 `error`(`bad_message`)로 답한다. 연결은 유지한다.
- **HTTP 중계 경로**(로그인 필수):

| 메서드·경로 | 처리 |
|---|---|
| GET `/ads/slot/{slot}` | 기존(ports.md 5절). 응답 조각의 클릭 링크는 아래 `/ads/click`, 비콘은 웹소켓 |
| GET `/ads/click?t=<serve 토큰>` | 광고 서버 클릭 엔드포인트를 부르고 302 `/ads/landing/<creative_id>`. 실패·시간 초과면 302 `/`. 어느 경우든 이동한다 |
| GET `/ads/landing/<creative_id>` | 광고 서버 가상 랜딩 HTML을 그대로 전달. 실패면 대체 조각(돌아가기 링크) |
