# 콘텐츠 계약 (E 작성, A 사용)

- **코드 기준**: `engine/domain/content.py`의 `ContentBundle`
- **최소 예시**: `fixtures/content_min/bundle.json`. 내용은 `tools/build_fixtures.py`가 만든다. 주민 11명은 모든 프리셋을 검증하는 용도이며, 실제 인물로 바꾸는 일은 E가 맡는다.

## 1. 구성 요소

| 항목 | 필수 필드 | 규칙 |
|---|---|---|
| 장소 `PlaceDef` | place, name_ko, public | 5곳 고정. 집만 public=false |
| 성향 `Temperament` | group_weights(근거 묶음별 가중치), aggression, stubbornness, conformity, confession_ease | 값은 0~1, 가중치 기본은 1.0 |
| 인물 `Character` | npc_id, name_ko, aliases, temperament_id, routine(시간대 → 장소 → 확률), speech_style | 별칭은 전체에서 고유해야 한다(의도 해석기의 인물 매칭에 쓴다) |
| 비밀 `Secret` | secret_id, eligible_npcs, slot, actual_place, false_place, confession_line, confession_threshold, conflicts_with, grudge_clue_id | actual_place ≠ false_place |
| 원한 단서 `GrudgeClue` | clue_id, text_ko, unlock, part(identity, death, target) | clue_id는 버전이 바뀌어도 유지한다 |
| 템플릿 `TemplateLine` | template_id, kind, temperament_id?, reaction?, variants | 자리표시자: {target} {place} {slot} {night} {addressee} {fact} {result} |

## 2. 프리셋별 필요 수
`required_counts(preset)`로 정한다.
- 주민 풀은 `n_active` 이상이어야 한다.
- 비밀은 `n_secret` 이상이어야 한다.
- 실제 배정이 검증기(domain.md 10절)를 통과하도록, 비밀마다 배정 가능한 인물이 충분해야 한다.
- 11인 판(duo11)에 필요한 수는 주민 11명, 비밀 4개다.

## 3. 버전
- 묶음 전체에 `content_version` 하나를 붙인다. 판은 시작할 때 이 버전을 고정한다.
- 이전 버전은 진행 중인 판이 끝날 때까지 제공한다.

## 4. 대사 원칙
- 사실(누가, 언제, 어디)은 엔진이 `fact_sentence`로 만든다. 템플릿과 LLM은 말투만 입힌다. LLM은 사실 문장의 끝 어미만 말투에 맞게 바꿀 수 있고 사실 요소는 바꾸지 못한다(`ports.md` 4절, 11/6 PM 결정).
- 캐묻기 반응 템플릿(해명·회피·역공·고백)은 `{fact}`를 그대로 포함해야 한다. 그래야 폴백도 같은 정보량을 갖는다. 회의 발언 템플릿은 엔진이 `{target}`만 넘기므로 `{fact}`를 넣지 않는다(11/6 PM 결정, E-06-01).
