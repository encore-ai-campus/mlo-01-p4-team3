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

## B-09-01 밴딧 연결

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

## 사용자 수행 대기

RunPod 결제 설정·자동 충전 끔·잔액 알림·D 한도 $4.5 및 Restricted 키의 비공개 전달은 사용자 조건이다.
완료 증거를 받지 못했으므로 미충족이다. 결제·키 생성·전달을 대신 실행하지 않았다. 값 자체를 증빙에 넣지 않는다.
