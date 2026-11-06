# 의도 해석 계약 (D 구현, B 호출)

포트: `IntentParser.parse(text, ParseContext) -> ParseResult`(ports.py). 예시: `fixtures/intent/seed_examples.jsonl`(라벨 10종 × 5개 이상, 대조쌍 포함).

## 1. 라벨

| 라벨 ID | 명령 | 필수 슬롯 |
|---|---|---|
| suspect | SUSPECT | target |
| defend | DEFEND | target |
| point_contradiction | POINT_CONTRADICTION | evidence(모순 1개 또는 주장 2개) |
| explain | EXPLAIN | - |
| silence | SILENCE | - |
| reveal_divination | REVEAL_DIVINATION | divination(target, night, result) |
| interrogate_whereabouts | INTERROGATE/WHEREABOUTS | addressee, night, slot |
| interrogate_contradiction | INTERROGATE/CONTRADICTION | addressee, evidence |
| interrogate_suspicion_reason | INTERROGATE/SUSPICION_REASON | addressee |
| unsupported | - | - |

- 문장 형태(`form`): assertion, question, quote, request. 같은 단어라도 형태가 다르면 라벨이 달라진다. 예: "<P1>이 수상하다"는 suspect이고, "<P1>이 수상하다고?"는 interrogate_suspicion_reason이다.
- 투표와 점 대상은 버튼으로만 고른다. 따라서 해석 대상이 아니다.
- 예시 문장 속 인물은 `<P1>`, `<P2>` 자리표시자로 쓴다. 학습할 때 콘텐츠 별칭으로 채운다.

## 2. 인물·근거 해소 순서
- **인물**: 문장에 명시한 별칭 → (대명사나 생략일 때) 화면에서 선택한 인물(`selected_npc`) → 직전 대화 상대(`last_addressee`) 순으로 정한다. 명시한 인물과 선택한 인물이 다르면 명시한 인물을 쓴다. 카드에서 바꿀 수 있다. 끝까지 정하지 못하면 `missing_target`이다.
- 인용 질문형("<P1>이 수상하다고?")은 의심 이유 캐묻기로 해석한다. 이때 언급된 인물은 `mentioned` 슬롯이 되고, 수신자는 그 말을 한 사람이다.
- **근거**: 선택한 카드(`selected_cards`) → 문장 속 인물·장소·시간대와 맞는 공개 주장 순으로 정한다. 후보가 여럿이면 `ambiguous_evidence`이고, 후보는 `evidence_alternatives`에 담는다.

## 3. 결과 규칙
- 다중 의도이면 카드를 여러 장 돌려준다(점수 내림차순). 플레이어가 하나를 고른다.
- `needs_confirmation`은 MVP에서 항상 true다. 자동 실행은 하지 않는다.
- 해석, 실패, 취소는 행동을 소비하지 않는다(api.md 4절).
- `versions`에는 model, tokenizer, alias_dict, matcher, threshold를 모두 기록한다(`intent_attempt` 이벤트).

## 4. 의도 시도 이벤트 (`intent_attempt` payload)
- 입력: `attempt_id`, `superseded_by`, `text`(privacy.md 마스킹 규칙 적용), `state_version`, `phase`
- 해석 결과: `candidates`(종류, 점수, 슬롯), `failure`, `versions`
- 플레이어 반응: `reaction`, `final_command`, `label_source`
- LLM 보조 기록(5절, 확장 E-08-12를 했을 때만): 후보마다 `source`, 시도마다 `assist`(아래 5절 형식)

## 5. LLM 해석 보조 (확장 E-08-12, 11/6 PM 결정)
분류 모델이 확신하지 못한 문장에서만 로컬 LLM에 해석 후보를 하나 더 받는다. LLM은 게임 상태를 바꾸지 않고, 결과는 발언 카드 후보 하나로만 들어간다. E-08-12를 하지 않으면 이 절의 필드는 쓰지 않는다(기본값으로 기존 동작과 같다).

- **부르는 조건(낮은 확신)**: 기준선·BERT 해석 결과에서 다음 중 하나. 수치 τ는 E-08-12가 학습에 쓰지 않은 검증 분할(계보 단위)에서 정해 `versions["threshold"]`와 같은 곳에 기록한다.
  - 1위 후보 `intent_score` < τ_p, 또는 1위와 2위 점수 차 < τ_m
  - 1위 후보 `missing_slots`가 비어 있지 않음, 또는 `failure`가 `unparseable`·`ambiguous_evidence`
  - `empty_input`이면 부르지 않는다. 1위가 `unsupported`이고 τ_p 이상이면 부르지 않는다(잡담에 LLM을 쓰지 않음).
- **요청(`IntentAssistRequest`)**: `attempt_id`, 마스킹된 원문(privacy.md), `phase`, 허용 라벨 목록(이 단계에서 합법인 라벨만, 1절 ID), 인물 목록(활동 주민 `npc_id`와 별칭, `ParseContext.aliases`), 공개 기록 ID 목록(주장·투표·기색·점괘. 각 ID와 인물·밤·시간대·장소 요약), `selected_npc`·`selected_cards`·`last_addressee`, `deadline_ms`(기본 5000).
- **응답(`IntentAssistResult`)**: `label`(허용 라벨 중 하나), `form`, 슬롯(`target`·`addressee`·`mentioned`·`night`·`slot`·`evidence`·`divination` 중 해당 라벨의 필수 슬롯), `status`(`done`·`timeout`·`invalid`·`failed`), `latency_ms`, `model_version`·`prompt_version`.
  - 출력은 형식 강제(E-08-11 문법 제약, 하지 않았으면 JSON 파싱 + 같은 검사)로 만들고, 받는 쪽이 다시 검증한다: 라벨이 허용 목록 밖, 인물이 활동 주민 밖, 기록 ID가 요청 목록 밖, 필수 슬롯 형식 위반이면 그 응답 전체를 버리고 `status=invalid`.
  - 응답에는 점수가 없다. LLM 후보의 `intent_score`는 0으로 두고 정렬에 쓰지 않는다(분류 확률과 섞지 않음).
- **후보 합치기**: 유효한 LLM 후보는 `CommandCandidate.source = "llm_assist"`로 표시해 분류기 후보 뒤에 붙인다(분류기 후보와 명령이 같으면 붙이지 않고 그 후보에 `assist_agreed=true`). 분류기·규칙 후보의 `source`는 `classifier`·`rule`. 기존 소비자는 `source`가 없으면 `classifier`로 본다.
  - 카드 화면은 LLM 후보를 다른 후보와 같이 보여 준다. 출처 표시 여부는 B가 정한다(필수 아님).
- **확인 생략 금지**: `source = "llm_assist"`인 후보와, LLM 후보와 합쳐진 시도는 확인 생략(기획서 8.1 실행 정책) 대상이 될 수 없다. 항상 발언 카드로 확인받는다(테스트).
- **시간 상한·폴백**: `deadline_ms`(기본 5000 ms. E-07-07 추궁 생성 p95 약 7.2초보다 출력이 짧은 대신 해석 응답 전체를 기다리게 하므로 더 짧게 둠) 안에 응답이 없으면 생성을 취소하고 분류기 결과만으로 돌려준다(`status=timeout`). 서비스 오류도 같다. 어떤 경우에도 해석 실패가 늘지 않는다.
- **기록(`intent_attempt.payload.assist`)**: `called`(bool), `reason`(부른 조건 코드: `low_score`·`low_margin`·`missing_slot`·`unparseable`·`ambiguous_evidence`), `status`, `latency_ms`, `model_version`, `prompt_version`, `agreed`(분류기 1위와 같은 명령인지). 후보별 `source`는 `candidates`에 함께 남는다.
- **라벨 출처**: 플레이어가 LLM 후보를 그대로 확인하면 `label_source`는 기존 규칙대로 "그대로 확인(낮음)"이고, 카드를 고쳤으면 "직접 수정(중간)"이다. LLM이 만든 해석은 사람 주석으로 쓰지 않는다. LLM은 문장을 만들지 않으므로 학습 세트에 LLM 생성 문장이 들어가지 않는다(플레이어 원문만 학습 대상).
- 버전: 필드를 덧붙이는 변경이다. 코드 반영(`CommandCandidate.source`·`assist_agreed` 기본값 필드, `IntentAssistant` 포트)은 E-08-12가 하고, 그때 `CONTRACT_VERSION`을 올리고 픽스처를 다시 만든다(parallel_dev.md 4절).
