# ci — CI 필수 체크와 증빙·문서화·비밀정보 검사

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

PR마다 필수 체크 4개(`core`, `django`, `evidence`, `docs`)를 돌려, 태스크 완료 여부를 사람이 결과물을 열지 않고 판정하게 한다(README 3절 ②③⑥⑦).

| 파일 | 계층 | 하는 일 |
|---|---|---|
| `.github/workflows/ci.yml` | 표현(진입점) | 이벤트별로 아래 도구를 부른다. PR 본문은 환경 변수 `PR_BODY`로만 넘긴다 |
| `tools/check_evidence.py` | 도메인 규칙 + 응용 + 표현(`main`) | 증빙 형식(`--format`), 완료 검사(`--complete`, 4절 규칙 1~6), PR 본문 문법(11절) |
| `tools/check_docs.py` | 도메인 규칙 + 응용 + 표현(`main`) | 문서화 검사(10절 규칙 1~6) |
| `tools/check_secrets.py` | 도메인 규칙 + 응용 + 표현(`main`) | 비밀정보 검사(E-05-02 조건 5), 예외 `tools/secrets_allowlist.txt` |
| `tools/evidence_md.py` | 도메인 | 증빙 마크다운 파서, "완료된 태스크" 판정(두 검사기가 함께 씀) |
| `tools/evidence_result.py` | 도메인(`build_result`) + 인프라(`write_result`) | 측정 결과 JSON(4절 형식) 쓰기 |
| `tools/gitrepo.py` | 인프라 | 파일·git 읽기(`GitRepo`). 규칙 함수는 이것을 직접 부르지 않고 인자로 받는다 |
| `tools/jsonlog.py` | 인프라 | 구조화 로그(5절) |

의존 방향: `main` → 응용(`complete`, `check_task`, `run` …) → 규칙 함수(문자열·목록만 받음). 인프라(`GitRepo`)는 응용이 받아 쓰고, 규칙 함수는 모른다. `check_evidence --complete`는 `check_docs.check_task`를 부르고, `check_docs`는 `check_evidence`를 부르지 않는다(10절 순환 금지).

## 왜

- 기획 근거: `docs/tasks/README.md` 3절(PM은 PR 병합·필수 체크·`--complete`만 본다), 4절(증빙 양식·결과 JSON·수치 표 규칙), 10절(문서화 기준), 11절(PR과 이슈 잇기). 태스크 정의는 `docs/tasks/E_pm.md` E-05-02.
- 수치 행을 결과 JSON과 대조하고 판정을 다시 계산하는 이유: ④ "성능 수치는 계측값만"을 손으로 고친 숫자로 우회하지 못하게 하려고. 반올림은 측정값 칸의 소수 자릿수에 맞춰 사사오입하고, 판정은 반올림하지 않은 값으로 한다(4절).
- `Closes`/`Refs` 외의 닫기 키워드, URL 형태 닫기, 한 줄에 번호 여러 개, **PR 커밋 메시지의 닫기 키워드(`Closes` 포함)**를 막는 이유: GitHub가 이 경로로도 이슈를 닫아 `--complete`를 거치지 않고 이슈가 닫히기 때문(11절 "커밋 메시지의 닫기 키워드 금지"). 이슈는 PR 본문의 `Closes #` 한 줄로만 닫힌다.
- 비밀 키 규칙을 "마지막 조각 일치"로 한 이유: 게임 용어 `secret_id`·`secrets`와 `TOKEN_TTL_MS`를 오탐하지 않기 위해(11/4 PM 결정, 조건 5).
- 표준 라이브러리만 쓴다(`docs/contracts/tech_stack.md`: 도구 코드는 외부 패키지 없음). CI에 설치 단계가 없다.

## 사용법

```
python -m unittest discover -s tests -t .                    # core 체크(검사 도구 자체 테스트 포함)
python -m tools.check_secrets                                # core 체크
python -m tools.check_evidence E-05-02 --format              # 양식만(상태: 실패 문서도 통과)
python -m tools.check_evidence E-05-02 --complete            # 4절 검사 규칙 1~6
python -m tools.check_evidence --all --until 11-05           # 예정일 11/5까지 태스크 전부 --complete
python -m tools.check_evidence --changed <base>              # base 이후 바뀐 증빙에 --format(push)
PR_BODY="$(cat body.md)" python -m tools.check_evidence --pr-body-env PR_BODY --changed <base>   # evidence 체크(PR)
python -m tools.check_docs --task E-05-02                    # 규칙 1~3
python -m tools.check_docs --all --until 11-05               # 규칙 4
python -m tools.check_docs --diff master                     # 규칙 5~6(커밋 전 변경·새 파일 포함)
```

측정 스크립트에서 결과 JSON 쓰기:

```
from tools.evidence_result import write_result
write_result("B-06-02", "api", {"api_p95": {"value": 412.3, "unit": "ms"}}, "python -m tools.perf.api_latency --seed 1")
# → docs/evidence/perf/B-06-02/api.json, 증빙 수치 행의 결과 파일 칸: docs/evidence/perf/B-06-02/api.json#api_p95
```

모든 도구는 통과 0, 실패 1을 돌려주고, 실패마다 `FAIL <파일:줄 또는 증빙 경로>: <이유>(규칙 번호)`를 출력한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| CI Python | `3.12` | `ci.yml`의 `setup-python` | 잠정값. ML·LLM 런타임은 11/5 3.14 단일로 확정했고(`tech_stack.md`), CI `core` 버전은 그 결정에 묶지 않고 PM이 따로 정한다(조건 1) |
| 서비스 이미지 | `mongo:8.0.34`, `mysql:8.4.13` | `django` 체크의 서비스 컨테이너 | `tech_stack.md`의 태그(개발 Compose와 같음). 바꿀 때는 tech_stack.md와 함께 |
| `PR_BODY` | - | `evidence` 체크가 읽는 PR 본문 환경 변수 | 이름을 바꾸면 `--pr-body-env` 인자도 같이 바꾼다 |
| `PALDO_LOG_DIR` | `out/logs` | 구조화 로그 파일 폴더(`ci.jsonl`) | 로그만 옮겨진다. 검사 결과와 무관 |
| `tools/secrets_allowlist.txt` | 4항목 | 한 줄에 경로·키 이름·사유(`\|`로 구분). 그 파일의 그 키만 허용 | 항목 추가는 PR 승인(⑧)으로 통제. 걸리지 않는 항목은 죽은 예외로 실패 |
| `tests/fixtures/secrets/` | - | 탐지 테스트용 가짜 값. 비밀정보 검사에서 이 폴더만 제외 | 다른 폴더에 가짜 값을 두면 검사가 실패한다 |
| base 커밋 | PR: `pull_request.base.sha`, push: `event.before` | 변경분 비교 기준(공통 조상 기준) | push의 `before`가 0으로만 된 SHA(새 브랜치)면 증빙 전부를 `--format` 검사 |

## 한계

- `django` 체크는 `server/manage.py`가 생기기 전까지 빈 단계다(조건 1). B-05-01 병합 때 `ci.yml`의 주석 두 줄로 바꾼다.
- 외부 서비스 테스트(결정 #12): `django` 작업이 `tests/infra/`·`tests/ads/`를 `PALDO_SERVICE_TESTS=1`로 경로별 실행한다(폴더가 없으면 통과). `core`에서 두 폴더를 빼는 `load_tests`는 폴더를 처음 만드는 태스크(C-06-01, D-05-03)가 그 `__init__.py`에 둔다. 광고 테스트의 Django 설정(`ads/project`, 결정 #1)은 D-05-03이 이 단계에 붙인다.
- `main` 보호 규칙의 필수 체크 등록과 "일부러 어긴 PR 3개"는 원격 저장소(P-11)가 생긴 뒤 한다. 그 전에는 같은 세 사례를 로컬 git 저장소 테스트로 재현해 둔다(`tests/tools/test_check_evidence.py` `ViolationScenario`, `tests/tools/test_check_docs.py` `ViolationScenarios`).
- 문서 산출 태스크(규칙 3)는 `산출물:` 줄의 파일 존재를 보고, 태스크에 `- 필수 제목:` 줄이 있을 때만 그 제목이 산출물의 마크다운 제목 줄(`#`~`######`, 코드 블록 밖, 글자 그대로 일치)에 있는지 본다. 본문 문장 속 단어는 제목으로 치지 않는다. `.pptx` 같은 비텍스트 산출물에는 필수 제목을 둘 수 없다.
- 통합 태스크(INT)에는 `check_docs` 규칙 1~4를 적용하지 않는다(README 10절). `--task INT-xx`는 항상 통과하고, `--all`은 INT를 대상에서 뺀다.
- `Task: P-xx`는 README 7절 "확정된 결정" 목록에 그 ID가 **나오는지**만 본다. 그 줄이 실제로 확정 내용인지는 보지 않는다.
- docstring 규칙(5)은 바뀐 줄과 겹치는 공개 정의만 본다. 클래스 안 메서드 본문만 바뀌어도 클래스 자체의 docstring을 요구한다.
- 비밀 키 규칙은 따옴표 값과 줄 전체 `KEY=값`/`KEY: 값`만 본다. `.py` 파일의 따옴표 없는 값은 변수·호출·타입 표기로 보고 검사하지 않는다. f-string은 `{`(보간)가 있을 때만 허용하고, 보간 없는 상수 f-string은 일반 문자열처럼 검사한다. 사설 IP는 점 네 개 IPv4만 보고, IPv6·호스트 이름·공인 IP는 보지 않는다.
- 손으로 닫힌 이슈는 이 도구가 찾지 않는다(GitHub를 조회하지 않는다). INT-11 #11에서 `gh issue list`와 `--all` 결과를 대조한다(11절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-02 | 11/5 | 처음 작성: `ci.yml` 4체크(결정 #12 경로별 단계, 이미지 태그 tech_stack 기준), `check_evidence`(커밋 메시지 닫기 키워드 금지 포함)·`check_docs`(필수 제목 줄, INT 제외)·`check_secrets`·`evidence_result`, 예외 목록, 자체 테스트(사전 작업, 실제 작업일 11/4) |
