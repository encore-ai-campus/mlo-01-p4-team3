# A 엔진 태스크

- 담당자: (P-01) · 노트북: (P-01)
- 폴더: `engine/domain/`(규칙·NPC·결정 층), `tools/run_engine_games.py`(엔진 판 대량 실행). `engine/`은 표준 라이브러리만 쓴다(`tests/contract/test_layers.py`).
- 늘 볼 문서: `docs/contracts/domain.md`, `ports.md`, `events.md`, 기획서 4~5장.
- 공통 완료 정의·증빙 양식·로그 규칙: [README.md](README.md) 3~5절. 아래 조건은 그 위에 추가되는 조건이다.
- 엔진 단위 테스트 위치: `tests/engine/`(unittest). 실행: `python -m unittest discover -s tests -t .`

---

## 11/5(목)

### A-05-00 자기 노트북 VM 4대 점검
- 0.5h · 선행: E-05-01(노트북 배정)
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-05-00` 행 · [트러블 A](../troubles/A.md)에 `### A-05-00` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")
- 산출물: `docs/evidence/A-05-00.md`
- 완료 조건: C-05-01의 점검표 양식으로 VM 4대 각각의 기동 여부, vCPU·RAM·디스크 사용량, 스냅샷 수를 기록. 논리 서버명만 쓰고 IP는 쓰지 않는다.
- 근거: 기획서 6.3

### A-05-01 판 생성·배정·아침 진술
- 3.5h · 선행: E-05-01
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-05-01` 행 · [트러블 A](../troubles/A.md)에 `### A-05-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 입력: `engine/domain/model.py`, `content.py`(`validate_assignment`), `fixtures/content_min/bundle.json`, `fixtures/assignment/`
- 산출물: `engine/domain/engine.py`(`GameEngine` 포트 구현의 `new_game`), `engine/domain/params.py`(목격 확률 등 밸런스 상수, `RULE_PARAMS_VERSION`), `tests/engine/test_setup.py`
- 완료 조건:
  1. 6개 프리셋 × 시드 200개로 판을 만들었을 때 `validate_assignment` 위반 0건(테스트 `test_setup.AllPresets.test_assignment_valid`).
  2. 0일째 실종자 1명, 역할 수가 프리셋 표(기획서 4.1)와 일치(테스트로 확인).
  3. 0일째 밤 비밀 사건만 생성(확정 결정 ①), 범행 장소 동석 금지(확정 결정 ③) 위반 0건.
  4. 아침 진술이 주장 스키마(기획서 5.1)를 따르고 `serde` 왕복이 손실 없음.
  5. 같은 시드 → 같은 판(결정적)을 테스트로 확인.
  6. 판 생성 1회 시간 p95를 `tools/perf/engine_bench.py --op new_game --n 1000`으로 측정해 증빙에 기록.
- 근거: 기획서 4.1, 4.2, 4.6, 10장 배정 검증기 / domain.md 2, 10

### A-05-02 회의·캐묻기·투표·종료 판정
- 3.5h · 선행: A-05-01
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-05-02` 행 · [트러블 A](../troubles/A.md)에 `### A-05-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 입력: `fixtures/scenarios/normal_game.json`, `runoff_tie_no_lock.json`, `mudang_locked.json`, `last_possessed_locked.json`
- 산출물: `engine/domain/engine.py`(`apply`), `tests/engine/test_scenarios_engine.py`
- 완료 조건:
  1. 위 4개 시나리오를 **실제 엔진으로** 재생해 모든 `checks` 통과.
  2. 단계별 합법 명령(`PHASE_RULES`)과 횟수 제한(`LIMITS`) 위반 명령이 거부되고 상태가 바뀌지 않음(거부 시 `state_version` 불변 테스트).
  3. 투표: 동시 비공개, 자기 투표 금지, 결선, 재동점 무감금. 종료 판정 순서 G=0 → 무당 감금 → G≥V+M과 종료 사유 기록.
  4. 감금 시 씐 자 여부 공개 이벤트가 발생(기획서 4.4).
  5. `apply` 1회 시간 p95(`engine_bench.py --op apply`) 기록. 목표: 게임 API p95 1초 이내를 위해 엔진 몫 p95 100ms 이하. 넘으면 증빙에 원인 기록.
- 근거: 기획서 4.3~4.5 / domain.md 3~5

## 11/6(금)

### A-06-01 밤 처리·점괘·고백·기색
- 3h · 선행: A-05-02
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-06-01` 행 · [트러블 A](../troubles/A.md)에 `### A-06-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 입력: `fixtures/scenarios/secret_confession.json`, `false_divination_reveal.json`
- 산출물: `engine/domain/engine.py`, `tests/engine/test_night.py`
- 완료 조건:
  1. 위 2개 시나리오 실제 엔진 재생 통과.
  2. 점괘는 밤 시작 시 대상 상태로 판정하고, 대상이 그 밤 실종돼도 결과 전달(확정 결정 ②).
  3. 고백자는 다음 점부터 맑음, 고백자는 후보에서 제외(domain.md 6-1).
  4. 기색은 사실 주장이 거짓일 때만, 주장 ID당 1회 고정(재질문 시 같은 결과 테스트).
  5. 점괘 공개 하루 1회, 거짓 공개 합법, 실제값·공개값 분리 저장.
- 근거: 기획서 4.2, 4.3(밤), 4.6

### A-06-02 관측 투영·스냅샷·진실 이벤트·이유 코드
- 2h · 선행: A-06-01
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-06-02` 행 · [트러블 A](../troubles/A.md)에 `### A-06-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/observation.py`, `tests/engine/test_projection.py`
- 완료 조건:
  1. 처리 순서 관측 투영 → 믿음 갱신 → 행동 선택. 행동 선택 함수가 진실(①)을 읽지 못함을 테스트로 증명(진실 객체를 넘기지 않는 시그니처 + 진실 필드 접근 시 실패하는 테스트).
  2. 결정마다 행동 직전 `observation` 이벤트, 결정 이벤트가 같은 `snapshot_id` 참조(events.md 3).
  3. `truth_revealed`는 `phase=ended` 뒤에만 발생(테스트).
  4. 모든 NPC 행동에 이유 코드·근거 ID(기획서 5.2 끝).
  5. 아침 발표에 피해 장소·시간대 공개(`DisappearanceRecord.place`·`slot`, `morning_announced.payload.crime_scene`), 0일째 포함. 집이면 피해자의 집.
  6. 알리바이 확정 명제는 `alibi_propositions`(공개 피해 장소 + 자기 목격)로만 만들고, 2명 판은 "실행자 아님"까지만(후보 제거 안 함). 타인의 공개 목격 주장은 근거(`others_claim`·`mutual_vouch`)로만. `tests/contract/test_crime_scene.py` 통과 + 엔진 판 200개에서 진실의 피해 장소를 읽은 코드 경로 0건(관측 함수 입력에 진실 없음).
- 근거: 기획서 5.1, 8.2 / events.md 3

### A-06-03 D·C 인수 패키지 (실제 엔진 소량 판)
- 2.5h · 선행: A-06-02
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-06-03` 행 · [트러블 A](../troubles/A.md)에 `### A-06-03` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `tools/run_engine_games.py`(엔진 + 최소 합법 NPC 정책 `policy=min-legal`으로 판 실행, `EventSink`로 jsonl 출력), `out/handoff/a06/`(커밋 제외, 생성 명령만 증빙)
- 완료 조건:
  1. `python -m tools.run_engine_games --preset base6 --games 200 --seed 1 --out out/handoff/a06` 실행 결과: 200판 모두 정확히 1번 종료, 판마다 `seq` 빈틈 없음, 모든 이벤트가 `serde` 엄격 검증 통과.
  2. 관측 스냅샷·진실·결정 이유 코드가 모두 들어 있음(`parallel_dev.md` 3절 인수 조건 4개를 스크립트로 검사: `tools/check_handoff_a06.py`).
  3. 처리량(판/초)과 판당 이벤트 수를 측정해 기록.
  4. D·C 담당이 같은 명령으로 재생성할 수 있음(증빙에 명령과 소요 시간).
- 근거: 기획서 6.2, 8.2 / parallel_dev.md 3

## 11/7(토)

### A-07-01 NPC 규칙 v0 (믿음 갱신·행동 선택·씐 자 전략)
- 4.5h · 선행: A-06-03
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-07-01` 행 · [트러블 A](../troubles/A.md)에 `### A-07-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/npc/`(`beliefs.py`, `policy_villager.py`, `policy_possessed.py`), `tests/engine/test_npc.py`
- 완료 조건:
  1. 믿음: 대상별 의심도, 무당 신뢰도, 확정 명제(출처: 직접 목격만 확정, 타인 주장·상호 보증 구분), "못 봤다"는 부재 증명 아님 — 각각 단위 테스트.
  2. 반복 의심 키(발언자·대상·근거) 효과 없음, 해명 반복 효과 감소, 모순 지적 실패 시 신뢰 하락 — 단위 테스트.
  3. 씐 자 전략 v0: 거짓 행적 선택, 누명 대상, 밤 대상 혼합, 무당 의심 유도. 비밀 보유 정보를 읽지 않음(테스트).
  4. 비밀 주민은 압박이 문턱값을 넘으면 고백, 씐 자는 고백하지 않음.
  5. `python -m tools.run_engine_games --preset base6 --games 500 --seed 2 --policy rule-v0`에서 합법 행동 위반 0건, 마을 추론 위반(확정 명제와 충돌하는 투표) 0건. 결과 JSON을 증빙에 첨부.
  6. NPC 1명 결정 시간 p95 측정·기록.
- 근거: 기획서 5.1, 5.2, 5.3

### A-07-02 투표 3층과 의심 모델 포트 연결
- 1.5h · 선행: A-07-01
- 문서화(+0.25h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-07-02` 행 · [트러블 A](../troubles/A.md)에 `### A-07-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/npc/vote.py`, `tests/engine/test_vote_layers.py`
- 완료 조건:
  1. 추정(`SuspicionModel` 포트) → 결정(성향 가중 확률 + 동조 × 예상 득표 + 잡음) → 안전(합법·확정 명제 필터, 전부 제거 시 의심도 최대 폴백) 각 층 테스트.
  2. 무당 의심 결합 q = q_max × (100 − trust)/100(ports.md 3), q_max 기본 0.5를 `params.py`에.
  3. 모델 실패·시간 초과(100ms)·버전 불일치 시 그 결정만 규칙 v0로 대체, `policy_version`에 `-fallback`(테스트: `FakeSuspicionModel` 실패 주입).
  4. 폴백 횟수 지표 `paldo_engine_suspicion_fallback_total` 노출 지점(엔진은 카운트만 반환, B가 노출) 정의.
- 근거: 기획서 5.2 / ports.md 3

### A-07-03 통합 1 참여
- 1.5h · 선행: A-07-02 · 조건: [E_pm.md INT-07](E_pm.md#int-07-통합-1-117) 중 A 항목 전부 통과
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-07-03` 행 · [트러블 A](../troubles/A.md)에 `### A-07-03` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

## 11/8(일)

### A-08-01 창귀 2명 규칙
- 3h · 선행: A-07-01
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-08-01` 행 · [트러블 A](../troubles/A.md)에 `### A-08-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 입력: `fixtures/scenarios/duo_accomplice_alibi.json`, `fixtures/assignment/valid_duo9.json`
- 산출물: `engine/domain/duo.py`, `tests/engine/test_duo.py`
- 완료 조건:
  1. 시나리오 통과. 공동 밤 명령, 실행자 선정, 거짓말 의무는 실행자만, 짝 정보 공유 범위(기획서 4.1) 테스트.
  2. 팀 전략 4종(감싸기, 거리 두기, 버리기, 알리바이 보강)이 선택 가능하고 선택 비율을 외부(D-09-02 밴딧)에서 주입할 수 있음.
  3. 감금 공개형·비공개형을 `params.py` 플래그로 전환 가능.
  4. duo9, duo11 각 500판(`--policy rule-v0`, 공개형·비공개형 각각) 실행: 합법·추론 위반 0건, 승률·판 길이 표를 증빙에.
- 근거: 기획서 4.1, 4.4, 5.2 / domain.md 6

### A-08-02 큰 판 회의 발언 축약
- 1.5h · 선행: A-08-01
- 문서화(+0.25h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-08-02` 행 · [트러블 A](../troubles/A.md)에 `### A-08-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/summarize.py`, `tests/engine/test_summarize.py`
- 완료 조건: domain.md 8절 규칙(반복 진술 접기, 변경점만 표시, 회의 축약) 각각 테스트. 11인 판 100판에서 회의 화면 항목 수(축약 전·후)를 측정해 기록.
- 근거: 기획서 4.1(11인), 12장 / domain.md 8

### A-08-03 결정 층 최적화 (1차)
- 3h · 선행: A-07-02, D-06-01(봇 정책 4종)
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-08-03` 행 · [트러블 A](../troubles/A.md)에 `### A-08-03` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring
- 산출물: `tools/optimize_decision.py`, `engine/domain/decision_params.json`(`DECISION_PARAMS_VERSION`)
- 완료 조건:
  1. 그리드 또는 베이지안 탐색 대상과 범위를 증빙에 표로. 대상: 결정 층(동조 가중치, 잡음, q_max)과 **피해 장소 공개 상태에서의** 목격 확률·동선 무작위성(`params.py`). 피해 장소 공개를 끈 비교 실행은 하지 않는다(공개가 확정 규칙).
  2. 최종 파라미터로 6인·창귀 1, 플레이어 대역 "논리 우선 봇" vs 규칙 v0 NPC **1,000판 이상**: 마을(대역 봇) 승률 **40~60%**, 95% 구간 함께 보고.
  3. 마을 추론 위반 0건(안전 층으로 보장).
  4. 실행 명령·시드 고정으로 재현 가능. 탐색 소요 시간 기록.
  5. 40~60%를 못 맞추면 완료 아님. 대신 목격 확률·비밀 수 등 조정 후보를 증빙에 남기고 A-10-01에서 재시도(PM 보고).
- 근거: 기획서 5.2, 8.3

## 11/9(월)

### A-09-01 원한 단서 해금·결말·선택형 도전
- 3h · 선행: A-07-01, E-06-03(단서 초안)
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-09-01` 행 · [트러블 A](../troubles/A.md)에 `### A-09-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/progress.py`, `tests/engine/test_progress.py`
- 완료 조건:
  1. 고백·추궁에서 단서 획득, 중복 무시(`progress_clue` 체크), 판 밖 상태만 유지(판 안 NPC 기억은 버림).
  2. 결말은 판 시작 때 선택, 같은 가설 단서 3조각이면 한풀이 해금(확정 결정 ④). 퇴마는 항상 가능.
  3. 선택형 도전 3종(점 1회 제한, 비밀 있는 주민 3명, 목격 정보 축소)을 프리셋 옵션으로. 각 옵션 300판 시뮬레이션 승률 기록(공개 여부는 P-08 기준).
  4. 진척 장애 복구: 고백 이벤트 저장 후 진척 반영 전에 프로세스가 죽는 경우를 주입(가짜 저장소의 실패 주입 + C-06-01 Mongo 구현)해, 재처리(이벤트 재생)로 단서·한풀이 해금이 복구되고 중복 획득 0(테스트).
- 근거: 기획서 4.7 / domain.md 9

### A-09-02 사용자 테스트 대응 수정
- 2h · 선행: E-09-01 진행 중
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-09-02` 행 · [트러블 A](../troubles/A.md)에 `### A-09-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")
- 완료 조건: 11/9 18:00까지 생성된 `bug`+`engine` 라벨 이슈 전부 종료 또는 "보류 사유" 코멘트. 단 `blocker` 이슈는 보류 불가(README 3절). 수정마다 회귀 테스트 1개 이상.
- 근거: 기획서 11장 4단계

### A-09-03 통합 2 참여
- 1.5h · 조건: [INT-09](E_pm.md#int-09-통합-2-119) 중 A 항목 전부
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-09-03` 행 · [트러블 A](../troubles/A.md)에 `### A-09-03` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

### A-09-04 프리셋 반례 테스트 (5, 7, 8)
- 1h · 산출물: `tests/engine/test_presets.py`
- 문서화(+0.25h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-09-04` 행 · [트러블 A](../troubles/A.md)에 `### A-09-04` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 완료 조건: 각 프리셋에서 종료 판정 경계(마지막 씐 자 감금, 무당 감금, G≥V+M 도달) 시나리오 테스트 통과.

## 11/10(화)

### A-10-01 결정 층 재최적화 (모델 v0·보정 대역 봇)
- 3h · 선행: D-08-04(씐 자 모델 v0), D-10-01의 ①(대역 봇 보정)
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-10-01` 행 · [트러블 A](../troubles/A.md)에 `### A-10-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring
- 산출물: `decision_params.json` v2, 프리셋별 표
- 완료 조건:
  1. 모델 v0를 추정 층에 쓴 상태, **피해 장소 공개 상태**에서 6인 판 1,000판 이상 대역 봇 승률 40~60%(95% 구간). 벗어나면 목격 확률·동선 무작위성을 다시 조정.
  2. 5, 7, 8, 9/2, 11/2 각 500판 승률·판 길이·무감금 빈도·고백률 표. 공개 여부는 P-08 기준으로 PM이 결정(표가 근거).
  3. 모델 대비 규칙 v0 차이(승률 ±5%p 이내, ml_lifecycle 2) 확인.
- 근거: 기획서 4.1, 5.2, 8.3, 9.3

### A-10-02 복기용 이유 조회
- 2h · 인계: **10:00까지 1번 → E-10-01·B-10-01** (이 태스크를 그날 가장 먼저 한다)
- 문서화(+0.5h, 공통 ⑦): 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md)의 필수 절 6개 갱신과 `변경 이력`에 `A-10-02` 행 · [트러블 A](../troubles/A.md)에 `### A-10-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음") · 새로 만들거나 바꾼 공개 함수·클래스 docstring · 새 폴더를 만들면 `paldo/README.md` 폴더 안내·작업별 위치 표 갱신
- 산출물: `engine/domain/review.py`(`review_trace(game)`: 행동 시점의 이유 코드·근거 ID·의심도 기여 상위 3개), `fixtures/review/review_trace_sample.json`
- 완료 조건:
  1. `review_trace` 반환 형식(필드·타입, 선택 필드 `win_prob` 포함)을 문서 문자열과 샘플 픽스처로 먼저 병합(10:00).
  2. 녹화 판과 시뮬레이션 판 20개에서 모든 NPC 투표에 이유가 있음(누락 0). 사후 계산이 아니라 행동 시점 기록만 사용(테스트: 판 종료 후 상태를 바꿔도 결과 동일). 실제 출력이 샘플 형식과 같음(테스트).
- 근거: 기획서 5.2, 7.1(복기)

### A-10-03 트러블·검증 기록 (발표용)
- 1.5h · 산출물: `docs/presentation/A.md`
- 문서화(+0h, 공통 ⑦): 산출물 자체가 문서다. 문서화 기준(README 10절)의 문서 제목 규칙을 따르고, 수치마다 증빙 링크. [트러블 A](../troubles/A.md)의 `### A-10-03` 항목은 "없음"이어도 둔다.
- 완료 조건: 담당 → 구현 → 트러블(최소 2건, 증빙 링크) → 산출물·검증 수치(테스트 수, 위반 0건, 승률 구간, 엔진 지연 p95) 4구간. 모든 수치에 증빙 링크.

### A-10-04 복기·해설 연동 지원
- 1h · 완료 조건: B-10-01과 E-10-01이 `review_trace`로 화면·해설을 만드는 테스트가 통과(그쪽 태스크의 조건과 같음).
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-10-04` 행 · [트러블 A](../troubles/A.md)에 `### A-10-04` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

## 11/11(수) 검증·수정 전용

### A-11-01 통합 3 수정
- 4h · 조건: [INT-11](E_pm.md#int-11-통합-3-1111) 중 A 항목 전부, 신규 기능 추가 없음(PR 라벨 `fix`만)
- 문서화(+0.5h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-11-01` 행 · [트러블 A](../troubles/A.md)에 `### A-11-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

### A-11-02 최종 파라미터로 재실행
- 1.5h · 완료 조건: 최종 커밋으로 A-08-03·A-10-01 표를 다시 생성해 6인 판 승률 40~60%(합격선)를 최종 결과로 충족하고, 값이 증빙과 ±2%p 안. 벗어나면 원인을 기록하되, 합격선 미달이면 원인 기록만으로 완료할 수 없다.
- 문서화(+0.25h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-11-02` 행 · [트러블 A](../troubles/A.md)에 `### A-11-02` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

### A-11-03 발표 구간 자료
- 2h · 완료 조건: E-11-02의 슬라이드 양식에 A 구간(3장 이내) 반영, 발표 대본 2분 이내.
- 문서화(+0h, 공통 ⑦): 산출물 자체가 문서다. 문서화 기준(README 10절)의 문서 제목 규칙을 따르고, 수치마다 증빙 링크. [트러블 A](../troubles/A.md)의 `### A-11-03` 항목은 "없음"이어도 둔다.

## 11/12(목)

### A-12-01 리허설·시연·발표
- 4h · 완료 조건: E-12-01 리허설 2회 참여, 담당 구간 시간 초과 없음.
- 문서화(+0.5h, 공통 ⑦): 코드를 바꿨다면 모듈 문서 [`docs/modules/engine.md`](../modules/engine.md) `변경 이력`에 `A-12-01` 행 · [트러블 A](../troubles/A.md)에 `### A-12-01` 항목(문제 → 원인 → 대응 → 결과 수치, 없으면 "없음")

---

## 일별 시간 합계 (공통 1.5h 별도, 태스크 한도 7.5h)

`python -m tools.tasks_to_issues --hours`가 이 표를 태스크 문서에서 다시 계산한다.

| 날짜 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|
| 태스크 | 7.5 | 7.5 | 7.5 | 7.5 | 7.5 | 7.5 | 7.5 | 4 |
| 문서화 | 1.25 | 1.5 | 1 | 1.25 | 1.25 | 1.25 | 0.75 | 0.5 |
| **합계** | **8.75** | **9** | **8.5** | **8.75** | **8.75** | **8.75** | **8.25** | 4.5 |

**한도 7.5h 초과 합계: 8.25h** (문서화 8.75h 포함). 해소 방법은 결정 대기 P-22(README 7절).
