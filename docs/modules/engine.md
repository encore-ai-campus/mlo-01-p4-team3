# engine — 게임 엔진(판 생성·회의·캐묻기·투표·밤·종료 판정)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

`engine/ports.py`의 `GameEngine` 포트를 구현한다. 입력은 판 메타(`GameMeta`)·현재 상태(`GameState`)·진실(`GameTruth`)·플레이어 명령(`CommandEnvelope`)이고, 출력은 수락 여부·새 상태·진실·이벤트·렌더 요청 또는 거부 코드(`TransitionResult`)다. 순수 함수형이어서 입력 객체를 바꾸지 않고, 같은 시드·같은 명령이면 같은 결과를 낸다.

| 파일 | 계층 | 하는 일 |
|---|---|---|
| `engine/domain/engine.py` | 도메인 | `PaldoEngine`(`new_game`, `new_game_from_setup`, `apply`, `player_view`), 배정·아침 진술, 명령 검증(`validate_command`, `legal_player_kinds`), 투표·감금·종료 판정(`end_reason`), 최소 밤 처리, NPC 결정 자리(`NpcPolicy`, 기본 `BaselinePolicy`) |
| `engine/domain/params.py` | 도메인 | 밸런스 상수(`RULE_PARAMS_VERSION`, `WITNESS_PROB`, `ASSIGNMENT_MAX_ATTEMPTS`) |
| `engine/domain/model.py`·`events.py`·`serde.py`·`content.py`, `engine/ports.py` | 도메인(계약) | 타입·`PHASE_RULES`·`LIMITS`·이벤트·직렬화·포트. 바꾸려면 계약 변경 절차(`docs/contracts/parallel_dev.md` 4절) |
| `engine/testing/fakes.py` | 테스트용 가짜 | `ScriptedGameEngine` 등. B-05-02가 같은 포트로 `PaldoEngine`과 갈아 끼운다 |
| `tools/perf/engine_bench.py` | 표현·응용 | `new_game`·`apply` 1회 시간 측정, 결과 JSON 기록 |

의존 방향: `tools/` → `engine/domain/`. `engine/domain/`은 표준 라이브러리만 쓰고 `server`·`ads`·`ml`·`llm`·`pipeline`·`intent`·`contracts`를 import하지 않는다(`tests/contract/test_layers.py`).

## 왜

- 규칙 근거: 기획서 4.1(프리셋), 4.3~4.5(회의 라운드·캐묻기·투표·감금 공개·종료 판정), 5.1~5.2(주장 스키마·거짓 행적). 계약은 `docs/contracts/domain.md` 3절(`PHASE_RULES`), 4절(`LIMITS`), 5절(투표·밤 순서).
- `apply`가 플레이어 명령 1개를 처리한 뒤 다음 플레이어 결정 지점까지 NPC 차례를 자동 진행하는 이유: 서버(B)가 요청 1번에 상태 1번만 저장하게 하려고(포트 docstring).
- 거부 때 상태를 바꾸지 않는 이유: 서버가 `state_version`으로 낙관적 잠금을 하므로, 거부된 명령이 버전을 올리면 다음 정상 명령이 충돌한다.
- 종료 판정 순서 G=0 → 무당 감금 → G≥V+M: 마지막 씐 자 감금과 동수 도달이 겹치면 마을 승리가 먼저다(domain.md 5절).
- NPC 결정을 `NpcPolicy`로 분리한 이유: 규칙 진행(엔진)과 NPC 판단(A-07-01 발언, A-07-02 투표)을 따로 바꾸고, 테스트·녹화 재생은 정해진 결정을 끼우려고.
- `new_game_from_setup`을 나눈 이유: 시나리오 파일의 고정 배정을 그대로 재현해 실제 엔진으로 재생하려고.

## 사용법

```
python -m unittest discover -s tests/engine -t .                 # 엔진 테스트(판 생성·시나리오 재생·거부·투표·종료)
python -m tools.perf.engine_bench --op new_game --n 1000          # 판 생성 p95, 결과 JSON 기록
python -m tools.perf.engine_bench --op apply --n 1000             # apply 1회 p95(base6, 판 생성 시간 제외)
python -m tools.perf.engine_bench --op apply --n 1000 --preset duo11 --no-write   # 다른 프리셋, 기록 없이
```

코드에서:

```python
from engine.domain.engine import PaldoEngine
eng = PaldoEngine()                         # policy=None → BaselinePolicy
res = eng.new_game(meta, deps)              # deps = EngineDeps(content, suspicion)
res2 = eng.apply(res.state, res.truth, envelope, deps)  # 거부면 res2.accepted=False, res2.reject·reject_detail. 입력 상태는 그대로 쓴다
view = eng.player_view(res2.state, deps.content)
```

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `RULE_PARAMS_VERSION`(`params.py`) | `rule-0.2` | 규칙 파라미터 버전. 판 메타에 들어가 재현에 쓴다 | 상수를 바꾸면 반드시 올린다 |
| `WITNESS_PROB`(`params.py`) | 0.7 | 같은 시간대·공공장소 두 사람이 서로 목격할 확률 | 단서 양·승률이 바뀐다(A-08-03 조정) |
| `ASSIGNMENT_MAX_ATTEMPTS`(`params.py`) | 200 | 배정 검증 실패 때 재추첨 최대 횟수 | 넘으면 콘텐츠 부족 오류 |
| `LIMITS`(`model.py`, 계약) | 캐묻기 하루 2, 점 공개 하루 1, 라운드당 발언 1 | 횟수 제한 | 계약 변경 절차 필요 |
| `PaldoEngine(policy=…)` | `BaselinePolicy` | NPC 발언·투표·밤 계획 결정 규칙 | 같은 시드라도 NPC 행동이 바뀐다 |
| `engine_bench --preset` | `base6` | 측정 프리셋 | 인원이 많을수록 `apply` 시간이 늘어난다 |

## 한계

- 밤 처리는 최소 구현이다: 상태 고정 → 행적 → 실종 → 종료 판정 → 점 결과. 밤 증언·목격·고백·말버릇(tell)은 A-06-01 범위라 2일째 아침 진술 목록은 비어 있다.
- `observe`와 `truth_revealed` 이벤트는 A-06-02에서 구현한다(지금은 `NotImplementedError`).
- 캐묻기 반응은 항상 `EXPLAIN`이다. 반응 선택은 A-06-01·A-07-01 범위.
- `BaselinePolicy`는 회의에서 침묵하고, 의심도 최고 후보에 투표(동률은 시드), 밤 피해자는 시드로 고른다. NPC 정책은 A-07-01·A-07-02가 바꾼다.
- 이벤트 순서는 domain.md대로 `night_resolved` → `divination_done`이다. 녹화 판(`normal_game.json`)은 반대 순서다.
- 실제 엔진은 주장·발언·렌더 작업 ID를 스스로 매기므로 녹화 판의 ID와 다르다. ID나 발언 순서를 녹화와 직접 비교하는 B 쪽 테스트는 교체 때 맞춰야 할 수 있다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| A-05-02 | 11/5 | `apply`(회의 라운드·캐묻기·동시 비공개 투표·결선·재동점 무감금·감금 공개·종료 판정·최소 밤), `player_view`, 명령 검증, `NpcPolicy`·`BaselinePolicy`, `new_game_from_setup`·`setup_from_assignment`, `engine_bench --op apply` |
