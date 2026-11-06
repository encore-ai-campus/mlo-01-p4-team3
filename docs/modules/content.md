# content — 게임 콘텐츠 묶음과 검사

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

게임이 쓰는 인물·성향·비밀·원한 단서·장소·템플릿 데이터(`content/bundle_v1.json`)와 그 검사 도구 `tools/check_content.py`. 데이터 형식은 도메인 계약 `engine/domain/content.py`의 `ContentBundle`이고, 엔진은 인프라의 `ContentSource`로 이 파일을 읽는다. 원전·창작 구분과 작성 규칙은 [`content/README.md`](../../content/README.md).
계층: 데이터 / 검사 도구는 `load_bundle`(인프라) / `content_problems`·`count_violations`(도메인 규칙 호출) / `run`(응용) / `main`(표현).

이 모듈의 데이터와 검사 도구는 세 가지다.

| 데이터 | 검사 도구 | 태스크 |
|---|---|---|
| 인물·성향·비밀·원한 단서·장소(`content/bundle_v1.json`) | `tools/check_content.py` | E-05-03 |
| 템플릿 대사(같은 파일의 `templates`, 47개) | `tools/check_templates.py` | E-06-01 |
| 의도 해석 학습 예시(`content/intent/examples_v1.jsonl`, 사람 작성 280행) | `tools/intent_examples.py` | E-06-02 |

- 템플릿: 회의 발언 5종(의심·감싸기·모순 지적·해명·침묵)과 캐묻기 반응 4종(해명·회피·역공·고백)을 공용 1개 + 성향 4종마다 1개(36개)로 둔다. 점괘 공개와 행적 진술(`t_whereabouts`)은 공용만 둔다. 템플릿마다 변형 2개 이상. 엔진은 지금 (행동 유형, 반응)이 맞는 첫 공용 템플릿의 `variants[0]`만 쓴다.
- 의도 예시: 의도 해석기(D, `intent/`)의 학습 데이터다. 형식은 `contracts.datasets.IntentExample`이고, 문장 속 인물은 `<P1>`·`<P2>` 자리표시자로 쓴다. 라벨 10종(`docs/contracts/intent.md` 1절)마다 24~33행이고, 같은 명제를 라벨만 바꾼 대조쌍(`contrast_group`)과 인물 해소 유형 표기(`notes`)를 둔다.
- 템플릿 검사 계층: `placeholder_fields`·`template_problems`(도메인 규칙) / `load_bundle`(인프라, `check_content` 재사용) / `run`(응용) / `main`(표현).
- 의도 예시 검사 계층: `note_info`·`label_counts`·`contrast_counts`·`type_counts`·`modern_counts`·`slot_errors`·`lineage_errors`·`duplicate_texts`·`summarize`(도메인 규칙, 행 목록만 받음) / `load_rows`(인프라, JSONL 엄격 로드) / `main`(응용·표현).

## 왜

- 근거: 기획서 3.3(창귀 설정), 4.6(비밀), 5.3(성향), 10장 / 계약 [`docs/contracts/content.md`](../contracts/content.md).
- 콘텐츠를 코드가 아닌 한 JSON 묶음으로 두어 엔진·의도 해석·에셋이 같은 인물 ID를 쓴다(픽스처 `fixtures/content_min/`과 같은 ID).
- 별칭은 의도 해석기가 부분 문자열로 인물을 찾으므로 전체 고유·서로 포함 금지로 검사한다. 같은 시간대·같은 실제 장소의 두 비밀은 충돌로 적게 해, 두 보유자가 서로를 목격해 거짓이 함께 깨지는 배정을 막는다.
- 템플릿(기획서 10장 "행동 유형 × 성향", 계약 `content.md` 1절·4절): 캐묻기 반응은 모든 변형에 `{fact}`를 넣어, LLM이 실패해 템플릿으로 폴백해도 플레이어가 받는 정보량이 같게 한다. 자리표시자는 엔진이 실제로 넘기는 인자(쓰임별 허용 표, `tools/check_templates.py` 머리말)만 쓰게 해, 오탈자가 실행 중 `format` 오류로 나는 것을 검사에서 먼저 잡는다.
- 의도 예시(기획서 8.1, 계약 `intent.md` 1·2절): 같은 단어라도 문장 형태나 부정 여부에 따라 라벨이 달라지므로("<P1>이 수상하오" 의심 ↔ "<P1>은 수상하지 않소" 감싸기 ↔ "<P1>이 수상하다고?" 의심 이유 캐묻기), 이 경계를 배우게 대조쌍을 라벨마다 6쌍 이상 둔다. 대명사·생략·미등록 별칭처럼 문장만으로 인물을 못 정하는 입력은 `notes`에 해소 맥락(`ctx`)을 적어 해석기의 해소 순서(`intent.md` 2절)를 시험할 수 있게 한다.
- 의도 예시 표현 기준(11/6 사용자 결정): 하오체 행에는 외래어·현대어와 지도에 없는 장소·직업을 쓰지 않는다. 점괘는 사람이 아니라 점괘·기운을 주어로 "탁하다·맑다"고 쓴다. 실제 플레이어는 현대어로 입력할 수 있으므로 현대어 입력 행(`register=modern`, 26행)을 따로 두고, 역할 단어("씐 자", "창귀") 행(26행)도 넣는다. 학습 데이터에만 쓰고 화면에는 나오지 않는다.

## 사용법

```
python -m tools.check_content                                         # 구조 검사(엄격 검증·참조·별칭·충돌)
python -m tools.check_content --preset base6 --seeds 200              # + 엔진 배정기로 시드 200개 배정 검증
python -m tools.check_content --preset base6 --seeds 200 --result     # 결과 JSON(docs/evidence/perf/E-05-03/)
python -m tools.check_templates                                       # 템플릿 검사(필수 조합·자리표시자·{fact}·변형 수)
python -m tools.check_templates --result                              # 결과 JSON(docs/evidence/perf/E-06-01/templates.json)
python -m tools.intent_examples                                       # 의도 예시 집계(라벨·대조쌍·해소 유형·현대어 행 수)를 출력
python -m tools.intent_examples --write examples_v1                   # 결과 JSON(docs/evidence/perf/E-06-02/examples_v1.json)
python -m unittest tests.tools.test_check_templates tests.intent.test_examples_v1   # 규칙 단독 테스트와 파일 조건
```

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `content_version` | `content-1.0` | 첫 플레이 버전 묶음 | 내용을 바꾸면 올린다 |
| 묶음 크기 | 인물 6·성향 4·비밀 6·원한 단서 4·장소 5·템플릿 47 | 6인 판 `base6`과 5인 판 `short5`를 채운다 | 7·8인 판 인물 2명은 E-08-02에서 추가 |
| `--bundle` | `content/bundle_v1.json` | 검사할 묶음(저장소 기준 경로) | - |
| `--preset` | 없음 | 배정 검증할 프리셋 ID(예: `base6`) | 없으면 구조 검사만 |
| `--seeds` | 200 | 배정 검증 시드 수(0부터) | 늘리면 검사 시간이 늘어난다 |
| `MIN_VARIANTS`(`check_templates`) | 2 | 템플릿별 최소 변형 수(README 7절 P-15, 잠정) | 올리면 모든 템플릿에 변형을 더 써야 한다 |
| `--bundle`(`check_templates`) | `content/bundle_v1.json` | 템플릿을 읽을 묶음 | - |
| `--path`(`intent_examples`) | `content/intent/examples_v1.jsonl` | 검사할 의도 예시 파일 | - |
| `--write`(`intent_examples`) | 없음 | 결과 JSON 이름(`docs/evidence/perf/E-06-02/<이름>.json`) | 없으면 화면 출력만 |
| `MAX_VARIANTS`(`intent_examples`) | 3 | 사람 작성 행 1개당 변형 행 최대 수(계보 검사) | 2단계 변형 생성의 상한 |
| `REGISTERS`(`intent_examples`) | `modern` | `notes`의 `register` 값 | 새 말투 구분을 넣으면 여기에 더한다 |

## 한계

- 성향 값은 콘텐츠 픽스처와 같은 초안이고, 시뮬레이션(A-08-03)으로 조정하기 전이다.
- 엔진은 지금 첫 공용 템플릿의 `variants[0]`만 쓴다. 성향별 템플릿 선택은 A-07-01(규칙 v0, 7번)에서 들어오고, 2번째 변형부터는 고르는 경로가 아직 없다(검사만 미리 한다).
- 의도 예시는 사람 작성 1단계만 있다. 로컬 LLM 변형 생성과 D 검수(E-06-02 조건 2)는 E-08-10 뒤에 하며, 그때 인물 이름이 섞인 잡담 변형을 늘려 이름만 보고 의심·감싸기로 오분류하는 것을 줄인다. 그래서 E-06-02 증빙은 `상태: 실패`(이슈 `evidence-fail`)다.
- 의도 예시의 점괘 슬롯은 시드와 같은 평평한 구조라 `night`가 선택이다. 계약 `intent.md` 1절의 `divination(target, night, result)` 표기와 문구가 다르다(PM이 계약 문구로 맞출 예정).
- 인용 질문형 중 "~라고?"·"~다니?" 꼴은 해석기 규칙(`intent/parser.py`의 인용형 정규식)이 아직 잡지 않아 모델 예측에만 맡겨진다(D 후속).
- 배정 검증은 엔진의 배정기(`engine.domain.engine.make_setup`)가 있어야 돈다.
- 인물 외형은 `ContentBundle`에 필드가 없어 `content/README.md` 표에 둔다(검사 대상 아님).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-03 | 11/5 | 처음 작성: 첫 플레이 버전 `bundle_v1.json`(인물 6·성향 4·비밀 6·원한 단서 4, 창작 설정만)과 `check_content`(엄격 검증·참조·별칭·충돌 제약·엔진 배정기로 base6 시드 200개 배정 검증) |
| E-06-01 | 11/6 | 템플릿 47개(회의 발언 5종·캐묻기 반응 4종 × 공용 + 성향 4종, 점괘 공개·행적 진술 공용, 변형 2개 이상)와 `check_templates`(필수 조합·자리표시자·쓰임별 허용·`{fact}`·엔진 인자 `format`·변형 수) |
| E-06-02 | 11/6 | 의도 예시 `content/intent/examples_v1.jsonl` 사람 작성 280행(라벨 10종, 대조쌍, 해소 유형 표기, 역할 단어 행, 현대어 입력 행 `register=modern`)과 `intent_examples` 검사·집계. 변형 생성·D 검수는 E-08-10 뒤 |
