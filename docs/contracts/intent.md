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
