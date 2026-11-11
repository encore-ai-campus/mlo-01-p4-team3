# engine — 게임 엔진(판 생성·회의·캐묻기·투표·밤·종료 판정)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

`engine/ports.py`의 `GameEngine` 포트를 구현한다. 입력은 판 메타(`GameMeta`)·현재 상태(`GameState`)·진실(`GameTruth`)·플레이어 명령(`CommandEnvelope`)이고, 출력은 수락 여부·새 상태·진실·이벤트·렌더 요청 또는 거부 코드(`TransitionResult`)다. 순수 함수형이어서 입력 객체를 바꾸지 않고, 같은 시드·같은 명령이면 같은 결과를 낸다.

| 파일 | 계층 | 하는 일 |
|---|---|---|
| `engine/domain/engine.py` | 도메인 | `PaldoEngine`(`new_game`, `new_game_from_setup`, `apply`, `player_view`, `observe`), 배정·아침 진술, 명령 검증(`validate_command`, `legal_player_kinds`), 투표·감금·종료 판정(`end_reason`), 밤 처리(목격·다음 날 아침 진술)·점괘·고백·기색, 종료 후 진실 본문(`truth_payload`), NPC 결정 자리(`NpcPolicy`, 기본 `BaselinePolicy`) |
| `engine/domain/observation.py` | 도메인 | 관측 투영·믿음 갱신(`observe_npc`, `update_beliefs`), 스냅샷 ID(`snapshot_id_of`), 결정과 이유 코드(`Decision`, `ReasonCode`), `observation`·`decision` 이벤트 본문 |
| `engine/domain/npc/beliefs.py` | 도메인 | 규칙 v0 믿음 ④: 공개 기록에서 대상별 의심도·무당 신뢰도·확정 명제(직접 목격만)·압박도·고백 문턱값을 매번 다시 계산(`compute_beliefs`), 마을 추론 위반 판정(`vote_conflicts`) |
| `engine/domain/npc/policy_villager.py`·`policy_possessed.py` | 도메인 | 마을 주민 정책(말·투표·캐묻기 답과 고백), 씐 자 전략(거짓 행적·누명·밤 대상·무당 의심 유도, 혼합 비율 `StrategyMix`를 `MixSource`로 판마다 1회 주입) |
| `engine/domain/npc/vote.py` | 도메인 | 마을 주민 투표 3층(의심 모델 추정 → 성향 가중·동조·잡음 결정 → 안전 층), 무당 결합 q, 모델 실패·100ms 초과·버전 불일치·계약 위반 출력 폴백, 폴백 횟수(`fallback_count`) |
| `engine/domain/npc/__init__.py` | 도메인 | `RuleV0Policy`(정책 버전 `rule-v0`): 관찰자 역할로 주민 정책·씐 자 전략을 나눠 부른다 |
| `engine/domain/params.py` | 도메인 | 밸런스 상수(`RULE_PARAMS_VERSION`, `ASSIGNMENT_MAX_ATTEMPTS`)와 결정 파라미터(`DecisionParams`, `WITNESS_PROB`·`TELL_PROB`·`Q_MAX`·`CONFORMITY_SCALE`·`VOTE_NOISE`·`ROUTINE_RANDOMNESS`, 탐색 도구 전용 `apply`) |
| `engine/domain/decision_params.json` | 도메인(고정 데이터) | 결정 파라미터 원본과 `DECISION_PARAMS_VERSION`(A-08-03 탐색 결과). `params.py`가 불러올 때 한 번 읽는다 |
| `engine/domain/model.py`·`events.py`·`serde.py`·`content.py`, `engine/ports.py` | 도메인(계약) | 타입·`PHASE_RULES`·`LIMITS`·이벤트·직렬화·포트. 바꾸려면 계약 변경 절차(`docs/contracts/parallel_dev.md` 4절) |
| `engine/testing/fakes.py` | 테스트용 가짜 | `ScriptedGameEngine` 등. B-05-02가 같은 포트로 `PaldoEngine`과 갈아 끼운다 |
| `tools/perf/engine_bench.py` | 표현·응용 | `new_game`·`apply` 1회 시간 측정, 결과 JSON 기록 |
| `tools/run_engine_games.py` | 표현·응용 | 실제 엔진 판 N개 실행 → `out/<경로>/events.jsonl`(`EventEnvelope` 한 줄씩)·`manifest.json`. `JsonlEventSink`는 `EventSink` 포트의 파일 구현. NPC 정책 선택지 `POLICIES` |
| `tools/check_handoff_a06.py` | 표현·응용 | 판 실행 출력 검사: 종료 1회·`seq` 연속·`serde` 엄격 검증, 11/6 A → D 인수 조건 4개 |
| `tools/optimize_decision.py` | 표현·응용 | 결정 층 최적화(A-08-03): 플레이어 대역 봇 vs 규칙 v0 판으로 목표 마을 승률 구간(`--target`)에 맞는 결정 파라미터 탐색. 격자 표본·윌슨 구간·후보 선택은 순수 함수 |
| `engine/domain/progress.py` | 도메인 | 판 밖 진척 규칙(E-09-08): 고백 이벤트 → 원한 단서(`clue_gains`), 같은 가설 세 부분 → 한풀이 해금(`completed_hypotheses`·`ending_unlocks`), 결말 선택 가능 여부(`ending_available`, 퇴마는 항상), 재처리 대상 이벤트(`progress_events`). 반영 순서는 `server/app/progress.py` |
| `engine/domain/review.py` | 도메인 | 복기용 이유 조회(E-10-10): 아웃박스 `decision` 이벤트 → 결정 단위 항목(`review_trace`, 종류별 발언 연결), 대표 이유 코드·문구 ID(`phrase_id_for`), 템플릿 해설(`review_text`, 괄호 조사 정리 `attach_josa`), 빠진 문구 검사(`missing_phrases`). 문구 파일은 콘텐츠 `content/review_lines_v1.json`, 저장소 읽기는 포트 `GameRepository.events` |
| `tools/perf/challenge_balance.py` | 표현·응용 | 선택형 도전 3종(`base6_div1`·`base6_secret3`·`base6_sight`)과 base6을 같은 판 시드로 돌려 봇별 마을 승률·윌슨 구간·검사 실패를 재고 P-08 공개 판정 |
| `tools/perf/npc_rule_check.py` | 표현·응용 | `--events`: jsonl의 결정마다 관측과 맞춰 합법 행동 위반·마을 추론 위반·의심 모델 폴백 결정을 센다. `--bench`: NPC 1명 결정 시간 |

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
- 결정 파라미터를 json 한 파일(`decision_params.json`)에 두는 이유: 탐색 도구가 결과를 그대로 쓰고, 버전(`DECISION_PARAMS_VERSION`)이 `RULE_PARAMS_VERSION`(`rule-0.8.decision-N`, 연결 문자는 `.`)에 붙어 판 메타만으로 값을 재현하려고(A-08-03). 판 메타가 `rule-0.7` 이하인 판은 고정값 `params.LEGACY_DECISION`(decision-1)을 쓴다: 목격·기색 확률은 엔진 `decision_of`, q_max는 정책 `RuleV0Policy(q_max=...)`(운영 조립 `ContentPolicyEngine`이 이전 판용 정책을 따로 만든다)(A-10-01 1번). 엔진·투표는 호출할 때마다 `params` 모듈 값을 읽으므로 탐색 도구는 프로세스 안에서 `params.apply`로 후보 값을 바꿔 판을 돌린다. 게임 웹은 `apply`를 부르지 않는다.
- 규칙 v0 믿음을 공개 기록에서 매번 다시 계산하는 이유: 스냅샷 계약에 압박도·성향이 없고, 같은 스냅샷이면 같은 믿음이 나와야 관측 기록과 실제 판단이 일치한다(A-07-01). 확정 명제를 직접 목격에서만 만드는 이유: 타인 주장은 거짓일 수 있고 "못 봤다"는 없음의 증거가 아니다(기획서 5.2).
- 투표를 추정·결정·안전 세 층으로 나눈 이유: 추정(의심 모델)은 D가 바꾸고, 성향·동조는 엔진 규칙이며, 안전 층은 어떤 모델이 와도 확정 무고에게 표를 주지 않게 한다(기획서 5.2, ports.md 3). 모델이 실패해도 판이 멈추지 않게 그 결정만 규칙 v0로 대신하고 `-fallback`으로 표시한다(A-07-02).
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

```
python -m tools.run_engine_games --preset base6 --games 500 --seed 2 --policy rule-v0 --out out/a0702   # 규칙 v0 판(투표는 가짜 의심 모델 3층)
python -m tools.perf.npc_rule_check --events out/a0702/events.jsonl                                     # 합법·마을 추론 위반, 폴백 결정 수
python -m tools.perf.npc_rule_check --bench --preset base6 --games 200 --seed 2                         # NPC 결정 시간
```

판 실행 규칙: 판 i(0부터)의 시드 = `seed * 1000000 + i`, 판 ID = `eng_<preset>_<seed>_<i:05d>`. `--policy min-legal`은 `BaselinePolicy`(정책 버전 `baseline-0`), `--policy rule-v0`은 `RuleV0Policy`다. `--record <태스크 ID>`를 붙이면 처리량·검사 수치를 `docs/evidence/perf/<ID>/engine_games.json`·`handoff_check.json`에 쓴다.

코드에서:

```python
from engine.domain.engine import PaldoEngine
eng = PaldoEngine()                         # policy=None → BaselinePolicy. 규칙 v0: PaldoEngine(RuleV0Policy(content, mix_source))
res = eng.new_game(meta, deps)              # deps = EngineDeps(content, suspicion)
res2 = eng.apply(res.state, res.truth, envelope, deps)  # 거부면 res2.accepted=False, res2.reject·reject_detail. 입력 상태는 그대로 쓴다
view = eng.player_view(res2.state, deps.content)
n = fallback_count(res2.events)             # engine.domain.npc.vote. B가 paldo_engine_suspicion_fallback_total을 n만큼 올린다
```

## 설정값

공통 역직렬화는 정적인 dataclass 타입별 주석 해석·필드 정보를 프로세스 안에서 최대 256종 재사용한다. JSON 값·판 상태·진실·검증 결과는 캐시하지 않으며, 매 입력의 필드·타입·생성자 검증과 `SchemaError`는 그대로 실행한다. JSON 스키마·공개 함수·계약 버전은 바뀌지 않는다.

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `RULE_PARAMS_VERSION`(`params.py`) | `rule-0.8.decision-2` | 규칙 기반 버전(`RULE_BASE_VERSION`) + 결정 파라미터 버전. 판 메타에 들어가 재현에 쓴다 | 규칙 상수를 바꾸면 기반 버전을, 결정 파라미터를 바꾸면 json `version`을 올린다 |
| `WITNESS_PROB`(`decision_params.json` `witness_prob`) | 0.5 | 같은 시간대·공공장소 두 사람이 서로 목격할 확률 | 단서 양·승률이 바뀐다(A-08-03에서 0.7 → 0.5) |
| `TELL_PROB`(`tell_prob`) | 0.4 | 거짓 사실 주장에 기색이 보일 확률(주장 ID당 1회 판정) | 기색 단서 양이 바뀐다(A-08-03에서 0.5 → 0.4) |
| `Q_MAX`(`q_max`) | 0.7 | 무당 의심 결합 상한(ports.md 3) | 클수록 NPC가 무당을 의심해 마을 승률이 크게 내려간다(A-08-03에서 0.5 → 0.7) |
| `CONFORMITY_SCALE`(`conformity_scale`) | 0.1 | 동조 항 크기(성향 conformity 1.0·예상 득표 100%일 때 더하는 확률) | 0이면 동조 항이 꺼진다(탐색 격자에서 뺌) |
| `VOTE_NOISE`(`vote_noise`) | 0.02 | 결정 점수 잡음 폭(확률 ±) | 클수록 NPC 표가 흩어진다 |
| `ROUTINE_RANDOMNESS`(`routine_randomness`) | 0.0 | 동선 무작위성(같은 후보 장소 안에서 균등 분포와 섞는 비율) | 0이면 콘텐츠 동선 가중치 그대로 |
| `ASSIGNMENT_MAX_ATTEMPTS`(`params.py`) | 200 | 배정 검증 실패 때 재추첨 최대 횟수 | 넘으면 콘텐츠 부족 오류 |
| `Q_MAX`(`params.py`) | 0.5 | 무당 의심 결합 상한. q = Q_MAX × (100 − trust)/100, 주민 후보 (1 − q) × p(ports.md 3) | 클수록 마을 주민이 무당에게 투표한다(A-08-03 조정) |
| `SUSPICION_TIMEOUT_S`(`npc/vote.py`) | 0.1 | 의심 모델 호출 시간 초과(ports.md 3, 100ms) | 계약 값. 바꾸려면 ports.md와 함께 |
| `CONFORMITY_SCALE`·`VOTE_NOISE_P`(`npc/vote.py`) | 0.2·0.02 | 투표 결정 층의 동조 항 크기·잡음 폭(확률 단위, 잠정) | 표가 모이는 정도가 바뀐다(A-08-03 조정) |
| 규칙 v0 상수(`npc/beliefs.py` `W`·`P`, `npc/policy_villager.py` `SUSPECT_MIN` 등) | 잠정값 | 근거별 의심도 가중치·압박도·의심 발언 문턱 | 근거 없는 잠정값이다(A-08-03 조정) |
| `StrategyMix`(`npc/policy_possessed.py`) | 모든 팔 1.0(균등) | 씐 자 전략 혼합 비율(lie_type·frame_target·answer_intent·night_target) | D-09-02 밴딧이 `MixSource`로 판마다 주입 |
| `LIMITS`(`model.py`, 계약) | 캐묻기 하루 2, 점 공개 하루 1, 라운드당 발언 1 | 횟수 제한 | 계약 변경 절차 필요 |
| `PaldoEngine(policy=…)` | `BaselinePolicy` | NPC 발언·투표·밤 계획 결정 규칙 | 같은 시드라도 NPC 행동이 바뀐다 |
| `engine_bench --preset` | `base6` | 측정 프리셋 | 인원이 많을수록 `apply` 시간이 늘어난다 |
| `run_engine_games --policy` | `min-legal` | NPC 정책(`POLICIES` 등록. A-07-01이 `rule-v0` 추가) | 정책 버전이 봉투 `npc_policy_versions`·`decision.policy_version`에 들어간다 |
| `run_engine_games --seed`·`--games` | 1·200 | 판 시드 기준·판 수 | 시드가 다르면 판 시드가 겹치지 않는다(판 수 < 1000000) |

## 한계

- `BaselinePolicy`는 회의에서 침묵하고, 캐묻기에는 해명하고, 의심도 최고 후보에 투표(동률은 시드), 밤 피해자는 시드로 고른다. 고백은 나오지 않는다. 그래서 `run_engine_games --policy min-legal` 데이터는 학습용 초기 데이터이고, 정책이 바뀌면 D가 다시 만든다(parallel_dev.md 3절).
- 의심 모델 100ms 시간 초과는 호출이 끝난 뒤 경과 시간으로 판정해 결과를 버릴 뿐, 느린 호출 자체는 끊지 못한다. 응답하지 않는 호출의 중단은 어댑터(D-08-01)가 자기 시간 제한으로 막는다(11/5 PM 결정). 시간 초과 폴백은 기계 부하에 따라 달라지므로 그 결정만은 재현되지 않을 수 있다.
- 씐 자의 투표는 의심 모델을 부르지 않고 A-07-01 전략을 쓴다(모델 확률은 "씐 자일 확률"이라 씐 자에게는 쓸모가 없다). 폴백 표시는 마을 주민 투표에만 붙는다.
- 동조 항의 예상 득표는 오늘 회의에서 말 차례를 쓴 사람(침묵 포함)과 무당을 투표자로 본다. 스냅샷에 활동 명단이 없어서다.
- 규칙 v0 가중치·동조 크기·Q_MAX는 근거 없는 잠정값이다. 가짜 의심 모델을 넣은 base6 500판에서 마을 승률이 약 77%로 목표 40~60%를 벗어난다(A-07-02 증빙, 조정은 A-08-03).
- `Place.HOME`은 누구 집인지 구분하지 않는다. `rule-0.7`부터 피해 장소는 늘 공개 장소라 새 판에는 해당하지 않는다. 이전 규칙 판에서 피해 장소가 피해자의 집일 때 실행자의 "집에 있었다"가 참으로 기록된다. 지금 이 문장을 내는 정책은 없다(트러블 A, A-07-01).
- 폴백 횟수는 엔진이 이벤트(`decision.policy_version` 끝 `-fallback`)로만 남긴다. `TransitionResult`에 카운트 필드는 없고, 응용 계층이 `fallback_count`로 센다.
- `run_engine_games`의 무당(플레이어) 명령은 판 시드로 고정한 단순 선택(`bench_player_command`: 캐묻기 1회·의심·무작위 투표·무작위 점)이다. 플레이어 결정 이벤트는 남기지 않는다. 의심 모델은 가짜(`FakeSuspicionModel`)다.
- `occurred_at`은 실제 시각이 아니라 고정 기준 시각(2026-11-06T00:00Z) + 판 순번 분 + 이벤트 순번 밀리초다. 시간 창 집계에는 쓰지 않는다.
- `check_handoff_a06`은 관측 스냅샷을 모두 엄격 역직렬화해 200판에 약 75초 걸린다(스냅샷 1개 약 12 ms).
- 실제 게임에서 `npc_policy_versions`를 채우는 일은 판 시작 유스케이스(B) 몫이다. 지금은 판 실행 도구만 채운다.
- 이벤트 순서는 domain.md대로 `night_resolved` → `divination_done`이다. 녹화 판(`normal_game.json`)은 반대 순서다.
- 실제 엔진은 주장·발언·렌더 작업 ID를 스스로 매기므로 녹화 판의 ID와 다르다. ID나 발언 순서를 녹화와 직접 비교하는 B 쪽 테스트는 교체 때 맞춰야 할 수 있다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-08-01 | 11/10 | 부하 병목 개선: `serde`의 타입 메타데이터만 제한된 캐시로 재사용. 입력별 엄격 검증·객체 격리·공개 계약 유지 |
| A-05-02 | 11/5 | `apply`(회의 라운드·캐묻기·동시 비공개 투표·결선·재동점 무감금·감금 공개·종료 판정·최소 밤), `player_view`, 명령 검증, `NpcPolicy`·`BaselinePolicy`, `new_game_from_setup`·`setup_from_assignment`, `engine_bench --op apply` |
| A-06-01 | 11/6 | 밤 목격·다음 날 아침 진술, 점괘 공개 분리 저장, 고백 처리 자리, 기색(보인 것만 공개, `TELL_PROB`, rule-0.3) |
| A-06-02 | 11/6 | `observation.py`(관측 투영·믿음 갱신·이유 코드), 정책 입력을 `ObservationSnapshot`으로, `observe` 구현, 결정 직전 `observation`·직후 `decision`(`paldo.observations`), 종료 후 `truth_revealed` |
| A-06-03 | 11/6 | `tools/run_engine_games.py`(실제 엔진 판 → jsonl·manifest, `--policy min-legal`, `npc_policy_versions` 채움), `tools/check_handoff_a06.py`(인수 조건 4개 검사) |
| A-07-01 | 11/7 | `npc/beliefs.py`·`policy_villager.py`·`policy_possessed.py`·`RuleV0Policy`(규칙 v0 믿음·주민 정책·고백·씐 자 전략과 `StrategyMix` 주입), 결정 직전 정책 믿음 갱신을 상태·관측에 저장, NPC 말 명령 대상·근거 검사, 화자 성향 템플릿 선택, 합의 알리바이가 범행 장소면 대체, `--policy rule-v0`, `tools/perf/npc_rule_check.py` |
| A-07-02 | 11/7 | `npc/vote.py`(투표 3층, 무당 결합 q, 의심 모델 폴백과 `-fallback` 표시, `fallback_count`), 엔진이 `EngineDeps.suspicion`을 투표에 넘김, `Q_MAX`(rule-0.4), `npc_rule_check`의 모델 투표 시간·폴백 집계 |
| A-08-03 | 11/8 | 결정 파라미터를 `decision_params.json`(decision-1)으로 분리·호출 시점 읽기, `routine_randomness` 추가, `tools/optimize_decision.py`(목표 승률 구간 인자), rule-0.5. 논리 우선 봇 vs 규칙 v0 1000판 마을 승률 49.6% |
| A-08-03 | 11/7 | 후속 수정: `RULE_PARAMS_VERSION` 연결 문자 `+` → `.`(`rule-0.5.decision-1`). `+`가 B 회차 등록 검증 `[a-zA-Z0-9_.-]`에 걸려 운영자가 회차를 등록할 수 없었다. `DecisionParams.version`도 같은 문자 집합만 받는다 |
| E-09-08 | 11/9 | `progress.py`(원한 단서 획득·한풀이 해금·결말 선택 규칙), 선택형 도전 프리셋 3종(`Preset.challenge`·`divination_limit`·`witness_scale`, 점 1회 제한은 `divination_available`, 목격 축소는 `draw_sightings(scale)`), `tools/perf/challenge_balance.py`. 기본 프리셋 판은 그대로(배율 1.0·점 제한 없음) |
| A-10-01 | 11/10 | rule-0.8·decision-2(1번, 11/10 사용자 결정): 실행자 거짓 알리바이에 집 포함·한밤 동선 비례(`executor_alibi(prefs)`), 한밤 집 가중치 ×0.25(`_slot_weights`, 콘텐츠 값은 그대로), 목격 0.7·기색 0.2·q_max 1.0. 이전 판 경계: `home_alibi`·`decision_of`·`params.LEGACY_DECISION`, `RuleV0Policy(q_max=...)`, `ContentPolicyEngine` 규칙 버전별 정책. rule-0.7 판 궤적이 이전 엔진과 같음을 해시로 시험(`tests/engine/test_rule08.py`) |
| 투표 기권 | 11/11 | rule-0.9(11/11 사용자 결정 ②안, 운영 기본은 rule-0.8 유지): `CommandKind.ABSTAIN`(규칙 버전으로 `legal_player_kinds`가 더함), `resolve_vote(abstain=)`·`tally`가 기권표(None) 제외, 모두 기권이면 무감금, `abstains`·`abstain_margin_of`, 마을 주민 기권(`vote.uncertain`, 규칙 v0·모델 경로 같은 문턱 `params.ABSTAIN_MARGIN` 0.05), 이유 코드 `abstain_uncertain`, `params.rule_params_version`·`RULE_BASES`. 균형 측정 `tools/perf/abstain_balance.py`. rule-0.8 판 바이트 회귀 해시 시험(`tests/engine/test_vote_abstain.py`) |
| E-10-10 | 11/10 | `review.py`(`review_trace`·`review_text`) 추가, 포트 `GameRepository.events`(가짜 저장소 구현 포함), 문구 파일 `content/review_lines_v1.json`(이유 코드 39개, 11/11 사용자 승인). 엔진 규칙·상태·난수는 바꾸지 않음 |
| E-08-02 | 11/11 | 확장 인물·비밀(`ContentBundle.extra_npcs`·`extra_secrets`, 기본 빈 목록)과 `draw_pool`: 주민 풀이 핵심 인물 수 이하인 프리셋은 핵심 인물·비밀에서만 뽑아 같은 시드 배정이 확장 전과 같다(11/11 사용자 결정, short5·base6 dev와 200/200 일치) |
| E-10-10 | 11/11 | 의심 근거 풀이 `suspect_basis`·`basis_text`(`review.py`): 마을 사람 의심(rule_suspect)의 근거 주장이 믿음 규칙 셋(직접 본 것과 다름·자기를 봤다는 말이 틀림·실종 장소에 있었다고 말함) 중 무엇에 걸렸는지 판 종료 뒤 기록으로 다시 따진다(가중치 큰 쪽). 셋 다 아니면 근거 종류로 기색(공개된 것만)·말이 어긋남·다른 사람들 의견 중 하나. 문구 `basis_lines` 6종(11/11 사용자 승인). 엔진 규칙·이벤트·저장 형식은 바꾸지 않음 |
| D-10-03 | 11/10 | 날짜 최초 공개 승률 특징과 선택 win_prob를 review_trace에 연결, 기존 None 호환 |
