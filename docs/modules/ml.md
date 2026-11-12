# ML 모델

## 무엇을
`gate.py`는 수명주기 평가 판정, `bandit.py`는 P-16 보상·상한·버전 고정 전략 공급자,
`mongo_bandit.py`는 전체 이벤트 export 배치·Mongo CAS와 C의 판 ID별 삭제 연결을 제공한다.
`ad_ctr.py`는 확정 광고 집계로 맥락별 CTR 모델을 학습하고 베타 기준선과 같은 평가를 수행한다.
구체적인 입력·B/C 책임·실행과 미충족 범위는 [D-09-02 인계](../reports/D-09-02-handoff.md)를 따른다.

`ml/suspicion/`은 씐 자 1명 판의 마을 NPC 관측을 학습·평가하는 인프라 모듈이다.
`features.py`는 실제 엔진 bot JSONL을 `TrainingRow` JSONL과 provenance manifest로 변환한다.
`train.py`는 게임 분할·로지스틱 회귀·LightGBM·로컬 MLflow 추적을 수행한다.
`evaluate.py`는 관측별 softmax, 다중분류 로그 손실·상위1 적중률·후보별 ECE를 계산한다.
`strategies.py`는 기존 `RuleV0Policy`와 `MixSource`로 거짓 행적 유형을 고정한 실제 엔진 판을 만든다.
`server/infra/model_selector.py`는 로컬에 등록한 모델을 정확한 버전으로 조회하고 의심 모델의 특징 버전을 검사한다.
`ml/adapters/reproduce_model_games.py`는 Selector로 고른 같은 모델·시드로 판 실행을 두 번 돌려 결과가 같은지 비교한다(D-08-04 재현).
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
- D-09-01은 `python -m tools.perf.intent_human_eval --help`에서 실행 인자를 확인한다. 명령 전체 일치율과 소표본 구간 판정은 D-09-01 범위다. 레지스트리 승격 관문은 D-09-02가 담당한다.

### D-08-01 모델 판 실행

D-08-01의 `ml/adapters/suspicion_model.py`는 웹 프로세스 내 로지스틱 추론과 격리 worker를 제공한다. `ml/adapters/run_model_games.py`는 기존 판 실행기를 재사용해 모델 버전·합법성·폴백 수·p95를 기록한다. `tools/`에서 `ml/`을 import하지 않는다.

11/11 PM 결정으로 운영 웹과 부하 시험은 `suspicion-logistic-` 버전의 산출물을 웹 프로세스에 버전별로 한 번 로드하고, 각 호출에서 읽기 전용으로 직접 추론한다. 로드·예열에서 BLAS를 한 스레드로 제한한다. 동기 추론 시간은 웹 요청에 포함되며 엔진은 반환된 결과가 100ms를 넘으면 버리고 그 결정만 규칙 v0로 폴백한다. Selector가 산출물의 버전과 특징을 확인하고 기존 결과 검증과 폴백 이유 코드를 적용한다. 부하 도구는 두 버전이 상주한 웹 RSS와 살아 있는 worker 수를 기록한다.

11/10 PM 정책에 따라 90ms 일반 마감은 해당 호출만 폴백한다. 같은 worker의 늦은 응답을 배경에서 요청 번호로 확인해 버리고, 처리 중에는 새 요청을 쌓지 않는다. 프로세스 종료 또는 요청 전송 시각부터 1.5초 동안 응답이 없을 때 같은 버전으로 재기동한다. 강제 한도는 후속 호출 없이도 집행된다. 재기동에 해당하는 연속 실패가 3회면 30초 대기하며 정상 추론 시 계수를 초기화한다. 입력 특징 오류는 worker를 유지하고, 산출물 특징·재로드 모델 ID 불일치와 파일 부재는 해당 버전을 거부한다. 명시적 `close()` 뒤에는 재기동하지 않는다.

동급 VM 재측정은 기존 부하 CLI의 `--serve-dir <비공개 교환 경로> --listen-host <사설 IPv4> --client-ip <생성기 IPv4>`로 웹만 실행한다. SSH로 `client.json`을 측정 VM 밖의 비공개 폴더에 전달하고, 그 호스트에서 같은 CLI에 `--load-dir <폴더>`를 주어 부하를 실행한다. 생성된 `load.json`을 서버 교환 경로에 넘기면 서버가 추론·RSS·원인별 폴백을 회수하고 정리한다. 세션이 든 `client.json`은 로컬 증빙과 Git에 포함하지 않는다.

시험은 `SharedModelStore`, 운영 웹은 B-09-01의 `ProcessSuspicionModels`가 버전별 모델을 소유한다. 두 경로는 `selector.suspicion_model(version, in_process_logistic=True)`로 로지스틱을 선택한다. 판은 고정 버전의 포트를 빌리고 소유 보관소가 모델을 정리한다. 격리 worker 어댑터는 CLI 실행과 무거운 모델을 위한 경로로 남긴다.

일반 timeout으로 worker를 유지한 호출은 연속 재기동 실패 수에 넣지 않는다. D-08-01 시험 조립은 NPC별 포트 호출·잠금 대기의 실제 시작/끝과 엔진 clock의 마감 시각을 같은 snapshot ID에 연결하고, 요청 UTC/monotonic 기준점으로 대사한다. 공식 부하는 11/11 PM 결정에 따라 코드 PR 병합 SHA를 고정한 기준 1회와 모델 켬 연속 2회로 판정한다.

```sh
python -m ml.adapters.run_model_games --preset base6 --games 50 --seed 1 --model out/suspicion/run1/models.pkl --out out/model_games
```

`--preset/--games/--seed/--out/--policy/--record`는 기존 도구와 같다. 기본 정책은 모델을 호출하는 `rule-v0`다. 파일 경로 모드는 `--model`, `PALDO_SUSPICION_MODEL_PATH`, 위 로컬 경로 순으로 읽는다. 신뢰된 학습 산출물 `models.pkl`과 그 의존 패키지가 필요하다. 없으면 종료 코드 1이며 모델 실행 성공 manifest를 만들지 않는다. `--policy min-legal`은 모델을 사용하지 않아 성공으로 판정하지 않는다.

웹 부하 시험은 `tools/perf/api_bench.py --web-processes 2 --mlruns <로컬 디렉터리> --suspicion-version <고정 버전>`으로 실제 엔진과 어댑터를 조립한다. 기본 녹화 엔진 모드와 같은 계정·Caddy·Mongo 접두어 격리를 사용하고, 시험 이벤트에는 `load_test=true`를 기록한다. 공개 PlayerView에서 명령을 만들어 일반 판을 끝까지 진행한다. 각 웹 프로세스의 모델 호출·실패와 히스토그램을 합산해 추론 p95 버킷 상한을 기록한다. 미호출·미완주·오류, 모델 p95 상한 100ms 초과 또는 게임 API p95 1초 초과면 종료 코드 1이다. 최종 검증은 적격 모델로 사용자 테스트 서버 또는 동급 VM에서 `--users 30 --minutes 3 --front binary --result-task D-08-01 --result-name model_web_load`를 붙인다.

### 로컬 모델 인계 (D-08-04)

1. D-07-02의 `models.pkl`과 `report.json`을 함께 `mlruns/suspicion-v0/<suspicion_version>/`에 둔다. D-08-01의 버전은 `suspicion-logistic-` 뒤에 `models.pkl`의 SHA-256 앞 12자리를 붙인 값이다. 같은 버전의 파일을 덮어쓰지 않는다. 11/9 사용자 결정에 따라 D-08-04는 로컬 인계·50판 재현으로 완료하며, 레지스트리 후보 등록·운영 적용은 A-10-01 3번과 수명주기 평가 관문을 통과한 뒤 연결한다.
2. 사용할 TF-IDF 기준선은 기존 `mlruns/intent-v0/<intent_version>/model.joblib` 경로에 둔다. `<intent_version>`은 저장된 `IntentBaseline.model_version`과 같아야 한다. 두 파일 형식 모두 신뢰하는 로컬 산출물만 사용한다.
3. A·B는 동일 커밋·콘텐츠·모델 파일에서 아래 명령으로 각 50판을 실행한다. 각자 다른 `--out`을 사용하고, `manifest.json`의 두 모델 버전과 위반·폴백 0건, `events.jsonl`의 SHA-256을 대조한다. 벤치마크는 의도 아티팩트도 불러 버전을 검사하며, 봇 판의 이벤트에는 추론에 사용한 의심 모델 버전이 기록된다.
4. B 조립에서는 `LocalModelSelector(Path("mlruns"), suspicion_version=suspicion_version, intent_version=intent_version, feature_version=FEATURE_VERSION)`을 만든다. `FEATURE_VERSION`은 `ml.suspicion.features`에서 가져온다. `select(game_id, player_pseudonym)`의 버전을 판 메타에 고정하고 그 값으로 두 모델을 조회한다. 실제 `ParseResult.versions["model"]`도 판 메타와 비교한다. 운영의 의심 모델은 프로세스별 `SharedModelStore`에서 빌려 쓰며 판이 닫지 않는다. 독립 CLI가 직접 로드한 어댑터는 해당 CLI 종료 때 닫는다.

```powershell
python -m ml.adapters.run_model_games --preset base6 --games 50 --seed 1 --mlruns mlruns --suspicion-version $suspicionVersion --intent-version $intentVersion --out out/d08-04/bench-a
Get-FileHash out/d08-04/bench-a/events.jsonl -Algorithm SHA256
```

같은 PC에서 두 번 돌려 바로 비교하려면 재현 명령을 쓴다(11/9, E가 A 쪽으로 준비). 두 실행의 판 ID·모델·특징·규칙 버전, `events.jsonl` SHA-256, 판별 이벤트 수, 판별 종료 결과(승자·사유)를 비교하고, 실행 시간·추론 지연·커밋·출력 경로는 비교하지 않는다. 같고 두 실행이 모두 통과(위반·폴백 0, p95 100ms 이하)하면 종료 코드 0이다. 보고는 `<out>/reproduce.json`.

```powershell
python -m ml.adapters.reproduce_model_games --mlruns mlruns --suspicion-version $suspicionVersion --intent-version $intentVersion --games 50 --seed 1 --out out/d08-04/repro
```

11/9 로컬 인계 버전은 `suspicion-logistic-1afb8f051c4e`와 `tfidf-lr-v0-2a678588557c`다. 원본 SHA-256과 검증 범위는 `mlruns/handoff-v0.json`, 증빙은 `docs/evidence/D-08-04.md`에 있다. A CLI 두 번과 B의 `ContentPolicyEngine` 조립 한 번을 D 환경에서 실행해 각 50판, 총 추론 960건 성공, 이벤트 해시 일치를 확인했다. 확인 절차는 저장소 루트에서 위 두 버전으로 재현 명령 실행 → `<out>/reproduce.json`의 `passed=true`·`reproduced=true`·`games=50` 확인 → 아래 기대 해시와 대조 순서다.

```text
848dbdd20f02fefccda9bdbf4438830e844181949412bac7a5a70704bb58f8ca
```

`select`는 고정 설정의 모델을 실제로 열어 검증한 뒤 버전 두 개와 `False`를 반환한다. 파일·로더·모델 ID 오류는 `ModelUnavailable`, 의심 특징 불일치는 `FeatureVersionMismatch`다. 조회는 로컬 경로와 버전만 사용한다. 판별 인스턴스 수명·버전 고정과 카나리아는 B-09-01, 원격 레지스트리는 C-09-01에서 연결한다. BERT 해석기 연결은 D-09-02 4번, 동결 사람 평가는 D-09-01 범위다.

### 의심 점수 섞기 α (D-10-01, 11/11)

관문(`ml/gate.py`)은 그대로 두고 재학습(`python -m ml.suspicion.retrain`)이 섞는 비율 α를 고른다. 후보 하나의 최종 로짓은 `α × 모델 로짓/온도 + (1−α) × rule_score`다. α=1은 기존 모델, α=0은 규칙 의심도와 같은 확률이다.

- 규칙(도메인): `ml/suspicion/blend.py`. α 후보는 0.0~1.0의 0.1 간격 11개다. 관문 benchmark 사유가 없는 α 중 log_loss가 가장 낮은 것을 고르고, 같으면 큰 α를 고른다. 고른 α가 0.3 미만이면 `alpha_too_small`, 규칙 점수 대비 log_loss 개선이 0.01 미만이면 `no_meaningful_gain`이다. 이 경우 관문을 통과해도 `accepted=false`라 승격하지 않는다.
- 흐름(응용): `ml/suspicion/retrain.py`. α마다 같은 시드로 benchmark 봇 판을 다시 돌려 마을 승률을 재고, 기준선 판의 관측으로 예측 지표를 잰다. 고른 산출물이 `trained/models.pkl`이고, 원래 모델은 `trained/models.base.pkl`이다. `decision.json`의 `accepted`는 관문 통과이면서 유효 후보일 때만 참이다. 관문 결과는 `gate_accepted`, α 탐색 표는 `blend_search`, 고른 α는 `blend_alpha`, 규칙 대비 개선폭은 `log_loss_gain_vs_rule`에 기록된다. 같은 값이 MLflow 태그 `paldo.blend_alpha`·`paldo.blend_search`·`paldo.promotable`에도 들어간다.
- 서빙(인프라 어댑터): `ml/adapters/suspicion_model.py`가 산출물의 `blend_alpha`를 읽는다. 값이 없는 기존 산출물(운영 `suspicion-logistic-9bbee626f29b` 등)은 α=1로 읽어 기존 계산 그대로이고, 0~1 밖이면 불러오지 않는다.
- 관문 기준(reference): `--production-model <운영 models.pkl>`을 주면 지금 운영 모델이 기준이다. benchmark는 같은 행·같은 seed로 채점하고 마을 승률은 운영 모델로 같은 seed 대전을 다시 돌려 잰다. 운영 모델이 없을 때만 규칙 점수(rule-v0)가 기준이다. α의 유효 후보 조건(규칙 대비 0.01 개선)은 기준과 별개로 규칙 점수와 비교한다. `decision.json`의 `reference_model`.

### 사람 판 학습·시험 (D-10-01, 11/11)

매일 밤 재학습이 운영의 사람 판을 자동으로 꺼내 학습과 시험 세트에 쓴다. 관문(`ml/gate.py`)은 그대로라 사람 판 시험 세트가 없으면 `human:invalid_or_missing_measurement`로 거부한다.

- 내보내기(인프라): `python -m server.infra.ut_export training --out training.json`. 사람 판(`source=human`)이면서 부하 시험이 아니고 종료·진실 이벤트가 있는 판만, 아래 필드만 읽는다.
- 변환(도메인): `ml/suspicion/features.py` `human_rows(games, consent, deleted)`. 동의한 가명만 쓰고 삭제 요청 가명은 뺀다. 봇 판과 같은 검사·특징 추출이고 출처 `human`, 분할 키 `user`=가명만 다르다. 검사에 걸린 판은 밤 실행을 멈추지 않고 사유별로 빼서 센다(`invalid:<예외>[:<검사 문구>]`, 검사 문구는 코드에 고정된 문구일 때만 붙여 입력값이 남지 않게 함·`incomplete`·`no_consent`·`deletion_request`·`load_test` 등). 정책 버전은 판마다 하나여야 하고 판끼리는 달라도 된다. 웹 판은 봉투 `npc_policy_versions`가 비어 있어(게임 메타에 기록하지 않음) 결정의 정책 버전(`-fallback` 제외)과 진실 이벤트의 역할 명단으로 채운다(`policy_versions_inferred`). 판 안 결정 버전이 둘 이상이면 거부한다.
- 분할(도메인): `ml/suspicion/evaluate.py` `human_side`. 사람(가명) 단위로 `sha256("suspicion-human-split-v1"||가명)` 앞 8바이트가 0.3 미만이면 시험, 아니면 학습이다(`docs/contracts/events.md` 3절). 결과가 가명과 고정 문자열에만 달려 있어 새 사람이 늘어도 기존 사람은 옮겨지지 않고, 상태 파일이 없어 삭제 요청 때 지울 명단도 없다. 비율을 바꾸면 고정 문자열 버전을 올린다.
- 흐름(응용): `python -m ml.suspicion.retrain ... --human-games training.json --consented-users consent.json --tombstones <삭제 요청 폴더> [--production-model <운영 models.pkl>]`. 학습 쪽 사람 행은 봇 학습 행과 합쳐 `dataset_train/`으로 학습한다. 시험 쪽은 후보(고른 α)와 기준을 같은 행·같은 seed로 채점해 관문 `human` 지표를 만든다. 사람 판은 이미 끝난 판이라 마을 승률은 양쪽 모두 그 판들의 실제 결과다. `decision.json`의 `human`에 학습/시험 판·사람·행 수와 제외 사유별 수, `human_evaluation`에 `provided`·`no_human_test_games`·`no_human_input`이 남는다.
- 판 수가 적을 때: 시험 세트가 50판 미만이면 관문이 무작위 재표본 95% 구간(bootstrap)이 기준보다 확실히 낮을 때만 통과시킨다. 사람 판이 쌓일수록 통과 가능성이 생긴다. 시험 쪽이 0판이면 human 지표를 만들지 않고 관문이 거부한다.

#### 학습용 내보내기 필드(개인정보 검토용)

| 수준 | 내보내는 것 | 빼는 것 |
|---|---|---|
| 판 | 판 ID, 가명(`player_pseudonym`) | 세션 ID, 시드·생성 시각 등 판 메타, 명령 응답 |
| 이벤트 종류 | NPC 관측(`observation`), NPC 결정(`decision`), 종료(`game_ended`), 진실(`truth_revealed`) | 그 밖의 모든 이벤트(플레이어 발화·명령 등 입력 문장이 담기는 종류) |
| 이벤트 봉투 | 이벤트 ID·순번·시각·국면·프리셋·규칙/콘텐츠/정책/모델 버전·본문·가명·부하 시험 표시 | `session_id`, `command_id` |

관측 본문은 NPC ID·장소·수치·이유 코드로 된 구조화 기록이고 자유 입력 문장·계정·IP는 없다. 가명은 동의 확인, 삭제 요청 반영, 사람 단위 분할에만 쓰고 `decision.json`·로그에는 가명을 쓰지 않고 수만 남긴다.

### 광고 CTR (D-10-02)

```powershell
python -m ml.ad_ctr --input <확정-Delta-export>/ctr.json --source synthetic --eval-from 2026-11-04 --out out/ctr-report.json --register
python -m tools.ad_ctr_scenario score --out out/d1002/scenario --report out/ctr-report.json
```

`--register` 실행 전에 격리 `MLFLOW_TRACKING_URI`를 지정한다. 학습은 지면·소재·판 결과·프리셋·귀속 시간대를 사용한다. 모델 파일과 범주 인코딩, 학습 Delta 버전, 코드 SHA, 같은 보류 노출의 베타/LightGBM 로그 손실 및 ECE를 기록한다. 기준은 로그 손실 증가 0.01 이하와 ECE 0.05 이하다. 평가 노출이 모두 베타 폴백이면 거부한다. 거부한 실행은 레지스트리 후보를 만들지 않는다. 카나리아는 학습에서 본 특징 조합만 적용하고 다른 조합은 베타로 폴백한다. 시나리오 6에서는 별도의 평가 시드와 숨긴 확률을 사용해 승리·패배별 CTR 오차를 잰다. [D-10-02 증빙](../evidence/D-10-02.md)

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

어댑터의 추론 응답 대기 마감은 90ms, native 초기화·워밍업을 포함한 로드 마감은 30초다. 고정 주민 수의 로컬 순차 IPC에서 검증했으며 웹 동시성·큰 메시지의 절대 wall-clock 상한은 별도 검증이 필요하다. 특징 버전·열 순서·보정 온도는 D-07-02 산출물과 기존 특징 모듈로 검증한다.

## 한계
운영에서는 게임 웹 서버의 매일 01:31(서울) 재학습 실행이 관문을 통과한 의심 모델 후보를 MLflow에 등록한 뒤 자동으로 설치·승격한다(사람 승인 없음). 승격은 운영 설정의 모델 버전을 바꾸고 웹 프로세스를 재시작한 뒤 healthz와 실제 추론으로 확인하며, 실패하면 직전 모델로 복원한다. 관문에서 떨어지면 기존 모델을 그대로 쓴다. 이 흐름에는 카나리아 단계가 없다(계약 [ml_lifecycle.md](../contracts/ml_lifecycle.md) 3~4절의 카나리아 승격은 이 야간 흐름에 적용하지 않는다). 승격 스크립트는 팀 비공개 운영 폴더에 있고 저장소에는 없다. `ops/k3s/cronjobs/retrain.yaml`은 k3s 대안 정의라 꺼진 채로 둔다. 사람 판 관문 측정은 시험 세트 판 수가 적어(11/12 기준 10판) 개선 판정이 보수적이다.
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
| D-08-01 | 11/9 | 병합된 Selector와 실제 후보 50판 반복 재현. 격리 웹 부하의 실제 엔진·모델 모드 및 프로세스별 추론 집계 추가 |
| D-08-01 | 11/10 | PM 장애 정책의 버전별 공유 worker·동일 버전 복구·연속 실패 대기와 부하 계측. 공통 처리 최적화 후 동급 VM 모델 켬 재측정: API p95 495.806ms·오류 0건, 폴백률 3.846%로 조건 6 미충족 |
| D-08-01 | 11/11 | PM 결정에 따라 운영·부하 시험의 로지스틱을 웹 프로세스에서 직접 추론하고 두 버전 상주 RSS를 측정 |
| D-08-04 | 11/8 | 로컬 Selector의 고정 버전 조회·특징 버전 검사와 A·B 재현 절차 추가 |
| D-08-04 | 11/9 | A 쪽 재현 명령 `reproduce_model_games`(같은 모델·시드 두 실행 비교, 실제 v0 수령 전 가짜 모델 시험, E) |
| D-08-04 | 11/9 | 사용자 변경 기준 적용, 실제 로컬 모델 인계와 A CLI·B 조립 50판 비교 완료 |
| D-10-02 | 11/9 | 확정 Delta 맥락 CTR 학습, 동일 베타 평가, LightGBM 관문·MLflow 후보·시나리오 6 채점 |
| D-09-01 | 11/11 | 공식 동결 평가의 기준선 유지 판정과 D-07-03 체크포인트 NAS 인계 |
| D-10-03 | 11/10 | 11/11 dev 반영, 버전 제한 JSON 승률 추론·복기 API·holdout 지표·패널, 표 기반 자기 대전 1,000판 비교, 프리셋·미학습 비밀 조합 및 무감금 상한 검증 |
| D-10-01 | 11/11 | 관문은 그대로 두고 재학습이 의심 점수 섞기 α를 고름(유효 후보 α≥0.3·규칙 대비 0.01 개선), 서빙 `blend_alpha` 하위 호환, 사람 판 평가 입력 |
| D-10-01 | 11/11 | 매일 밤 재학습이 동의한 사람 판을 학습용 내보내기로 꺼내 사람 단위 30%를 시험 세트, 나머지를 학습에 섞음. 관문 기준을 지금 운영 모델로(없으면 규칙 점수). 관문 규칙은 그대로(E 보완) |
| D-11-01 | 11/11 | 광고 시나리오 톰슨 관측·원장 내보내기와 Delta 건별 대사, 공식 의도 재학습과 광고 1~6 최종 재실행 |
| D-10-01 | 11/11 | 사람 판 변환: 웹 판의 빈 봉투 정책 버전을 결정 버전으로 채움(운영 23판이 전부 `invalid`로 빠지던 원인), 제외 사유를 검사 문구별로 세분(E 보완) |
| D-10-01 | 11/12 | 운영 매일 밤 재학습의 관문 통과 시 자동 승격·실패 시 직전 모델 복원(카나리아 없음)을 한계 절에 반영. 이전 "자동 승격 안 함" 문구를 운영 동작에 맞춤 |

모델 판 실행은 현재 씐 자 1명 logistic 모델을 연결한다. 학습 산출물의 근거별 기여값이 없어 빈 매핑을 반환한다. 실측과 완료 조건은 [D-08-01 증빙](../evidence/D-08-01.md)을 참조한다.

승리 확률은 `python -m ml.win_probability`로 학습한다. `model.json`은 유한 계수·특징·규칙·콘텐츠·프리셋과 holdout 지표를 포함한다. `server/infra/win_probability.py`가 표준 라이브러리로 복기 `win_prob`를 계산하고 offline_holdout Prometheus gauge를 제공한다. `PALDO_WIN_PROBABILITY_MODEL`은 신뢰된 로컬 JSON 경로이며 기본은 빈 값이다. 종료된 자기 판만 API가 반환하고, D3 결정의 화면 그래프 숨김을 적용한다. Grafana `model.json`의 비교 패널과 운영 설정·배포는 별도 수신 검증이 남아 있다.

`python -m ml.selfplay --out <new directory> --record`는 양 진영의 표 기반 투표 정책을 1,000판 학습하고, 표를 고정해 A 규칙과 동일 시드 1,000판씩 비교한다. 추정 모델은 양쪽 FakeSuspicionModel, 플레이어는 LogicBot이다. `python -m ml.preset_report --out <new directory> --model <trusted models.pkl> --training-events <official events.jsonl> --record`는 현재 콘텐츠 배정 검사와 각 500판 개발 측정, fit·온도 보정 조합을 뺀 비밀 조합 평가를 수행한다. content-1.1에서 short5·base6·base7·large8을 측정했다. CLI는 새 출력 디렉터리를 요구한다.
