# 기준선 의도 해석기 B 인계 — D-07-01

D-09-02의 로컬 BERT 후보는 `intent.bert.BertIntentParser(run_dir)`다. 검증한 D-07-03 아티팩트로 생성해
기존 `build_from_runtime(runtime, parser)`에 주입한다. 11/9 실제 체크포인트와 통합 테스트 4개로 연결 검증을 통과했다.
기본 운영 조립은 기준선을 유지하며 B가 후보를 주입해 실제 웹/API를 검증한다. 상세 아티팩트·판 버전 고정·B 수신 검증은
[D-09-02 인계](../docs/reports/D-09-02-handoff.md)를 따른다. 사람 평가는 D-09-01 범위다.

실제 구현은 `intent.parser.BaselineIntentParser`, 포트는 `engine.ports.IntentParser`다. `parse(text, ParseContext)`는 확인용 후보를 반환하며 명령을 실행하거나 행동을 소비하지 않는다. 후보의 `needs_confirmation`은 항상 true다.

## 한 줄 전환

B의 서비스 조립 코드에서 아래 **import 한 줄**을 선택한다. 새 환경 변수나 이미 존재하지 않는 Django 설정을 가정하지 않는다.

```python
from intent.parser import BaselineIntentParser as ParserImpl  # 실제 기준선 선택 줄
```

가짜 구현으로 돌아갈 때는 같은 줄을 아래로 바꾼다.

```python
from engine.testing.fakes import FakeIntentParser as ParserImpl
```

그 아래 생성·주입 코드는 두 경우 모두 같다. `game`과 `store`는 B가 기존 서비스 조립에서 준비한 게임 서비스와 상호작용 저장소다.

```python
from server.app.interactions import InteractionService

parser = ParserImpl()  # 프로세스 조립 시 한 번 생성. 요청마다 다시 만들지 않는다.
game.interactions = InteractionService(game, store, parser=parser)
```

현재 B의 `InteractionService.__init__(game, store, parser=None, renderer=None)`가 이 주입 지점을 제공한다. 위 코드는 parser 주입의 최소 예이며, B가 이미 renderer를 주입하고 있다면 그 인자는 유지한다. B-07-01에서 자신의 실제 조립 코드와 화면 테스트에 연결한다. 이 인계 작업에서 B의 파일을 대신 수정하지 않는다.

## 실행 환경·계약 검사

- Python 3.14, 기준 패키지는 `requirements/ml.txt`의 scikit-learn 1.9.1이다. 기준선 자체는 PyTorch·transformers·LLM·DB를 사용하지 않는다.
- 기본 생성자는 `fixtures/intent/seed_examples.jsonl`을 읽어 문자 TF-IDF와 로지스틱 회귀를 학습한다. 생성 비용과 `parse` 1회 지연은 다르다.
- 입력 문맥에는 공개 기록·활동 인물 별칭·선택 인물/카드·직전 상대 등 `ParseContext` 필드만 넣는다. 진실 역할·NPC 개인 상태는 주지 않는다.
- 결과 `versions`의 model·tokenizer·alias_dict·matcher·threshold를 B가 의도 시도 이벤트에 보존한다. 버전 문자열은 실행 환경·문맥에 따라 달라질 수 있으므로 고정값으로 덮어쓰지 않는다.

저장소 루트에서 해당 환경의 Python으로 실행한다.

```powershell
python -m unittest tests.intent.test_contract.RealIntentContract -v
python -m unittest discover -s tests/intent -t . -v
```

첫 명령은 `tests/contract/test_fakes.py::Intent`의 기존 테스트 3개를 상속하고, 생성자만 실제 기준선으로 바꿔 그대로 검증한다. 원본 가짜 계약 테스트는 다른 흐름이 계속 사용할 수 있도록 유지한다.

## 인계 범위

- `intent/parser.py` 구현은 dev에 이미 병합됐다. 실제 병합은 2026-11-06 10:45:27 KST, PR #177의 병합 커밋 `26d3c7d414dec6f5ef8eb2e77746d6adad6cba0e`다. D-07-01의 11/7 11:00 인계 시한보다 이르다.
- B 쪽 전환·자유 입력 화면 테스트의 수신 확인은 B-07-01의 완료 조건이다. D-07-01이 B의 수신 확인을 기다리지는 않는다.
- BERT 후보를 평가하는 방법은 아래 D-09-01 명령을 따른다.
- 전체 설명·기준선의 한계·성능은 D-05-01 증빙을 따른다. 작은 시드로 계약 시나리오를 통과한 것이 일반 문장 성능 합격을 뜻하지 않는다.

## D-09-01 개발 세트 평가

Python 3.14 CPU 추론 환경과 D-07-03 `run_dir`을 [D-09-02 인계](../docs/reports/D-09-02-handoff.md)대로 준비한다.

```powershell
$env:PALDO_BERT_TESTS = '1'
python -m unittest tests.intent.test_bert.BertRuntimeTests -v
python -m tools.perf.intent_human_eval --run <run_dir> --dev --n 200 --threads 4
```

개발 142행 자체의 유형별 분모와 정확도는 `*_split_*` 지표다. 별칭·대명사·생략은 예시의 `notes` 유형, 부정은 명시 태그 또는 부정 표현, 질문·주장은 두 형태를 함께 가진 대조 그룹, 다중 의도는 복수 정답 명령으로 분류한다. 해당 행이 없는 유형은 분모 0으로 기록한다. 별도 12개 반례 픽스처의 측정은 기존 지표로 확인한다.

동결 전에는 E-08-10 사전 테스트 3판의 `games.json`과 5문장의 사람 주석·당시 공개 문맥으로 시험 실행한다. 아래의 PM 시험 모드는 실제 내보내기의 시도 ID·가명·판·마스킹된 문장을 주석과 대조하고 결과를 `pm_smoke.json`에 따로 기록한다.

```powershell
python -m tools.perf.intent_human_eval --run <run_dir> --human <pm_annotations.jsonl> --contexts <pm_cases.jsonl> --pm-smoke <games.json>
```

## E-09-02 동결 세트 평가

`tools.ut_freeze`는 마스킹된 원문과 분할 정보를 동결한다. PM 시험과 동결 평가의 주석 형식은 `IntentExample` JSONL(`split=test_human`, `label_source=human_annotation`, `split_key.user`와 `game_id` 포함)이다. `example_id`는 내보낸 `attempt_id`와 같다. 행마다 같은 `example_id`로 공개 `ParseContext`와 주석된 `Command` 배열을 담은 별도 JSONL을 제공한다. `context`는 발언 당시 별칭, 공개 기록, 선택된 인물과 카드, 직전 상대를 담는다. `commands`는 미지원일 때 빈 배열이며, 여러 의도일 때 모두 포함하고 주 라벨에 해당하는 명령을 먼저 둔다. 인물과 근거는 문맥의 실제 ID를 사용한다. 주석과 공개 문맥 파일은 로컬의 Git 제외 경로로 인계한다.

`--manifest`에는 `tools.ut_freeze`가 만든 공식 `manifest.json`을 지정한다. 같은 폴더의 `test_human.jsonl`, `train_candidates.jsonl`, `assignment.json`, `report.json` 네 파일의 크기·SHA-256을 검증한다. 주석된 평가 행은 동결본과 ID·원문·사용자·판이 일치해야 하며, 학습용 문장·사용자와 겹치면 거부한다. PM 포함/제외는 동결본의 `pm` 필드를 따른다. 주석·문맥·원본 학습 파일의 해시도 실행 기록에 보존한다. D-10-01 9번에서 분할을 실행하고 E-09-02가 검수·판정·동결을 확인한다.

```powershell
python -m tools.perf.intent_human_eval --run <run_dir> --human <human_annotations.jsonl> --contexts <public_cases.jsonl> --manifest <freeze_dir/manifest.json>
```

`--training-data` 기본값은 `content/intent/examples_v1.jsonl`이다. 개발 비교는 기존 학습 390행으로 맞춘 TF-IDF, 사람 평가는 현재 웹의 시드 기준선을 사용한다. 임계값은 체크포인트에 고정한다. 명령 전체 일치에는 복수 후보의 모든 명령과 슬롯이 포함된다. 슬롯별 지표는 단일 명령 행으로 계산한다. 200행 미만이면 같은 시드로 사용자·판·계보·대조쌍 묶음을 2,000회 복원추출한다. 두 구간이 겹치거나 경계가 닿으면 개선 아님이다. PM ID를 받으면 제외한 값도 따로 낸다. `mlruns/intent_human_eval/`에 라벨별 오류와 판정, `docs/evidence/perf/D-09-01/`에 집계값을 쓴다. 종료 코드 2는 후보 실행 불가를 뜻한다. 운영 전환은 수명주기 계약의 별도 절차에서 판단한다.
