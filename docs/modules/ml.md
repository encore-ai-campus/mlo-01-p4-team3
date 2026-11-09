# ML 모델

## 무엇을
`gate.py`는 수명주기 평가 판정, `bandit.py`는 P-16 보상·상한·버전 고정 전략 공급자,
`mongo_bandit.py`는 전체 이벤트 export 배치·Mongo CAS와 C의 판 ID별 삭제 연결을 제공한다.
구체적인 입력·B/C 책임·실행과 미충족 범위는 [D-09-02 인계](../reports/D-09-02-handoff.md)를 따른다.

`ml/suspicion/`은 씐 자 1명 판의 마을 NPC 관측을 학습·평가하는 인프라 모듈이다.
`features.py`는 실제 엔진 bot JSONL을 `TrainingRow` JSONL과 provenance manifest로 변환한다.
`train.py`는 게임 분할·로지스틱 회귀·LightGBM·로컬 MLflow 추적을 수행한다.
`evaluate.py`는 관측별 softmax, 다중분류 로그 손실·상위1 적중률·후보별 ECE를 계산한다.
`strategies.py`는 기존 `RuleV0Policy`와 `MixSource`로 거짓 행적 유형을 고정한 실제 엔진 판을 만든다.
의도 기준선은 [intent.md](intent.md), 광고 서버는 [ads.md](ads.md)를 따른다.

## 왜

[events.md](../contracts/events.md) 3절의 완료 진실 조인·마을 관찰자 경계를 지키고,
[ports.md](../contracts/ports.md) 3절의 창귀 1명 확률 합 1을 보장한다.
[ml_lifecycle.md](../contracts/ml_lifecycle.md)와 [parallel_dev.md](../contracts/parallel_dev.md)에 따라
고정 게임 분할과 단일 정책 버전으로만 평가한다. 전체 모델 서비스·승격은 후속 태스크다.

## 사용법

```powershell
python -m tools.run_engine_games --preset base6 --games 200 --seed 2 --policy rule-v0 --out out/handoff/a0701
python -m tools.check_handoff_a06 --dir out/handoff/a0701 --record D-07-02
python -m ml.suspicion.features --events out/handoff/a0701/events.jsonl --out out/suspicion/pm-data
python -m ml.suspicion.train --rows out/suspicion/pm-data/rows.jsonl --out out/suspicion/pm-run --seed 7 --record D-07-02
python -m unittest tests.test_suspicion -q
```

학습에 쓰지 않은 거짓말 전략의 별도 평가 명령이다. 판별 seed와 게임 ID도 분리한다.

```powershell
python -m ml.suspicion.strategies --out out/suspicion/1108/train --games 300 --seed 708 --lie-type unrefuted
python -m ml.suspicion.strategies --out out/suspicion/1108/heldout --games 60 --seed 709 --lie-type routine
python -m ml.suspicion.train --rows out/suspicion/1108/train/data/rows.jsonl --heldout-rows out/suspicion/1108/heldout/data/rows.jsonl --out out/suspicion/1108/strategy-run --seed 7 --record D-07-02 --result-name strategy
```

학습·dev 보정에는 첫 데이터만 사용한다. 별도 평가에서는 같은 규칙·콘텐츠·정책 버전, 겹치지 않는
게임과 seed, 학습에 없는 `lie_*`의 실제 선택 횟수를 검사한다. `heldout_post_night_*`는 요청한 유형을
실제 선택한 밤 이후 관측의 별도 지표다. 그 유형을 선택하지 않은 판과 선택 전 관측은 제외한다.
0일째 배정은 두 전략에서 공통이며, routine 선택 뒤 엔진이 실제 범행 장소와
겹치는 알리바이를 unrefuted 방식으로 대신할 수 있다. 보고하는 holdout은 정책의 거짓말 유형 선택 축이다.

특징 변환과 학습의 출력 경로는 새 경로여야 한다. 학습 모델·온도·보고서·게임 분할은 `out/suspicion/pm-run/`과
`mlruns/suspicion-artifacts/`에 보존한다. 추적 DB는 `mlruns/suspicion.db`다.
`models.pkl`은 이 실행이 만든 로컬 모델이며 신뢰하는 산출물만 불러온다.
의존성은 `requirements/ml.txt`의 scikit-learn 1.9.1·LightGBM 4.7.0·MLflow 3.18.0을 사용한다.

11/8 PM 결정에 따라 모델 비교에는 로컬 `rule-v0` 200판을 쓴다. C-07-01 공식 silver v1의 `baseline-0` 200판은 별도 연결 검증에 쓴다. C의 원본 이벤트와 완료된 Spark export 디렉터리를 확보한 뒤 실행한다.

```powershell
python -m ml.suspicion.features --events out/suspicion/delta1108/source/events200.jsonl --delta-export out/suspicion/delta1108/source/training_export --delta-version 1 --out out/suspicion/delta1108/aligned
python -m ml.suspicion.train --rows out/suspicion/delta1108/aligned/rows.jsonl --out out/suspicion/delta1108/run --seed 7 --record D-07-02 --result-name delta_connectivity
```

원본의 마을 관측에서 `suspicion-1` 특징을 계산하고, `public-snapshot-v1` export의 행 ID·라벨·조인 시각 등을 대조해 특징을 교체한다. 대조에서는 C가 기록한 활동 인원 수를 보존한다. manifest에는 Delta 버전과 export part별 해시, 원본과 변환 행의 해시, 정책과 seed를 기록한다. 접속 위치와 키는 외부 보호 설정으로 관리한다.

- D-07-03 의도 미세조정은 `python -m tools.perf.intent_finetune --epochs 5 --include-generated`로 실행한다. 증빙은 `docs/evidence/D-07-03.md`, 모델·실행 계보는 `mlruns/intent_bert/`에 보관한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| feature_version | suspicion-1 | rule_score, day, n_candidates, trust_in_mudang 및 근거 묶음별 delta/25 | 변경 시 모델·export 재생성 |
| seed | 7 | 분할·추정기·bootstrap seed | 재학습 분할이 바뀜 |
| train/dev/test | 70/15/15 | 게임 ID 정렬 뒤 고정 seed 셔플 | 행 단위 섞기 금지 |
| LightGBM | 80 trees, 7 leaves, depth 3, lr .05 | CPU 1개·결정적 학습 | train만 학습에 사용 |
| temperature | .5, 1, 2, 4 중 dev log loss 최소 | 모델별 softmax 로짓 보정 | test는 선택에 사용하지 않음 |
| ECE | 10 equal-width bins | 후보별 이진 라벨과 확률의 가중 오차 | top1 confidence ECE와 다름 |
| small test | 50판 미만 300회 게임 bootstrap | 95% 구간 함께 보고 | 개선 판정은 별도 관문 |

## 한계
관문 통과는 별도 candidate 등록·카나리아·승격을 자동 실행하지 않는다. 실제 사람 성능은 미측정이다.
밴딧은 클래스룸 파일럿용 단일 Mongo 문서(16 MiB 한계)다. B의 실제 판 시작 주입과 종료 이벤트 연결은 수신 검증 대기다.
삭제 필터를 등록한 C가 `MongoBanditStore.forget_games`를 호출해야 후속 전체 이력 갱신이 정상 동작한다.


모델 비교와 Delta 연결 검증은 별도 실행이다. 다른 프리셋의 최소 판 수와 모델 합격선은 D-08-01부터 적용한다.
형식용 synthetic/scripted와 사람 데이터는 현재 경계에서 거부한다. 현재 데이터는 실제 bot 판이다.
규칙 기준값은 엔진에 주입된 `FakeSuspicionModel`과 같은 `beliefs.suspicion/25` softmax다.
행동 감사는 입력 엔진 판의 `legal_problem`과 대상 존재·`vote_conflicts` 집계이며,
새 모델을 엔진에 연결한 행동 위반·승률 검증을 대체하지 않는다(D-08-01).
두 모델은 같은 bot 정책에 대한 오프라인 수치만 제공하며 운영 승격·배포·성능 우열을 판정하지 않는다.
모델 v0 어댑터는 D-08-01, 관문·밴딧은 D-09-02, 광고 CTR LightGBM은 D-10-02 범위다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 하루 일괄 문서화로 ML 패키지의 미구현 범위와 후속 작업 위치를 명시 |
| D-09-02 | 11/9 | 수명주기 관문·P-16 밴딧·동시성/재시도/삭제 처리·BERT/B/C 인계. 사람·운영 검증은 별도 |
| D-07-02 | 11/7 | 로컬 TrainingRow 변환·게임 분할·두 추정기·규칙 비교·MLflow·경계 테스트 |
| D-07-02 | 11/8 | 최신 dev 규칙 반영, 거짓말 전략 고정 생성·별도 평가·판/seed 중복 거부·실제 선택 이후 관측 분리 |
| D-07-02 | 11/8 | C Delta 행의 원본 관측 대조와 학습 코드 연결 실행 추가 |
| D-07-03 | 11/7 | intent/의 CPU 미세조정·동일 기준선 평가와 모델·계보 저장 경로를 연결 |
