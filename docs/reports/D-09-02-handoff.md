# D-09-02 인계

## D 제공 범위

`ml/gate.py`는 수명주기 2절의 순수 판정이다. `GateDecision.accepted`, `reasons`, `reference_version`과
`serving_version`을 반환한다. 거부 시 실행 담당자가 `reasons`를 MLflow 거부 태그에 기록하고 기존 모델을 유지한다.
이 코드가 모델을 자동 등록·승격하지 않는다. 후보와 기준선은 반드시 같은 동결 평가 ID/판/문장을 사용한다.
첫 비교 입력은 씐 자 `rule-v0`, 의도는 규칙 기준선이다. production 버전이 있으면 `current_version`을 넘긴다.

씐 자 입력은 `benchmark`와 `human` 각각 `games`, `log_loss`, `ece`, `village_win_rate`를 가진 사전이다.
50판 미만이면 양쪽 `bootstrap_95.log_loss=[lower, upper]`가 필요하다. 의도 입력은 `dataset="test_human"`,
`rows`, `command_exact_match`, `macro_f1`, `unsupported_false_positive_rate`, `contrast_accuracy`, `slot_errors`를 가진다.
`slot_errors` 키는 `kind, addressee, target, evidence, night, slot, divination, interrogation_kind` 전부다.
200문장 미만이면 양쪽 `command_exact_match_ci95`가 필요하다. 구간이 겹치거나 닿으면 거부한다.
소표본 개선 판단의 주 지표는 각각 log loss와 명령 일치율이다. 승률은 개선 목표가 아니라 안정성 제한이다.
테스트 픽스처는 실제 사람 평가나 모델 채택 증거가 아니다.

## B-09-01 밴딧 연결 (현재 담당 E)

11/9 PM 담당 변경에 따라 B-09-01 1~5번의 수신 담당은 E다([현재 태스크](../tasks/E_pm.md#b-09-01-모델-버전-적용업데이트-내역-표시)). 아래 판 시작 조립·버전 고정 절차는 E의 수신 검증 항목이다.

배치는 동의한 사람의 `paldo.game`/`paldo.player` 전체 종료 이력 export를 읽는다. 스트림 증분만 넣지 않는다.
`game_ended`(phase=ended, winner=village/possessed), `survey_submitted`를 판 ID로 조인한다.
사람 출처·동의·`load_test=false`만 사용하며 설문 의견은 읽어 보상에 넣거나 저장하지 않는다.
동의 목록은 회차/동의 원본을 소유한 B/C가 제공한다. 중단·미동의·부하·합성 판은 제외한다.

```powershell
python -m ml.mongo_bandit --events <private_full_events.jsonl> --consented-users <private_consent.json>
```

접속은 실행 환경의 `PALDO_MONGO_URI`로만 받는다. 저장 위치는 같은 DB의 `bandit_state` 컬렉션,
`_id="bandit"` 단일 문서다. 처음에는 기존 키의 거짓말 2 × 누명 3 × 답 의도 3 = 18개 프로필이며 밤 대상은 균등이다.
`--arms <json>`으로 초기 프로필을 명시할 수 있지만 기존 상태의 팔 구성은 바꾸지 못한다.
각 팔의 `alpha/beta`와 Thompson 표본을 정규화한 `ratios`, 버전별 반영 `game_ids`와 새 `reward_game_ids`를 저장한다.
버전은 `bandit-N`, 첫 버전 전은 `bandit-0`이다. `revision`은 Mongo CAS용이며 개인정보 정리 때도 증가하므로
서빙 버전으로 쓰지 않는다. 서빙 버전은 마지막 `versions[].version`이다.

```python
from ml.bandit import VersionedMixSource
from ml.mongo_bandit import MongoBanditStore

state = MongoBanditStore(database['bandit_state']).load()
version = state['versions'][-1]['version'] if state['versions'] else 'bandit-0'
source = VersionedMixSource(state, version)
mix = source.mix_for(game_id)
# B의 판 시작 조립에서 이 두 메타 값을 함께 고정한다.
meta.model_versions['bandit'] = version
meta.model_versions['bandit_arm'] = mix.mix_id
# A의 기존 주입점: RuleV0Policy(content, mix_source=source)
```

B는 판 메타에서 버전을 한 번 고르고, 해당 버전의 source를 그 판의 엔진에 넣는다. 진행 중인 판은 다시 읽지 않는다.
재시작 복구도 메타의 원래 버전으로 source를 만든다. `mix_for(game_id)`는 버전+판 ID로 같은 팔을 선택하고
엔진 `PossessedPolicy.mix()`도 판 동안 캐시한다. `model_versions`가 임의 문자열 사전이므로 포트 형식 변경은 없다.
추가 키 `bandit_arm`은 종료/설문 이벤트에도 그대로 복사해야 한다. 이 키가 없으면 학습을 거부한다.
종료 시각은 발생 UTC 시각, 동률은 game_id 정렬로 먼저 끝난 5판을 정한다. 새 응답 20판 전에는 쓰지 않는다.
설문 미도착/건너뜀도 p에는 포함되고, 늦게 온 최초 응답은 다음 갱신에 한 번만 반영한다.
가명은 제한 계산에만 쓰며 저장에는 팔별 집계·판별 해시·판 ID만 남는다. 개인 모델은 없다.

**B 수신 검증 대기**: 실제 웹 판 시작→전략 적용→종료/설문 Kafka→배치→다음 판,
기존 판 버전 유지, B-09-01의 ModelSelector와 결합 확인은 아직 하지 않았다. 이 문서가 인계 자료이며 외부로 전송하지 않았다.

## C 개인정보 삭제 연결

C가 실행 중 배치를 멈추고 재적재 필터를 먼저 등록한 뒤 참가자의 전체 판 ID로 `store.forget_games(game_ids)`를 호출한다.
버전의 판 ID·무결성 해시·개별 보상 기여 기록을 삭제하고, 다음 갱신의 alpha/beta에서 해당 기여를 뺀다.
이미 게시된 비율은 진행 중 판의 기존 모델이므로 유지하며, 다음 20응답 갱신은 정리된 posterior를 쓴다.
정리는 CAS/멱등이다. 삭제를 반영한 새 전체 export로만 배치를 재개한다. 삭제 전 입력을 가진 작업자는 재시작해야 한다.
상태 문서는 파일럿용 16 MiB 한계가 있다. 확대 운영 전 버전 컬렉션/트랜잭션으로 분리한다.

## B-09-02 BERT 연결

D-09-01 작업폴더의 `intent/bert.py`, 분류기 런타임 검증, 해당 테스트만 재사용했다. 원본은 그대로다.
프로세스 조립에서 `parser=BertIntentParser(run_dir)`, 기존 `build_from_runtime(runtime, parser)`로 주입한다.
현재 웹 기본 조립은 기준선을 유지한다. 버전별 파서 선택·판 고정·실제 API 지연 검증은 B 담당이다.
필수 아티팩트: D-07-03 `run.json`, model/config.json, model/model.safetensors, tokenizer.json,
tokenizer_config.json 및 함께 저장된 선택적 tokenizer 파일. 로더는 외부 다운로드나 원격 코드를 실행하지 않는다.
가중치 해시·10라벨 매핑·토크나이저 버전을 확인하고, 추론 실패는 `unparseable`, 빈 후보, 확인 필수로 끝낸다.

기존 5 epoch 후보는 `koelectra-intent-v0-7ec09f448fe3`이며 재학습하지 않았다.
11/9 재검증에서 기존 실제 체크포인트의 IntentParser 로드·대상 슬롯·확인 필수 응답이 통과했고 실물 통합 테스트 4개도 통과했다. 이전 WinError 4551은 같은 환경에서 재현되지 않았으며 정책 설정은 바꾸지 않았다. 83.80%는 개발 수치이며 사람 평가 합격을 뜻하지 않는다.
`test_human` 인계는 11/10 19:30, D-09-01 최종 평가는 11/11 09:00이다. 사람 평가 합격을 주장하지 않는다.

## RunPod 준비와 종료 확인

11/10 사용자 확인으로 선불 충전·자동 충전 끔·잔액 부족 알림·Restricted 설정 저장·E 수령은 완료했다. D 한도는 $4.5다. 조건 5의 남은 순서는 E의 실제 실험 종료·키 폐기 요청 확인 뒤 D가 전달 키를 폐기하고, 실험용 Network Volume 0개를 확인한 다음, Pod별 사용 시간·요금을 E에게 저장소 밖으로 전달하는 것이다. 실험 종료·폐기 요청과 종료 확인 증빙은 아직 받지 못했다. 키 원문은 기록하지 않는다.

## 11/10 로컬 BERT HTTP 수신 검증

최신 dev `5cbb14e`의 B 수신 시험에 실물 체크포인트를 주입했다. Django test client·임시 SQLite·녹화 엔진에서 31개 시험이 24.574초에 통과했고 건너뜀은 0개다. 그중 실물 체크포인트를 사용하는 RealBertScreens는 11개다. 새 회귀는 실제 BERT의 `훈장이 수상하다` 입력에서 `kind=suspect, target=hunjang`, 확인 필수, 시도 기록의 모델·토크나이저 버전, 취소 후 동일 state_version을 검증한다.

측정 당시 테스트 파일 SHA256은 `f59dbb3cef34d1b936c7bd9e6914905dffe9c7f2fcb02c0d81e3c9ccaa08279c`다. 결과는 [bert_receive.json](../evidence/perf/D-09-02/bert_receive.json)에 기록했다. 이 실행은 같은 VM의 웹 2프로세스·30명·3분 부하 측정을 포함하지 않는다.

### B가 재현할 설정

압축을 푼 `20261107T162208Z` 디렉터리에 run.json과 model/이 함께 있어야 한다. 운영 설정에 사용할 공개 무인자 팩토리는 `server.paldo_web.intent.make_bert_parser`다.

| 설정 | 값 |
|---|---|
| INTENT_PARSER_FACTORY | server.paldo_web.intent.make_bert_parser |
| INTENT_BERT_RUN_DIR | 압축을 푼 20261107T162208Z 절대 경로 |
| 모델 버전 | koelectra-intent-v0-7ec09f448fe3 |
| 입력 길이 / 임계값 | 64 / 0.2 |
| 최소 입력과 기대 후보 | 훈장이 수상하다 → suspect / hunjang / needs_confirmation=true |

PowerShell에서 시험용 경로만 지정해 수신 검증을 재현한다.

```powershell
$env:PALDO_BERT_RECEIVE_RUN_DIR = '<압축 해제 경로>/20261107T162208Z'
$env:OMP_NUM_THREADS = '1'
$env:MKL_NUM_THREADS = '1'
$env:HF_HUB_OFFLINE = '1'
python -B server/manage.py test server.tests.test_bert_receive --settings=server.paldo_web.test_settings --noinput --verbosity=2
```

검증 환경은 Python 3.14.6이다. 설치 기준은 requirements/ml.txt와 requirements/web.txt이며 다음 표는 실제 실행 환경에서 읽은 버전이다.

| 패키지 | 실행 버전 |
|---|---|
| torch | 2.15.0+cpu |
| transformers | 5.22.1 |
| tokenizers | 0.23.3 |
| safetensors | 0.8.0 |
| scikit-learn | 1.9.1 |
| numpy | 2.5.4 |
| django | 6.1.2 |
| joblib | 1.6.0 |
| prometheus-client | 0.26.0 |

### 로컬 인계 묶음

로컬 파일은 `koelectra-intent-v0-7ec09f448fe3.zip`다. 크기는 453038785 bytes, SHA256은 `dc8884daafeab7a0a42490c8ee5e11581412b9d390b819f26a32e89c504e7636`다. 원래 체크포인트 파일 5개, manifest.json, ML·웹 requirements를 담았다. 압축 내부 파일을 다시 읽어 아래 SHA256과 모두 일치함을 검사했다. 묶음 생성까지 완료했으며 B의 다른 장비 전달·수신 기록은 대기다.

| 아티팩트 상대 경로 | SHA256 |
|---|---|
| run.json | `520c1e03768e6dc26d6a44ba6076cff76733a4b10a00008432ad118fdab399fa` |
| model/config.json | `9add951f73b6e5d68f8d9ef2a8b0dae9b3aa57789f71d69c6f5e4317b094ec31` |
| model/model.safetensors | `7ec09f448fe3f768575227be0baebdb23408bf829968eb2694c29e39d18239d3` |
| model/tokenizer.json | `a9c958b7afb86ba4bd2b476e0ce39c131f3fd3d8f7146f77287e0830180f4515` |
| model/tokenizer_config.json | `cc777ac02b3331f4ae490a2e461c45d20d5ecd29b1e93b64e7c1567d59637293` |

## 11/10 로컬 종료·설문 이벤트 검증

dev 5cbb14e의 기존 웹 설문 시험에서 녹화 판을 시작·종료하고 HTTP로 설문을 제출했다. 시험 상태에 밴딧 버전과 팔을 고정한 경우, 웹이 생성한 paldo.game/game_ended와 paldo.player/survey_submitted가 같은 판의 보상 입력 1건으로 결합됐다. 설문 의견은 보상 입력에 들어가지 않았다. 기본 조립에서는 두 이벤트에 밴딧 귀속 값이 없었고, D 배치의 입력 검사가 이 이력을 거부했다. 두 경우를 모두 확인했다.

E의 판 시작 조립에서 bandit과 bandit_arm을 함께 기록한 뒤, 이 값이 있는 대상 판의 종료·설문 이력을 배치에 인계해야 한다. 전환 전의 값 없는 이력이 섞인 전체 export는 현재 입력 검사에서 거부된다. 소급해서 팔을 추정하지 않도록 대상 판의 범위를 E/C와 합의해야 한다. 실제 Kafka 발행·소비와 다음 판 반영은 남아 있다.

## 후속 완료 범위

- 조건 2의 실제 웹 판 시작 → 밴딧 버전/팔 고정 → 종료·설문 Kafka → 배치 → 다음 판 연결은 E 담당 B-09-01 수신 경로와 함께 확인해야 한다.
- B-09-02의 실제 엔진·동급 VM 30명3분 측정은 별도 조건이다.
- 11/10 사용자 확인: D 계정 RunPod 결제 설정·키 전달 완료. 선불 충전, 자동 충전 OFF, 잔액 부족 알림 ON, Restricted 설정 저장 및 E 수령을 확인했다. 첨부 편집 화면에서 GraphQL Read / Write는 전체 GraphQL 접근으로 표시되며, 화면에 Pod 전용 권한 항목은 없다. 실험 후 키 폐기, 실험용 Network Volume 0개 확인, Pod별 사용 시간과 요금의 E 전달은 대기다. 조건 5는 종료 증빙 대기 상태다.


## 11/11 dev 재검증

기존 작업 브랜치에 `origin/dev`의 `5156dec`을 반영했다. 관문·밴딧·실물 BERT 통합 23개, 실제 체크포인트 HTTP 수신 31개(실물 클래스 11개), 격리 Mongo 5개가 모두 통과했고 건너뜀은 0개다. [재검증 증빙](../evidence/D-09-02.md#1111-최신-dev-반영과-후속-착수-확인).

E-11-12 PR #346은 자유 대화 도구·평가 세트가 병합된 상태다. 실험 종료 여부는 병합과 별도로 확인해야 한다. E 담당 B-09-01의 밴딧 운영 주입·버전 고정은 현재 dev에서 확인되지 않아 조건 2의 실제 웹/Kafka 연결 검증은 대기다. RunPod 준비는 완료이며, 조건 5는 실제 실험 종료 후 확인만 남는다.


## 11/11 부분 PR 재검증

제출 직전 dev `2a79c3f`를 반영해 관문·밴딧·BERT 24개, 실제 체크포인트 HTTP 수신 33개(실물 클래스 12개), 격리 Mongo 5개가 통과했다. 건너뜀은 0개다. 최신 결과는 [D-09-02 증빙](../evidence/D-09-02.md#1111-부분-pr-직전-재검증)과 `dev_recheck.json`에 있다.

11/11 사용자가 E의 실제 실험 종료·키 폐기 요청을 아직 받지 않았다고 확인했다. 준비 완료와 종료 확인 대기를 구분한다. 조건 2의 E 밴딧 운영 주입·실제 Kafka 연결과 조건 5의 실험 종료 후 확인은 이번 부분 PR의 미완료 항목이다.
