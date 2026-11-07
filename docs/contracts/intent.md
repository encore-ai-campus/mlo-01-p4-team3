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
| reveal_divination | REVEAL_DIVINATION | divination(target, night, result). 예시 데이터의 슬롯은 target·result 두 개다. night는 문장에 시간 표현이 있으면 그 밤, 없으면 해석기가 공개 주장 중 가장 최근 밤으로 채운다(`intent/parser.py`, 11/7 PM 정리) |
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
- LLM 보조 기록(5절, 확장 E-08-12·E-10-11을 했을 때만): 후보마다 `source`, 시도마다 `assist`(아래 5절 형식)

## 5. LLM 해석 보조 (확장 E-08-12·E-10-11, 11/6 PM 결정)
분류 모델이 확신하지 못한 문장에서만 로컬 LLM에 해석 후보를 하나 더 받는다. LLM은 게임 상태를 바꾸지 않고, 결과는 발언 카드 후보 하나로만 들어간다. 설정 기본값은 끔이며, 끄면 이 절의 필드는 기본값이고 기존 동작과 같다.

- **분류 진단(`ParseResult.diagnostics`, 새 필드)**: 기존 후보 목록만으로는 낮은 확신을 판정할 수 없다(후보 2개가 서로 다른 절일 수 있고 `unsupported`는 후보에서 빠진다). 해석기는 절마다 분류 1위 라벨·점수, 2위 점수, `unsupported` 여부를 담아 돌려준다(D 해석기 변경, D 확인).
- **부르는 조건(낮은 확신, 조건 코드)**: 진단에서 다음 중 하나.
  - `low_score`: 어떤 절의 1위 점수 < τ_p. `low_margin`: 어떤 절의 1·2위 점수 차 < τ_m
  - `missing_slot`: 후보의 `missing_slots`가 비어 있지 않음. `unparseable`·`ambiguous_evidence`: 해당 `failure`
  - 부르지 않음: `empty_input`, 모든 절이 `unsupported`이면서 1위 점수 ≥ τ_p(잡담), 다중 의도 절이 3개를 넘음
  - τ_p·τ_m은 학습에 쓰지 않은 검증 분할(계보 단위)에서 정하고 `versions["threshold"]`에 함께 기록한다. 채택 판정 세트와 겹치지 않는다.
- **요청(`IntentAssistRequest`)**: `request_id`(응용이 해석 전에 발급하는 `attempt_id`와 같은 값), 마스킹된 원문(privacy.md), `phase`, 허용 라벨(이 단계에서 합법인 라벨만, 1절 ID), 활동 주민 `npc_id`와 별칭, 공개 기록 요약(기록 ID, 근거 유형, 화자, 대상 인물, 밤·시간대·장소, 극성(했다/안 했다), 정정·철회 관계), `selected_npc`·`selected_cards`·`last_addressee`, `deadline_ms`.
- **응답(`IntentAssistResult`)**: `label`, `form`, 해당 라벨의 필수 슬롯(`target`·`addressee`·`mentioned`·`night`·`slot`·`evidence`·`divination`), `status`(`done`·`timeout`·`invalid`·`failed`), `latency_ms`(큐 대기 포함), `model_version`·`prompt_version`. 점수는 없다.
- **검증(받는 쪽)**: 응답을 명령으로 바꾼 뒤 기존 해석 ⑤단계(필수 슬롯·단계·횟수·합법성)를 그대로 통과해야 한다. 라벨이 허용 목록 밖, 인물이 활동 주민 밖, 근거 ID가 요청 목록 밖(모순 근거의 합성 ID `claimA|claimB`는 두 ID가 모두 목록 안이면 허용), 슬롯 형식 위반이면 응답 전체를 버리고 `invalid`. 점괘 공개는 공개 결과가 실제와 달라도 합법이므로 진실과 대조하지 않는다(기획서 3.4).
- **후보 합치기**: 유효한 LLM 후보는 `CommandCandidate.source = "llm_assist"`, `intent_score` 0으로 분류기 후보 뒤에 붙인다(분류 확률과 섞지 않음). 분류기 후보와 명령이 같으면 붙이지 않고 그 후보의 `assist_agreed = true`. 분류기 후보가 없던 시도(해석 실패)는 LLM 후보가 유일한 카드가 될 수 있다. 분류기·규칙 후보의 `source`는 `classifier`·`rule`이고, 값이 없으면 `classifier`로 본다. 출처 표시는 B가 정한다(필수 아님).
- **확인 생략 금지**: `source = "llm_assist"`인 후보가 섞인 시도는 확인 생략(기획서 8.1 실행 정책) 대상이 아니다. 항상 발언 카드로 확인받는다.
- **시간 상한·취소**: `deadline_ms`(기본 5000)는 성능 예측이 아니라 포기 시한이다. 큐 대기·전송·생성·검증을 모두 포함한다. 넘으면 서비스에 취소를 보내고(`LineRenderer`와 같은 취소 경로, 생성 중단 로그 확인) 분류기 결과만으로 돌려준다. 보조 요청은 동시에 1건만 처리하고(넘치면 즉시 `timeout` 처리), 큐 우선순위는 캐묻기 대사(0)와 같은 0이되 먼저 들어온 대사 작업을 앞지르지 않는다.
- **기록**: `ParseResult.assist`(새 필드)에 `called`, `reason`(조건 코드), `status`, `latency_ms`, `model_version`, `prompt_version`, `agreed`를 담고, 응용(B)이 그대로 `intent_attempt.payload.assist`에 넣는다. 후보별 `source`는 `candidates`에 함께 남는다.
- **라벨 출처**: 플레이어가 LLM 후보를 그대로 확인하면 `label_source`는 기존 규칙대로 "그대로 확인(낮음)", 고쳤으면 "직접 수정(중간)"이다. LLM 해석은 사람 주석으로 쓰지 않는다. LLM은 문장을 만들지 않으므로 이 보조 경로로는 학습 세트에 LLM 생성 문장이 들어가지 않는다(플레이어 원문만 학습 대상). 이 규칙은 5절 해석 보조에만 해당한다. 예시 데이터를 늘리려고 로컬 LLM으로 만들고 사람이 검수한 변형 예시(E-06-02 2번, 검수 통과분만 `label_source: generated_reviewed`)는 별개다(11/7 PM 정리).
- **버전**: 필드를 덧붙이는 변경이다(`ParseResult.diagnostics`·`assist`, `CommandCandidate.source`·`assist_agreed`, 모두 기본값 있음, `IntentAssistant` 포트). 코드 반영은 E-08-12가 하고 그때 `CONTRACT_VERSION`을 올리고 픽스처를 다시 만든다(parallel_dev.md 4절). 응용이 `attempt_id`를 해석 전에 발급하도록 바꾸는 것도 E-08-12에 포함한다(B 확인).
