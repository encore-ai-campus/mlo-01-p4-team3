# ML 모델

## 무엇을
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

C 인계 뒤 `TrainingRow` 형식의 Delta export를 `--rows`로 넣고 `--manifest`로 인계 메타데이터를 지정한다.
manifest의 필수 키는 `data_version`(`delta:<table>@<version>`), `input_sha256`, `rows_sha256`,
`feature_version=suspicion-1`, 단일 `npc_policy_version`, `seeds` 목록,
`observer_filter=completed_truth_villager`, `candidate_filter=snapshot_excludes_mudang`다.
원본 진실·역할·정책은 TrainingRow에 없으므로 필터·정책 선언은 C export의 검증 책임이다.
수치 특징의 버전/열이 다르면 거부하며 C와 먼저 특징 추출을 맞춘다. 계약 자체는 바꾸지 않는다.

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

로컬 사전 개발이다. C Delta 최종 평가, 다른 필수 프리셋의 최소 판 수,
VM Python 3.14에서 torch·transformers·lightgbm·pyspark import는 미완료다.
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
| D-07-02 | 11/7 | 로컬 TrainingRow 변환·게임 분할·두 추정기·규칙 비교·MLflow·경계 테스트 |
| D-07-02 | 11/8 | 최신 dev 규칙 반영, 거짓말 전략 고정 생성·별도 평가·판/seed 중복 거부·실제 선택 이후 관측 분리 |
