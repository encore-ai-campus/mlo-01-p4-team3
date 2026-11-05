# 병렬 개발 계약

목표: A, B, C, D, E 다섯 흐름이 11/5부터 서로를 기다리지 않고 개발한다. 일정과 인수 시점은 `../team_plan.md` 3~5절에 있다. 배포 대상은 기획서 6장 VM 배치를 참고한다.

## 1. 흐름별 시작 세트

| 흐름 | 바로 쓰는 것 | 지켜야 할 계약 |
|---|---|---|
| A 엔진 | `FixtureContentSource`, `FakeSuspicionModel`, `fixtures/scenarios`, `fixtures/assignment` | `GameEngine`(ports.md), domain.md 전체 |
| B 웹·API | `ScriptedGameEngine`, `InMemoryGameRepository`, `FakeIntentParser`, `FakeLineRenderer`, `fixtures/api` | api.md, `server/app/dto.py`, `server/api/viewmodels.py` |
| C 인프라/파이프라인 | `fixtures/events`, `tools/gen_synthetic.py`, `InMemoryGameRepository`(기준 동작) | ports.md 2절(원자 저장), events.md |
| D ML | `fixtures/ml`, `fixtures/intent`, `tools/gen_synthetic.py`, `FakeSuspicionModel`(출력 형식) | ports.md 3절, intent.md, ml_lifecycle.md |
| E 콘텐츠·LLM | `fixtures/content_min`, `fixtures/llm`, `FakeLineRenderer` | content.md, ports.md 4절, privacy.md |
| D 광고(ads/ 전체) | `fixtures/ads`, `tools/gen_synthetic.py --ads`, `ads/domain`(기준 규칙) | ads.md, events.md 5절 |
| B 광고 지면 | `FakeAdProvider`, `AdGateway` | ports.md 5절 |
| C 광고 인프라 | `fixtures/ads`, 합성 광고 이벤트 | ads.md 5·7절(원장, 아웃박스, 대사), events.md 5절 |

담당은 확정됐다(1안). 결정 층 최적화는 A(엔진)가 맡는다. 광고 서버 전체(`ads/domain`, `ads/app`, `ads/infra`, `ads/web`)는 D가 맡는다. C는 광고 DB 복제, `ad.*` 토픽, Spark 광고 집계, 대시보드를 맡는다.

## 2. 계약 시나리오 (`fixtures/scenarios/`)
각 파일에는 입력(setup, steps)과 기대 결과(checks)가 들어 있다. 지금은 `tests/contract/test_scenarios.py`가 기준 규칙 함수와 가짜 구현으로 checks를 평가한다. A의 엔진이 나오면 같은 steps를 엔진으로 재생해 같은 기대값을 확인하는 테스트를 A가 추가한다.

| 시나리오 | 확인하는 것 |
|---|---|
| normal_game | 기본 6인 판의 정상 진행(녹화 판과 같은 판), 종료 판정, 단계별 합법 명령 |
| secret_confession | 고백 전후 점괘, 모순 추궁 필수 슬롯, 원한 단서 중복 무시 |
| false_divination_reveal | 거짓 점괘 공개의 합법성, 공개값과 실제값 분리, 하루 1회 제한 |
| duo_accomplice_alibi | 창귀 2명 판의 알리바이 명제("실행자 아님"까지만), 2명 판 종료 판정 |
| runoff_tie_no_lock | 본투표 동점 → 결선 동점 → 무감금, 밤 진행 |
| mudang_locked | 무당 감금 시 인원과 무관한 창귀 승리 |
| last_possessed_locked | 마지막 씐 자 감금 시 즉시 종료, 밤 생략 |
| intent_failure_and_version_conflict | 해석 실패 상태, 다중 의도, 버전 충돌, 멱등성, 소비 없음 |

체크 종류는 다음과 같다. `evaluate()`가 기준 구현이다.
- `end_check`, `vote`, `phase_legal`, `alibi`, `divination`, `reveal_record`
- `missing_slots`, `limit`, `assignment`, `progress_clue`
- `parse`, `repo_commit`, `idempotent`

## 3. 인수 조건 (가짜 → 진짜)
- 가짜로 테스트를 통과한 것은 "착수 완료"일 뿐이다. 실제 구현으로 바꾸고 계약 테스트와 수직 통합(team_plan 7절)을 통과해야 완료로 센다.
- 11/6 A → D 인수 조건은 다음과 같다.
  - 기본 6인 판 완주
  - 행동 직전 관측 스냅샷
  - 종료 후 진실 이벤트
  - 결정 이유 코드
- 11/7 NPC 규칙 v0와 11/8 창귀 2명 규칙이 나오면 D는 학습 데이터를 다시 생성한다. 정책 버전(`npc_policy_versions`)이 다른 데이터를 한 학습에 섞지 않는다.

## 4. 규칙
- 계약 변경 절차:
  1. `engine/domain/*.py`, `engine/ports.py`, `contracts/`, `server/app/dto.py`를 고친다.
  2. `python -m tools.build_fixtures`로 픽스처를 다시 만든다.
  3. 계약 테스트를 통과시킨다.
  4. 해당 문서를 고친다.
  5. `CONTRACT_VERSION`을 올린다.
- 계약 변경은 영향받는 흐름 담당에게 알린다. 11/4 밤에 동결한 뒤에는 PM 승인을 받아야 한다.
- 각 흐름은 상대 흐름의 가짜를 자기 테스트에서 자유롭게 쓴다. 다만 가짜를 고쳐 계약을 바꾸지 않는다. 가짜 동작이 계약과 다르면 가짜를 고치는 것이 아니라 계약 논의를 한다.
- 교체 시점(team_plan 4절)에는 가짜 대신 실제 구현을 넣고, `tests/contract/`가 그대로 통과하는지 확인한다.
- 픽스처는 생성물이다. 손으로 고치지 말고 `tools/build_fixtures.py`를 고친다.
