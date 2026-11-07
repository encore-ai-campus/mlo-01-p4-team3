# 도메인 계약

코드 기준: `engine/domain/model.py`(규칙·상태), `engine/domain/content.py`(콘텐츠·배정 검증기). 문서와 코드가 다르면 코드가 기준이고, 다른 점은 버그로 고친다.
기획서: `../paldo_design_v1.md` 3·4장(v1.2의 2·3장, v1.1의 4·5장).

## 1. 참가자와 ID
- 플레이어 ID는 항상 `"mudang"`(`PLAYER_ID`)이다. 주민은 콘텐츠의 `npc_id`(예: `hunjang`)를 쓴다.
- 판 ID(`game_id`)는 판마다 새로 만든다. 플레이어는 가명 ID(`player_pseudonym`, privacy.md)로 식별한다. 관계: 플레이어 1 : 판 N. `PlayerProgress.game_ids`에 쌓인다.
- 활동 인원 `active`에는 무당이 들어 있다. 갇힌 사람은 `locked`, 실종자는 `missing`이다.

## 2. 프리셋
`PRESETS`가 기준이다. 6가지 프리셋(short5, base6, base7, large8, duo9, duo11)이 있다. 첫 사용자 테스트는 `base6`으로 한다. 등장 주민 수는 `n_active`와 같다(활동 인원 − 무당 + 0일째 실종자 1명).

## 3. 단계별 합법 명령 (`PHASE_RULES`)

| 단계 | 플레이어 | NPC | 다음 단계 |
|---|---|---|---|
| morning | advance | (진술은 엔진이 생성) | round1 |
| round1 | suspect, defend, point_contradiction, explain, silence, reveal_divination | 말 행동 5종 | 모두 1행동씩 한 뒤 interrogation |
| interrogation | interrogate, advance | 없음(대답은 엔진 반응) | advance를 하면 round2 |
| round2 | round1과 같다 | 같다 | vote |
| vote | vote | vote | 동점이면 runoff, 아니면 감금 → 종료 판정 → night 또는 ended |
| runoff | vote(동점자만 대상) | vote | 감금 또는 무감금 → 종료 판정 |
| night | divine | night_plan(씐 자) | 일괄 처리 → 종료 판정 → morning 또는 ended |
| ended | 없음 | 없음 | - |

- `GameEngine.apply`는 플레이어 명령 하나를 처리한다. 그 뒤 NPC 차례를 **다음 플레이어 결정 지점까지 자동으로 진행**한다. `PlayerView.awaiting`에는 다음에 행동할 차례가 담기고, 플레이어 차례이면 그 값이 `"mudang"`이다.
- 라운드 발언 순서는 매 라운드 시드로 섞은 `turn_order`를 따른다. 플레이어보다 앞 순서인 NPC 발언은 직전 전이 결과에 함께 담긴다.

## 4. 횟수와 소비 시점 (`LIMITS`, `check_player_limits`)

| 항목 | 한도 | 초기화 |
|---|---|---|
| 캐묻기 | 낮마다 2회 | 아침 |
| 점괘 공개 | 낮마다 1회 | 아침 |
| 말 행동(점괘 공개 포함) | 라운드마다 1회 | 라운드 시작 |
| 점 | 밤마다 1명 | - |
| 투표 | 투표 회차마다 1표, 자기 자신 금지 | - |

- **소비는 엔진이 명령을 수락하고 저장소 커밋이 성공한 순간에만 일어난다.** 다음은 아무것도 소비하지 않는다: 해석, 카드 수정·취소·재시도, 엔진 거부(`RejectCode` 전부), 버전 충돌.
- 캐묻기는 LLM 결과와 무관하게 수락 시점에 소비된다. 폴백 문장에도 같은 정보가 담긴다.
- **기획서 대비 결정**: 점괘 공개는 그 라운드의 말 행동 1회를 함께 쓴다. 기획서는 점괘 공개를 낮 1회로만 정했다. 공개와 의심을 한 라운드에 둘 다 하면 행동 수가 다른 참가자보다 많아지므로 이렇게 정했다.

## 5. 투표와 밤 처리 순서
1. 투표는 동시 비공개다. NPC 표는 vote 단계에 들어갈 때의 상태로 정한다. 플레이어 표는 보지 않는다.
2. 플레이어가 투표하면 엔진이 전원의 표를 한꺼번에 공개한다(`votes_revealed`). 이어서 `resolve_vote`로 결과를 정한다.
   - 최다 득표자가 한 명이면 그 사람을 가둔다.
   - 동점이면 동점자만 후보(`runoff_candidates`)로 결선 투표를 한다.
   - 결선도 동점이면 아무도 가두지 않는다.
3. 감금 직후 `check_end`로 종료를 판정한다. 끝났으면 밤을 실행하지 않는다(`night_runs`). 마지막 씐 자를 가둔 경우도 마찬가지다.
4. 밤에는 플레이어 점 대상과 씐 자의 `NightPlan`을 비공개로 정한 뒤 다음 순서로 일괄 처리한다.
   1. 밤 시작 참가자 집합과 점 대상의 상태를 고정한다.
   2. 피해자를 포함한 밤 시작 참가자 전원의 행적을 정한다. 실행자는 한밤에 피해자의 장소로 간다.
   3. 목격을 정한다. 같은 시간대·같은 공공장소에 있던 사람끼리 일정 확률로 서로를 목격한다. 목격 확률은 A의 규칙 상수이며, 못 봤다는 것은 부재 증명이 아니다.
   4. 피해자를 실종시킨다.
   5. `check_end`로 종료를 판정한다.
   6. 점 결과를 전달한다.
   - 배정 검증기(10절)의 행적 제약은 0일째뿐 아니라 매 밤 같은 규칙을 적용한다. 공범 1명이 갇힌 다음 밤도 마찬가지다.
5. **기획서 대비 결정**: 점은 밤이 시작될 때의 대상 상태로 판정하고, 대상이 그 밤에 실종돼도 결과를 전달한다.

종료 판정 순서(`check_end`): ① G=0이면 마을 승리 → ② 무당이 갇혔으면 창귀 승리 → ③ G ≥ V+M이면 창귀 승리.

## 6. 창귀 2명 규칙
- 공유 지식은 짝 ID, 밤마다의 `NightPlan`(실행자, 피해자), 합의 알리바이(`agreed_alibis`)뿐이다.
- 피해자는 활동 마을 주민 NPC만 될 수 있다(자신, 짝, 무당 제외). 거짓말 의무는 실행자에게만 있다.
- 피해 시각에 다른 공공장소에서 목격된 사람은 "그날 밤 실행자가 아님"(`not_executor`)까지만 확정된다. 창귀 1명 판에서는 같은 상황에서 "씐 자 아님"이 확정된다(`alibi_proposition_kind`).
- **피해 장소 공개(11/4 PM 결정, 기획서 4.3)**: 아침 실종 발표 때 피해 장소(5곳 중 하나, 집이면 피해자의 집)와 시간대(한밤)를 모두에게 공개한다("실종자의 짚신이 ○○에서 발견됐다"). 공개 기록 `DisappearanceRecord.place`·`slot`에 남고, 0일째 사건도 같다. 아침 이벤트 `morning_announced` payload에 `crime_scene`이 들어간다.
- **알리바이 명제의 전제**: 공개된 피해 장소 + 관찰자 자신의 목격(`alibi_propositions`). 피해 시각에 피해 장소가 아닌 공공장소에서 직접 목격한 사람에 대해 명제를 만든다(1명 판 `not_possessed`, 2명 판 `not_executor`까지만). 다른 사람의 공개 목격 주장으로 같은 계산을 하면 **확정 명제가 아니라** `others_claim`·`mutual_vouch` 근거로만 믿음에 반영한다(기획서 5.1: 확정 명제는 직접 목격만). 엔진은 진실의 피해 장소를 쓰지 않고 공개 기록만 쓴다.

## 6-1. 고백과 기색
- 비밀 사건은 0일째 밤의 한 시간대에만 일어난다. 그 밤 그 시간대의 행적만 거짓으로 진술하고, 고백하면 그 주장을 `Claim.corrects`로 정정한다.
- 고백자는 확정 무고로 본다. 관측 스냅샷 `candidates`, 확률 정규화(쌍 조합 포함), 투표 안전 층에서 모두 제외한다. 감금 공개로 씐 자가 아님이 드러난 사람도 이미 활동 인원이 아니므로 후보가 아니다.
- 기색: 사실 주장이 거짓일 때만 판정한다. 주장을 말한 직후 한 번 정하고, 판정 결과(보임·안 보임)는 화자의 `NpcRuntime.tells`에 주장 ID당 1회 고정한다(비공개, 재질문해도 바뀌지 않음). `PublicRecord.tells`(`TellRecord`)에는 **보인 기색만**(`shown=True`) 공개한다. 안 보인 판정까지 공개하면 그 주장이 거짓이라는 진실이 새기 때문이다(7절, 11/6 PM 결정). 근거 `EvidenceType.TELL`은 이 기록의 주장 ID를 가리킨다.

## 7. 정보 공개 범위

| 정보 | 플레이어(`PlayerView`) | NPC 자신(관측) | 학습 스냅샷 | 판 종료 후 |
|---|---|---|---|---|
| 공개 기록(주장, 발언, 표, 감금, 실종과 피해 장소·시간대, 고백) | O | O | O | O |
| 무당의 실제 점 결과 | O(자기 것) | X | X | 복기에 공개 |
| 자기 행적·목격·비밀(③) | - | O(자기 것) | O(관찰자 자기 것) | 복기에 공개 |
| 짝 ID·NightPlan | X | 씐 자만 | 씐 자 관찰자만 | 복기에 공개 |
| NPC 믿음(④) | X | 자기 것 | O(관찰자 자기 것) | 복기에 이유 코드만 |
| 진실(①: 역할, 비밀 배정, 주장의 참·거짓) | X | X | **X** | `truth_revealed` 이벤트 |

- 감금 공개: 갇힌 사람이 씐 자였는지는 공개한다(`LockRecord.revealed_possessed`). 비밀 보유 여부는 공개하지 않는다.
- 진실(`GameTruth`)은 판 문서 안의 별도 필드에 둔다. 화면용 직렬화와 관측 스냅샷에는 절대 넣지 않는다.

## 8. 11인 판 회의 발언 축약 (A 구현)
- 활동 인원이 9명 이상인 라운드에만 적용한다. 원본 `Speech`는 공개 기록에 전부 남긴다. 축약은 `PlayerView.speech_groups`로 표시 단위만 묶는다.
- 그대로 남기는 발언:
  - 무당을 대상으로 하거나 무당에게 하는 발언
  - 처음 나온 (발언자, 대상, 근거) 조합
  - 감싸기
  - 모순 지적
  - 고백
- 묶는 발언:
  - 근거가 새롭지 않은 같은 대상 의심. 예: "훈장·머슴 외 2명이 약초꾼을 의심"
  - 반복 해명
  - 침묵
- 순서: 실행 순서는 바꾸지 않는다(`turn_order`). 묶음은 그 묶음의 첫 발언 위치에 둔다.

## 9. 판 밖 상태 (`PlayerProgress`)
- 원한 단서는 비밀 고백과 같은 해금 조건이 충족될 때 `add_clue`로 얻는다. 같은 단서를 다시 얻으면 무시한다(False). 처음 얻은 판과 날만 기록한다.
- 같은 `hypothesis_id`의 단서 세 부분(identity, death, target)이 모두 모이면 `endings_unlocked`에 `"haewon:<hypothesis_id>"`를 추가한다. 서로 다른 가설의 부분은 합치지 않는다. `"exorcism"`은 처음부터 열려 있다(기본값).
- 결말은 판 시작 때 고른다(`StartGameInput.ending_choice` → `GameMeta.ending_choice`). 열리지 않은 결말을 고르면 400을 반환한다. 결말은 승패에 영향이 없고, 종료 연출과 복기 해설만 바뀐다.
- 저장은 `ProgressRepository`의 원자 연산(`add_clue`, `unlock_ending`)으로 한다. 원천 이벤트 ID로 멱등성을 보장한다. 진척 저장 전에 장애가 나면 아웃박스의 `confession`과 `game_ended` 이벤트를 재처리해 복구한다.
- 프리셋별 최소 콘텐츠는 `required_counts(preset)`로 정한다. 주민 풀은 `n_active` 이상이어야 한다. 비밀은 `n_secret` 이상이어야 하며, 배정 검증기를 통과하는 조합이 있어야 한다.
- 콘텐츠 버전: 판은 시작할 때 `content_version`을 고정한다(`GameMeta`). 진행 중인 판은 끝날 때까지 그 버전으로 진행한다. 따라서 `ContentSource`는 진행 중인 판이 쓰는 이전 버전을 계속 제공해야 한다. 판 밖 상태는 `clue_id`로 쌓이므로, 단서 ID는 버전이 바뀌어도 유지한다(삭제 금지, 폐기 표시만 허용).

## 10. 배정 검증기 (`validate_assignment`)
판 시작 시 엔진이 시드로 만든 배정(`Assignment`)을 검사한다. 위반이 나오면 다른 시드로 다시 뽑는다. 위반 코드는 다음과 같다.
- **인원·역할**:
  - `resident_count`, `resident_duplicate`, `unknown_character`
  - `role_coverage`, `possessed_count`, `mudang_in_residents`
  - `night0_victim`: 피해자는 마을 주민이어야 한다.
- **비밀**:
  - `secret_count`, `unknown_secret`
  - `secret_on_non_villager`: 씐 자에게 비밀을 주지 않는다.
  - `secret_on_victim`, `secret_not_eligible`, `secret_false_equals_actual`, `secret_conflict`
  - `secret_whereabouts_mismatch`: 비밀 시간대의 실제 행적은 비밀의 실제 장소여야 한다.
- **행적·홀림**:
  - `whereabouts_incomplete`, `executor_not_possessed`, `plan_victim_mismatch`
  - `executor_not_at_victim`: 실행자는 한밤에 피해자와 같은 장소에 있어야 한다.
- **목격 조합**:
  - `witness_at_crime`: 피해 시각 피해 장소(공공장소)에 실행자와 피해자 외의 **마을 주민**이 있으면 위반이다. 공범은 예외다. **기획서 대비 결정**: 기획서는 같은 장소라도 확률적으로 목격한다고만 정했다. 하지만 범행 장소에 마을 주민이 함께 있으면, 목격 여부와 관계없이 그 주민의 진술이 결정적 단서가 될 위험이 크다. 그래서 동선 단계에서 배제한다. 시뮬레이션에서 난이도가 너무 올라가면 이 제약을 확률 목격으로 완화한다.
- **2명 판 알리바이**: `alibi_by_non_possessed`, `executor_alibi_is_truth`

예시는 `fixtures/assignment/`에 있다. 정상 배정 2개와, 충돌 사례 7개(각각 기대 위반 코드 포함)다.
