# engine — 게임 엔진(판 생성·회의·캐묻기·투표·밤·종료 판정)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

`engine/ports.py`의 `GameEngine` 포트를 구현한다. 입력은 판 메타(`GameMeta`)·현재 상태(`GameState`)·진실(`GameTruth`)·플레이어 명령(`CommandEnvelope`)이고, 출력은 수락 여부·새 상태·진실·이벤트·렌더 요청 또는 거부 코드(`TransitionResult`)다. 순수 함수형이어서 입력 객체를 바꾸지 않고, 같은 시드·같은 명령이면 같은 결과를 낸다.

| 파일 | 계층 | 하는 일 |
|---|---|---|
| `engine/domain/engine.py` | 도메인 | `PaldoEngine`(`new_game`, `new_game_from_setup`, `apply`, `player_view`, `observe`), 배정·아침 진술, 명령 검증(`validate_command`, `legal_player_kinds`), 투표·감금·종료 판정(`end_reason`), 밤 처리(목격·다음 날 아침 진술)·점괘·고백·기색, 종료 후 진실 본문(`truth_payload`), NPC 결정 자리(`NpcPolicy`, 기본 `BaselinePolicy`) |
| `engine/domain/observation.py` | 도메인 | 관측 투영·믿음 갱신(`observe_npc`, `update_beliefs`), 스냅샷 ID(`snapshot_id_of`), 결정과 이유 코드(`Decision`, `ReasonCode`), `observation`·`decision` 이벤트 본문 |
| `engine/domain/params.py` | 도메인 | 밸런스 상수(`RULE_PARAMS_VERSION`, `ASSIGNMENT_MAX_ATTEMPTS`)와 결정 파라미터(`DecisionParams`, `WITNESS_PROB`·`TELL_PROB`·`Q_MAX`·`CONFORMITY_SCALE`·`VOTE_NOISE`·`ROUTINE_RANDOMNESS`, 탐색 도구 전용 `apply`) |
| `engine/domain/decision_params.json` | 도메인(고정 데이터) | 결정 파라미터 원본과 `DECISION_PARAMS_VERSION`(A-08-03 탐색 결과). `params.py`가 불러올 때 한 번 읽는다 |
| `engine/domain/model.py`·`events.py`·`serde.py`·`content.py`, `engine/ports.py` | 도메인(계약) | 타입·`PHASE_RULES`·`LIMITS`·이벤트·직렬화·포트. 바꾸려면 계약 변경 절차(`docs/contracts/parallel_dev.md` 4절) |
| `engine/testing/fakes.py` | 테스트용 가짜 | `ScriptedGameEngine` 등. B-05-02가 같은 포트로 `PaldoEngine`과 갈아 끼운다 |
| `tools/perf/engine_bench.py` | 표현·응용 | `new_game`·`apply` 1회 시간 측정, 결과 JSON 기록 |
| `tools/run_engine_games.py` | 표현·응용 | 실제 엔진 판 N개 실행 → `out/<경로>/events.jsonl`(`EventEnvelope` 한 줄씩)·`manifest.json`. `JsonlEventSink`는 `EventSink` 포트의 파일 구현. NPC 정책 선택지 `POLICIES` |
| `tools/check_handoff_a06.py` | 표현·응용 | 판 실행 출력 검사: 종료 1회·`seq` 연속·`serde` 엄격 검증, 11/6 A → D 인수 조건 4개 |
| `tools/optimize_decision.py` | 표현·응용 | 결정 층 최적화(A-08-03): 플레이어 대역 봇 vs 규칙 v0 판으로 목표 마을 승률 구간(`--target`)에 맞는 결정 파라미터 탐색. 격자 표본·윌슨 구간·후보 선택은 순수 함수 |

의존 방향: `tools/` → `engine/domain/`. `engine/domain/`은 표준 라이브러리만 쓰고 `server`·`ads`·`ml`·`llm`·`pipeline`·`intent`·`contracts`를 import하지 않는다(`tests/contract/test_layers.py`).

## 왜

- 규칙 근거: 기획서 4.1(프리셋), 4.3~4.5(회의 라운드·캐묻기·투표·감금 공개·종료 판정), 5.1~5.2(주장 스키마·거짓 행적). 계약은 `docs/contracts/domain.md` 3절(`PHASE_RULES`), 4절(`LIMITS`), 5절(투표·밤 순서).
- `apply`가 플레이어 명령 1개를 처리한 뒤 다음 플레이어 결정 지점까지 NPC 차례를 자동 진행하는 이유: 서버(B)가 요청 1번에 상태 1번만 저장하게 하려고(포트 docstring).
- 거부 때 상태를 바꾸지 않는 이유: 서버가 `state_version`으로 낙관적 잠금을 하므로, 거부된 명령이 버전을 올리면 다음 정상 명령이 충돌한다.
- 종료 판정 순서 G=0 → 무당 감금 → G≥V+M: 마지막 씐 자 감금과 동수 도달이 겹치면 마을 승리가 먼저다(domain.md 5절).
- NPC 결정을 `NpcPolicy`로 분리한 이유: 규칙 진행(엔진)과 NPC 판단(A-07-01 발언, A-07-02 투표)을 따로 바꾸고, 테스트·녹화 재생은 정해진 결정을 끼우려고.
- `new_game_from_setup`을 나눈 이유: 시나리오 파일의 고정 배정을 그대로 재현해 실제 엔진으로 재생하려고.
- 정책 입력을 관찰자 `ObservationSnapshot` 하나로 둔 이유: 상태 전체를 넘기면 다른 NPC의 개인 지식(역할·밤 명령)을 통해 진실이 새기 때문이다(기획서 v1.1 5.1 네 층 분리, A-06-02). 엔진은 결정 직전 `observation`, 직후 `decision`을 같은 `snapshot_id`로 `paldo.observations`에 남기고, 진실은 판 종료 뒤 `truth_revealed`(`paldo.truth`)로만 낸다(events.md 3절, 기획서 8.2).
- 기색은 공개 기록에 보인 것만 남긴다: 판정 대상이 거짓 주장뿐이라 "판정했다"는 사실 자체가 진위 정보이기 때문이다(domain.md 7절, A-06-01).
- 결정 파라미터를 json 한 파일(`decision_params.json`)에 두는 이유: 탐색 도구가 결과를 그대로 쓰고, 버전(`DECISION_PARAMS_VERSION`)이 `RULE_PARAMS_VERSION`(`rule-0.5+decision-N`)에 붙어 판 메타만으로 값을 재현하려고(A-08-03). 엔진·투표는 호출할 때마다 `params` 모듈 값을 읽으므로 탐색 도구는 프로세스 안에서 `params.apply`로 후보 값을 바꿔 판을 돌린다. 게임 웹은 `apply`를 부르지 않는다.
- 판 실행 도구가 고정 시각·시드로 같은 바이트를 내는 이유: D·C가 같은 명령으로 인수 데이터를 다시 만들고 해시로 대조하려고(A-06-03). 판 메타의 `npc_policy_versions`를 채우는 이유: D가 정책 버전이 다른 데이터를 한 학습에 섞지 않게(parallel_dev.md 3절).

## 사용법

```
python -m unittest discover -s tests/engine -t .                 # 엔진 테스트(판 생성·시나리오 재생·거부·투표·종료)
python -m tools.perf.engine_bench --op new_game --n 1000          # 판 생성 p95, 결과 JSON 기록
python -m tools.perf.engine_bench --op apply --n 1000             # apply 1회 p95(base6, 판 생성 시간 제외)
python -m tools.perf.engine_bench --op apply --n 1000 --preset duo11 --no-write   # 다른 프리셋, 기록 없이
python -m tools.run_engine_games --preset base6 --games 200 --seed 1 --out out/handoff/a06   # D·C 인수 데이터(약 14초)
python -m tools.check_handoff_a06 --dir out/handoff/a06                                      # 인수 조건 검사(약 75초), 위반이 있으면 종료 코드 1
python -m tools.optimize_decision --seed 11 --workers 8 --out out/a0803 --write-params      # 결정 층 탐색(약 4분, 목표 0.40~0.60). 미달이면 파일을 안 쓰고 종료 코드 1
python -m tools.optimize_decision --target 0.65 0.75 --workers 8 --out out/d_easy --params-out out/difficulty/easy.json --version decision-easy-1   # 다른 목표 구간(난이도별 등), 기본 파일은 그대로. 파일마다 다른 --version 필수
```

판 실행 규칙: 판 i(0부터)의 시드 = `seed * 1000000 + i`, 판 ID = `eng_<preset>_<seed>_<i:05d>`. `--policy min-legal`은 `BaselinePolicy`(정책 버전 `baseline-0`)다. `--record <태스크 ID>`를 붙이면 처리량·검사 수치를 `docs/evidence/perf/<ID>/engine_games.json`·`handoff_check.json`에 쓴다.

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
| `RULE_PARAMS_VERSION`(`params.py`) | `rule-0.5+decision-1` | 규칙 기반 버전(`RULE_BASE_VERSION`) + 결정 파라미터 버전. 판 메타에 들어가 재현에 쓴다 | 규칙 상수를 바꾸면 기반 버전을, 결정 파라미터를 바꾸면 json `version`을 올린다 |
| `WITNESS_PROB`(`decision_params.json` `witness_prob`) | 0.5 | 같은 시간대·공공장소 두 사람이 서로 목격할 확률 | 단서 양·승률이 바뀐다(A-08-03에서 0.7 → 0.5) |
| `TELL_PROB`(`tell_prob`) | 0.4 | 거짓 사실 주장에 기색이 보일 확률(주장 ID당 1회 판정) | 기색 단서 양이 바뀐다(A-08-03에서 0.5 → 0.4) |
| `Q_MAX`(`q_max`) | 0.7 | 무당 의심 결합 상한(ports.md 3) | 클수록 NPC가 무당을 의심해 마을 승률이 크게 내려간다(A-08-03에서 0.5 → 0.7) |
| `CONFORMITY_SCALE`(`conformity_scale`) | 0.1 | 동조 항 크기(성향 conformity 1.0·예상 득표 100%일 때 더하는 확률) | 0이면 동조 항이 꺼진다(탐색 격자에서 뺌) |
| `VOTE_NOISE`(`vote_noise`) | 0.02 | 결정 점수 잡음 폭(확률 ±) | 클수록 NPC 표가 흩어진다 |
| `ROUTINE_RANDOMNESS`(`routine_randomness`) | 0.0 | 동선 무작위성(같은 후보 장소 안에서 균등 분포와 섞는 비율) | 0이면 콘텐츠 동선 가중치 그대로 |
| `ASSIGNMENT_MAX_ATTEMPTS`(`params.py`) | 200 | 배정 검증 실패 때 재추첨 최대 횟수 | 넘으면 콘텐츠 부족 오류 |
| `LIMITS`(`model.py`, 계약) | 캐묻기 하루 2, 점 공개 하루 1, 라운드당 발언 1 | 횟수 제한 | 계약 변경 절차 필요 |
| `PaldoEngine(policy=…)` | `BaselinePolicy` | NPC 발언·투표·밤 계획 결정 규칙 | 같은 시드라도 NPC 행동이 바뀐다 |
| `engine_bench --preset` | `base6` | 측정 프리셋 | 인원이 많을수록 `apply` 시간이 늘어난다 |
| `run_engine_games --policy` | `min-legal` | NPC 정책(`POLICIES` 등록. A-07-01이 `rule-v0` 추가) | 정책 버전이 봉투 `npc_policy_versions`·`decision.policy_version`에 들어간다 |
| `run_engine_games --seed`·`--games` | 1·200 | 판 시드 기준·판 수 | 시드가 다르면 판 시드가 겹치지 않는다(판 수 < 1000000) |

## 한계

- 고백은 처리와 `NpcPolicy.react` 자리만 있다. 압박 누적·고백 문턱값 발동 규칙은 A-07-01 범위라 `BaselinePolicy`에서는 고백이 나오지 않는다.
- `BaselinePolicy`는 회의에서 침묵하고, 캐묻기에는 해명하고, 의심도 최고 후보에 투표(동률은 시드), 밤 피해자는 시드로 고른다. NPC 정책은 A-07-01·A-07-02가 바꾼다. 그래서 `run_engine_games --policy min-legal` 데이터는 학습용 초기 데이터이고, 정책이 바뀌면 D가 다시 만든다(parallel_dev.md 3절).
- `run_engine_games`의 무당(플레이어) 명령은 판 시드로 고정한 단순 선택(`bench_player_command`: 캐묻기 1회·의심·무작위 투표·무작위 점)이다. 플레이어 결정 이벤트는 남기지 않는다. 의심 모델은 가짜(`FakeSuspicionModel`)다.
- `occurred_at`은 실제 시각이 아니라 고정 기준 시각(2026-11-06T00:00Z) + 판 순번 분 + 이벤트 순번 밀리초다. 시간 창 집계에는 쓰지 않는다.
- `check_handoff_a06`은 관측 스냅샷을 모두 엄격 역직렬화해 200판에 약 75초 걸린다(스냅샷 1개 약 12 ms).
- 실제 게임에서 `npc_policy_versions`를 채우는 일은 판 시작 유스케이스(B) 몫이다. 지금은 판 실행 도구만 채운다.
- 이벤트 순서는 domain.md대로 `night_resolved` → `divination_done`이다. 녹화 판(`normal_game.json`)은 반대 순서다.
- 실제 엔진은 주장·발언·렌더 작업 ID를 스스로 매기므로 녹화 판의 ID와 다르다. ID나 발언 순서를 녹화와 직접 비교하는 B 쪽 테스트는 교체 때 맞춰야 할 수 있다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| A-05-02 | 11/5 | `apply`(회의 라운드·캐묻기·동시 비공개 투표·결선·재동점 무감금·감금 공개·종료 판정·최소 밤), `player_view`, 명령 검증, `NpcPolicy`·`BaselinePolicy`, `new_game_from_setup`·`setup_from_assignment`, `engine_bench --op apply` |
| A-06-01 | 11/6 | 밤 목격·다음 날 아침 진술, 점괘 공개 분리 저장, 고백 처리 자리, 기색(보인 것만 공개, `TELL_PROB`, rule-0.3) |
| A-06-02 | 11/6 | `observation.py`(관측 투영·믿음 갱신·이유 코드), 정책 입력을 `ObservationSnapshot`으로, `observe` 구현, 결정 직전 `observation`·직후 `decision`(`paldo.observations`), 종료 후 `truth_revealed` |
| A-06-03 | 11/6 | `tools/run_engine_games.py`(실제 엔진 판 → jsonl·manifest, `--policy min-legal`, `npc_policy_versions` 채움), `tools/check_handoff_a06.py`(인수 조건 4개 검사) |
| A-08-03 | 11/8 | 결정 파라미터를 `decision_params.json`(decision-1)으로 분리·호출 시점 읽기, `routine_randomness` 추가, `tools/optimize_decision.py`(목표 승률 구간 인자), rule-0.5. 논리 우선 봇 vs 규칙 v0 1000판 마을 승률 49.6% |
