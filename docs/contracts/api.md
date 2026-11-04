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

## 5. LLM 답변 폴링
- 캐묻기가 수락되면, 응답 화면의 해당 발언 자리에 폴백 문장이 먼저 표시된다. 그 자리에 `hx-get="/games/{id}/renders/{job_id}" hx-trigger="every 1s"` 조각이 붙는다.
- `RenderOutput.final`이 true이면(done, timeout, failed, cancelled, discarded) 폴링 속성이 없는 조각으로 바꿔 폴링을 멈춘다. done이 아니면 `text`는 폴백 문장이다.
- 판이 다음 단계로 넘어가면 응용 계층이 미완료 작업을 취소한다. 그 뒤의 폴링 응답은 `final=true`(폴백)다.

## 6. 화면 정보 제한
- 플레이어 화면에 들어가는 것은 `PlayerView`의 정보뿐이다(domain.md 7절).
- 템플릿, 뷰 모델, JSON 어디에도 진실, NPC 믿음, 다른 NPC의 개인 지식을 넣지 않는다. `tests/contract/test_fixtures.py::RecordedGame.test_view_has_no_truth`가 녹화 판에 금지 키가 없는지 검사한다.
