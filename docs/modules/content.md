# content — 게임 콘텐츠 묶음과 검사

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

게임이 쓰는 인물·성향·비밀·원한 단서·장소·템플릿 데이터(`content/bundle_v1.json`)와 그 검사 도구 `tools/check_content.py`. 데이터 형식은 도메인 계약 `engine/domain/content.py`의 `ContentBundle`이고, 엔진은 인프라의 `ContentSource`로 이 파일을 읽는다. 원전·창작 구분과 작성 규칙은 [`content/README.md`](../../content/README.md).
계층: 데이터 / 검사 도구는 `load_bundle`(인프라) / `content_problems`·`count_violations`(도메인 규칙 호출) / `run`(응용) / `main`(표현).

## 왜

- 근거: 기획서 3.3(창귀 설정), 4.6(비밀), 5.3(성향), 10장 / 계약 [`docs/contracts/content.md`](../contracts/content.md).
- 콘텐츠를 코드가 아닌 한 JSON 묶음으로 두어 엔진·의도 해석·에셋이 같은 인물 ID를 쓴다(픽스처 `fixtures/content_min/`과 같은 ID).
- 별칭은 의도 해석기가 부분 문자열로 인물을 찾으므로 전체 고유·서로 포함 금지로 검사한다. 같은 시간대·같은 실제 장소의 두 비밀은 충돌로 적게 해, 두 보유자가 서로를 목격해 거짓이 함께 깨지는 배정을 막는다.

## 사용법

```
python -m tools.check_content                                         # 구조 검사(엄격 검증·참조·별칭·충돌)
python -m tools.check_content --preset base6 --seeds 200              # + 엔진 배정기로 시드 200개 배정 검증
python -m tools.check_content --preset base6 --seeds 200 --result     # 결과 JSON(docs/evidence/perf/E-05-03/)
```

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `content_version` | `content-1.0` | 첫 플레이 버전 묶음 | 내용을 바꾸면 올린다 |
| 묶음 크기 | 인물 6·성향 4·비밀 6·원한 단서 4·장소 5·템플릿 11 | 6인 판 `base6`과 5인 판 `short5`를 채운다 | 7·8인 판 인물 2명은 E-08-02에서 추가 |
| `--bundle` | `content/bundle_v1.json` | 검사할 묶음(저장소 기준 경로) | - |
| `--preset` | 없음 | 배정 검증할 프리셋 ID(예: `base6`) | 없으면 구조 검사만 |
| `--seeds` | 200 | 배정 검증 시드 수(0부터) | 늘리면 검사 시간이 늘어난다 |

## 한계

- 성향 값은 콘텐츠 픽스처와 같은 초안이고, 시뮬레이션(A-08-03)으로 조정하기 전이다.
- 템플릿은 픽스처의 최소 세트다. 행동 유형 × 성향 템플릿은 E-06-01에서 바꾼다.
- 배정 검증은 엔진의 배정기(`engine.domain.engine.make_setup`)가 있어야 돈다.
- 인물 외형은 `ContentBundle`에 필드가 없어 `content/README.md` 표에 둔다(검사 대상 아님).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-03 | 11/5 | 처음 작성: 첫 플레이 버전 `bundle_v1.json`(인물 6·성향 4·비밀 6·원한 단서 4, 창작 설정만)과 `check_content`(엄격 검증·참조·별칭·충돌 제약·엔진 배정기로 base6 시드 200개 배정 검증) |
