# ci — 로컬 검사(precheck)와 증빙·문서화·비밀정보 검사

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

로컬 검사 한 명령 `python -m tools.precheck`로 구조·비밀정보·단위 테스트·문서화·증빙을 돌려, 태스크 완료 여부를 사람이 결과물을 열지 않고 판정하게 한다(README 3절 ②③⑥⑦). GitHub Actions CI는 11/5 폐지했다(조직 공용 Actions 분량을 쓰지 않는다). `git push` 때 `.githooks/pre-push`가 같은 검사를 돌리고, PR 본문에는 마지막 줄(`precheck: 통과 …`)을 붙인다.

| 파일 | 계층 | 하는 일 |
|---|---|---|
| `.githooks/pre-push` | 표현(진입점) | `git push` 때 푸시하는 커밋마다 `precheck`를 부르고 실패하면 푸시를 멈춘다(태그·브랜치 삭제 푸시 제외). 먼저 `precheck --check-pass <SHA>`로 통과 기록을 보고, 같은 비교 기준으로 이미 통과한 커밋이면 생략. 그 커밋이 HEAD가 아니거나 작업 트리에 변경이 있으면 임시 worktree에서 검사. precheck를 부를 때만 git이 훅에 넘긴 저장소 지정 변수(`GIT_DIR` 등, `git rev-parse --local-env-vars`)를 지운다 |
| `tools/precheck.py` | 도메인(`summarize`·`may_record`·`skip_decision` 등) + 인프라(`SubprocessRunner`·`PassStore`) + 응용(`run_precheck`·`record_pass`·`check_pass`·`quick_plan`·`slow_plan`) + 표현(`main`) | 아래 검사 도구를 순서대로 부르고 한 줄 요약. 전체 검사에서도 무거운 테스트는 바뀐 파일과 이어질 때만 돌리고(`--all-slow`면 모두) 뺀 개수를 요약 줄에 적는다. 깨끗한 트리에서 전체 통과하면 통과 기록을 남기고, `--check-pass`로 훅에 생략 여부를 알려 준다. `--quick`은 바뀐 파일과 이어진 테스트 모듈만 돌리고 기록을 남기지 않는다. PR 본문은 환경 변수로만 `check_evidence`에 넘긴다. 하위 명령 환경에서 저장소 지정 git 변수를 뺀다(`clean_env`) |
| `tools/precheck_scope.py` | 도메인 | 빠른 검사 대상(폴더별 테스트 모듈) 고르기: `.py`는 저장소 import 관계(`-m` 모듈 이름·`.py` 경로 문자열 포함)를 거슬러 쓰는 테스트와 상위 폴더 이름을 적은 폴더 훑기 테스트, `.md`·`docs/` 아래 파일은 문서를 읽는 테스트, `web/` 화면 파일은 화면 파일을 읽는 테스트, `server/tests/`(precheck 밖 Django·JS 테스트)는 그것을 쓰거나 적은 테스트만(없으면 테스트 없음). precheck 테스트가 쓰지 않는 `server/` 모듈은 Django 쪽으로 보고 전체로 넘기지 않는다. `load_tests`가 있는 폴더는 폴더 전체. 공용·그 밖의 비 `.py`·쓰는 테스트 없는 그 밖의 모듈은 전체. 무거운 테스트 목록 `SLOW_TESTS`와 전체 검사에서 뺄 것 고르기(`slow_skips`) |
| `tools/run_tests.py` | 도메인(`drop_tests`) + 표현(`main`) | 테스트 폴더 하나를 `unittest discover`와 같이 돌리되 지정한 테스트 id를 뺀다. 실제로 뺀 개수를 `precheck-skip: N`으로 내고 종료 코드는 `unittest`와 같다(0·1·5) |
| `tools/check_evidence.py` | 도메인 규칙 + 응용 + 표현(`main`) | 증빙 형식(`--format`), 완료 검사(`--complete`, 4절 규칙 1~6), PR 본문 문법(11절) |
| `tools/check_docs.py` | 도메인 규칙 + 응용 + 표현(`main`) | 문서화 검사(10절 규칙 1~6) |
| `tools/check_secrets.py` | 도메인 규칙 + 응용 + 표현(`main`) | 비밀정보 검사(E-05-02 조건 5), 예외 `tools/secrets_allowlist.txt` |
| `tools/check_structure.py` | 도메인 규칙 + 응용 + 표현(`main`) | 디렉터리 트리 검사(`docs/contracts/structure.md` 6절 허용 폴더). `--all`은 `precheck`, `--diff`는 임시 운영 로컬 CI |
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
- 표준 라이브러리만 쓴다(`docs/contracts/tech_stack.md`: 도구 코드는 외부 패키지 없음). 설치 단계가 없다.
- CI를 없앤 이유(11/5 PM 결정): 조직 저장소 68개가 무료 플랜 Actions 분량(월 2,000분)을 함께 쓰는데, 잡마다 1분 단위로 올려 세서 PR 하나에 약 30분이 들었다(11/5 하루 약 15회·140분). 검사 자체는 수십 초라 각자 노트북에서 돌려도 부담이 없다.
- 저장소 지정 git 변수를 지우는 이유: 훅이 받은 `GIT_DIR`·`GIT_INDEX_FILE`이 precheck의 테스트까지 이어지면, 테스트가 임시 저장소 대신 실제 저장소에 `git init`·commit·config를 한다(11/5 사고, [트러블 E](../troubles/E.md#e-05-02)).
- 통과 기록으로 훅 검사를 생략하는 이유(11/7 PM 결정): 작업 세션이 PR 본문 검사로 같은 커밋을 이미 통과시켰는데 푸시 때 같은 검사가 다시 돌아 1~2분씩 들었다. 기록은 커밋 SHA와 비교 기준이 모두 같을 때만 쓰고, 깨끗한 작업 트리·전체 통과일 때만 남긴다. `--pr-body` 실행은 본문 종류(P-xx)에 따라 push 때의 `check_evidence --changed`를 건너뛸 수 있어, 기록 전에 그 검사를 한 번 더 돌린다.
- 빠른 검사(`--quick`)를 둔 이유(11/9 PM 결정): 작업 중 확인에 전체 테스트(약 940개, 4~5분)를 매번 돌리면 기다리는 시간이 길다. 작업 중에는 바뀐 계층과 이어진 테스트만 돌리고, 다른 변경과 합친 결과를 잡는 전체 검사는 PR 직전과 push 때 한 번 돈다. 그래서 빠른 검사는 통과 기록을 남기지 않고(훅이 생략하지 않게), 요약 줄을 `precheck(빠른):`로 바꿔 PR 본문에 붙이면 `check_evidence`가 거부한다. 고르는 규칙이 애매하면 전체로 돈다(덜 줄이는 쪽).
- 무거운 테스트를 전체 검사에서도 고르는 이유(11/9 PM 결정): 전체 검사는 폴더를 동시에 돌려 가장 느린 폴더가 전체 시간을 정한다. 11/9 측정에서 `tests/engine`(약 290초)·`tests/tools`(약 260초)가 그 폴더였고, 그 시간의 절반 이상이 10초 넘는 테스트 9개(광고 트래픽 HTTP 재현 127초, 프리셋 저장·복원 왕복 90초, 판 실행 검사들)였다. 이 9개는 바뀐 파일이 import로 이어질 때만 돌리고, 공용 파일을 바꿨거나 바뀐 파일이 없으면(PM이 `dev`를 받아 그대로 돌리는 매일 검사) 모두 돈다. 목록은 `tools/precheck_scope.py`의 `SLOW_TESTS` 한 곳이라 테스트 파일은 고치지 않는다.
- 비교 기준을 못 구하면 문서·증빙 단계를 실패로 보는 이유: 건너뛰면 검사 없이 `precheck: 통과` 줄이 나와 PR 본문의 결과 줄을 믿을 수 없게 된다.

## 사용법

```
git config core.hooksPath .githooks                          # 처음 한 번: push 때 precheck 자동 실행
python -m tools.precheck                                     # 구조·비밀·테스트(tests/assets 제외)·문서(--diff)·증빙(--changed)
python -m tools.precheck --pr-body pr.md                     # PR 본문까지(Closes면 --complete). 마지막 줄을 PR 본문에
python -m tools.precheck --with-assets                       # 에셋 관련 파일을 바꿨을 때 tests/assets 포함
python -m tools.precheck --base <커밋>                        # 비교 기준 직접 지정(기본 origin/dev와의 merge-base)
python -m tools.precheck --check-pass <SHA>                  # 훅용: 통과 기록이 지금 비교 기준과 맞으면 0(생략), 아니면 1
python -m tools.precheck --quick                             # 작업 중 빠른 검사(바뀐 파일과 이어진 테스트 모듈만, 기록 없음, PR에 못 붙임)
python -m tools.precheck --all-slow                          # 무거운 테스트(SLOW_TESTS)도 모두(기본: 바뀐 파일과 이어질 때만)
python -m unittest discover -s tests -t .                    # 단위 테스트 전체(검사 도구 자체 테스트 포함)
python -m tools.check_secrets                                # 비밀정보
python -m tools.check_structure --all                        # 추적 파일의 모든 폴더가 structure.md 6절 안
python -m tools.check_structure --diff <base>                # base 이후 새로 생긴 폴더만(로컬 CI core)
python -m tools.check_evidence E-05-02 --format              # 양식만(상태: 실패 문서도 통과)
python -m tools.check_evidence E-05-02 --complete            # 4절 검사 규칙 1~6
python -m tools.check_evidence --all --until 11-05           # 예정일 11/5까지 태스크 전부 --complete
python -m tools.check_evidence --changed <base>              # base 이후 바뀐 증빙에 --format(push)
PR_BODY="$(cat body.md)" python -m tools.check_evidence --pr-body-env PR_BODY --changed <base>   # PR 본문 검사(precheck --pr-body가 부름)
python -m tools.check_docs --task E-05-02                    # 규칙 1~3
python -m tools.check_docs --all --until 11-05               # 규칙 4
python -m tools.check_docs --diff origin/dev                 # 규칙 5~6(커밋 전 변경·새 파일 포함). 기준은 통합 브랜치 dev
```

저장소 배정 전 임시 운영(README 10-1절, GitHub 없이 검사를 로컬에서 대체). **11/5 저장소 배정으로 끝났고 기록으로만 남긴다.** `interim`은 로컬 `main`을 통합 브랜치로 가정한 임시 운영 전용 도구라 `dev`로 바꾸지 않았다:

```
python -m tools.interim ci --base <SHA>                      # 커밋된 HEAD를 임시 worktree로 꺼내 core·docs·evidence·service
python -m tools.interim submit --task <ID> --base <main SHA> [--complete] [--kind fix]
python -m tools.interim intake <번들>                         # PM: 검증 후 refs/inbox/<ID>/<NN>
python -m tools.interim merge --task <ID> --number <NN>       # PM: --no-ff 병합 + 병합 결과 재검사
python -m tools.interim publish --out <main 폴더> --until 11-05 --inbox <inbox 폴더>   # PM: 일 마감 전체 검사 + main 번들·진행표
python -m tools.interim status --until 11-05 --inbox <폴더>   # 진행표(완료·병합됨·제출됨·실패·미착수)
python -m tools.interim check-ledger                         # 원장(⑧ 대체) 검사
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
| Python | `3.14` | 각자 노트북의 Python(`precheck`는 자기를 실행한 인터프리터로 하위 검사를 돈다) | 프로젝트 전체 Python 3.14 단일(11/5 PM 결정, `tech_stack.md`) |
| `PALDO_PRECHECK_NO_CACHE` | 없음 | `1`이면 훅이 통과 기록을 보지 않고 항상 전체 검사 | 기록 판정이 의심될 때 끈다 |
| 통과 기록 보관 | 14일 | `<git 공용 폴더>/precheck-pass/`의 오래된 기록은 새 기록을 남길 때 지움(`RECORD_MAX_AGE`) | 짧게 하면 오래 묵은 브랜치 푸시 때 전체 검사 |
| `PRECHECK_PR_BODY` | - | `precheck --pr-body`가 `check_evidence --pr-body-env`에 넘기는 환경 변수 | 이름을 바꾸면 `tools/precheck.py`의 `PR_BODY_ENV`도 같이 |
| `core.hooksPath` | `.githooks` | git 훅 폴더(각 clone에서 한 번 설정) | 설정하지 않으면 push 때 자동 검사가 돌지 않는다 |
| `PALDO_LOG_DIR` | `out/logs` | 구조화 로그 파일 폴더(`ci.jsonl`) | 로그만 옮겨진다. 검사 결과와 무관 |
| `tools/secrets_allowlist.txt` | 3항목(CI 폐지로 불필요한 예외를 제거하고 D-05-02의 Hugging Face 인증 토큰 자동 전송 차단 설정 예외 포함) | 한 줄에 경로·키 이름·사유(`\|`로 구분). 그 파일의 그 키만 허용 | 항목 추가는 PR 승인(⑧)으로 통제. 걸리지 않는 항목은 죽은 예외로 실패 |
| 허용 폴더 목록 | `docs/contracts/structure.md` 6절 펜스 블록 | `dir/` = 그 폴더만, `dir/**` = 아래 전부. 루트 파일은 늘 허용 | 폴더 추가는 PM 승인 후 이 목록과 1절 트리를 같이 고친다 |
| `tests/fixtures/secrets/` | - | 탐지 테스트용 가짜 값. 비밀정보 검사에서 이 폴더만 제외 | 다른 폴더에 가짜 값을 두면 검사가 실패한다 |
| base 커밋 | `origin/dev`와 HEAD의 merge-base(`--base`로 지정) | 변경분 비교 기준 | 못 구하면 문서·증빙 단계 실패. `git fetch origin` 뒤 다시 |

## 한계

- 자동으로 막는 장치가 없다. 훅은 `core.hooksPath`를 설정한 clone에서만 돌고 `git push --no-verify`로 건너뛸 수 있으며, PR 본문의 결과 줄은 손으로 쓸 수 있다. 실수로 빼먹는 것만 막는다. PM은 `Closes` PR을 병합하기 전에 PR 브랜치에서 `precheck --pr-body`를 다시 돌린다(README 11절).
- 통과 기록은 각자 노트북의 git 폴더에만 있다. 다른 사람·다른 clone이 같은 커밋을 푸시하면 다시 검사한다. 기록은 손으로 만들 수 있으므로 실수 방지용이다(위와 같은 한계).
- 빠른 검사의 대상 고르기는 정적 분석이다. 문자열을 조립하는 동적 import, 테스트가 경로를 조립해 읽는 비 `.py` 파일은 보지 못한다. 그래서 비 `.py` 변경은 문서(`.md`·`docs/`)와 `web/` 화면 파일만 빼고 전체로 돌고, PR 직전 전체 검사를 빼지 않는다. `server/tests`(Django)는 전체 검사와 마찬가지로 대상이 아니다. precheck 테스트가 쓰지 않는 `server/` 모듈(Django 화면·관리 명령)을 전체로 넘기지 않는 것도 같은 이유다(전체 검사도 그 모듈을 돌리지 않는다). 테스트 폴더의 탐색 전용 장치(`load_tests` 외 `setUpModule` 등)는 모듈을 직접 지정해 돌려도 그대로 적용된다.
- 병합 뒤 `dev`에서 자동 검사가 돌지 않는다. 두 PR이 각각 통과해도 합친 결과가 깨질 수 있어, PM이 매일 `dev`에서 `precheck`를 한 번 돌린다. 이때는 바뀐 파일이 없어 무거운 테스트도 모두 돈다(작업 트리에 추적 안 하는 파일이 있으면 `--all-slow`).
- 무거운 테스트 고르기도 빠른 검사와 같은 정적 분석이라, 동적으로 불러오는 경로로만 이어진 변경에서는 PR 단계에서 빠질 수 있다. 그런 결함은 위 매일 `dev` 검사에서 잡는다. 새로 10초 넘는 테스트가 생기면 `SLOW_TESTS`에 더한다(목록의 id는 `tests/tools/test_precheck_scope.py`가 실제 존재를 검사).
- `tests/assets`(단일 테스트가 10초를 넘는다)는 기본에서 빠진다. 에셋 관련 파일(`tools/assets/`, `tests/assets/`, `docs/assets/`, `content/images/`)을 바꾼 사람만 `--with-assets`로 돌린다.
- Django(`server/manage.py test`)와 외부 서비스 테스트(결정 #12, `tests/infra/`·`tests/ads/`, `PALDO_SERVICE_TESTS=1`)는 `precheck`에 넣지 않았다. 두 폴더를 기본 discover에서 빼는 `load_tests`는 폴더를 처음 만드는 태스크(C-06-01, D-05-03)가 그 `__init__.py`에 두고, 실제 실행은 각자 Compose와 통합(INT)에서 한다.
- 임시 운영 로컬 CI(`tools/interim.py`)는 Actions와 같은 검사 함수를 부르지만 실행 환경은 각자 노트북이다(Python 3.14 버전은 `.ci.txt`에 기록). 서비스 테스트는 `PALDO_SERVICE_TESTS=1`과 Compose 기동이 있어야 돌고, 대상 영역을 바꿨는데 안 돌렸으면 실패로 본다. 원장 검사는 병합 SHA·부모·흐름만 보고 검토 내용 자체는 보지 않는다.
- 브랜치 모델(11/5 PM 결정, tasks README 3절): 통합 `dev`(기본 브랜치, 태스크 PR 대상), 배포 `main`(승격 PR만). 승격 PR(`dev` → `main`)은 Task 줄이 없으므로 `dev`에서 `precheck --base origin/main`을 돌려 `main` 이후 바뀐 증빙의 `--format`만 본다(기본 기준 `origin/dev`면 바뀐 것이 없다). 이슈 본문의 원본 링크는 `dev`를 가리킨다(`tools/tasks_to_issues.py` `INTEGRATION_BRANCH`).
- 보호 규칙은 비공개·무료 플랜이라 걸 수 없다(P-11). "일부러 어긴 사례 3개"는 로컬 git 저장소 테스트로 재현해 둔다(`tests/tools/test_check_evidence.py` `ViolationScenario`, `tests/tools/test_check_docs.py` `ViolationScenarios`).
- 문서 산출 태스크(규칙 3)는 `산출물:` 줄의 파일 존재를 보고, 태스크에 `- 필수 제목:` 줄이 있을 때만 그 제목이 산출물의 마크다운 제목 줄(`#`~`######`, 코드 블록 밖, 글자 그대로 일치)에 있는지 본다. 본문 문장 속 단어는 제목으로 치지 않는다. `.pptx` 같은 비텍스트 산출물에는 필수 제목을 둘 수 없다.
- 통합 태스크(INT)에는 `check_docs` 규칙 1~4를 적용하지 않는다(README 10절). `--task INT-xx`는 항상 통과하고, `--all`은 INT를 대상에서 뺀다.
- `Task: P-xx`는 README 7절 "확정된 결정" 목록에 그 ID가 **나오는지**만 본다. 그 줄이 실제로 확정 내용인지는 보지 않는다.
- docstring 규칙(5)은 바뀐 줄과 겹치는 공개 정의만 본다. 클래스 안 메서드 본문만 바뀌어도 클래스 자체의 docstring을 요구한다.
- 비밀 키 규칙은 따옴표 값과 줄 전체 `KEY=값`/`KEY: 값`만 본다. `.py` 파일의 따옴표 없는 값은 변수·호출·타입 표기로 보고 검사하지 않는다. f-string은 `{`(보간)가 있을 때만 허용하고, 보간 없는 상수 f-string은 일반 문자열처럼 검사한다. 사설 IP는 점 네 개 IPv4만 보고, IPv6·호스트 이름·공인 IP는 보지 않는다.
- `check_structure`는 git이 보는 파일의 폴더만 본다. 빈 폴더와 `.gitignore`로 빠진 경로(`out/` 등)는 검사하지 않는다. `--all`은 추적 파일만, `--diff`는 커밋 전·추적 안 된 새 파일도 본다. 폴더 이름만 보고 파일이 1절 트리의 어느 줄에 맞는지는 보지 않는다.
- 손으로 닫힌 이슈는 이 도구가 찾지 않는다(GitHub를 조회하지 않는다). INT-11 #11에서 `gh issue list`와 `--all` 결과를 대조한다(11절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-05-02 | 11/5 | 처음 작성: `ci.yml` 4체크(결정 #12 경로별 단계, 이미지 태그 tech_stack 기준), `check_evidence`(커밋 메시지 닫기 키워드 금지 포함)·`check_docs`(필수 제목 줄, INT 제외)·`check_secrets`·`evidence_result`, 예외 목록, 자체 테스트(사전 작업, 실제 작업일 11/4) |
| E-05-02 | 11/5 | 저장소 배정 전 임시 운영 도구 `interim`(로컬 CI·번들 제출·PM 병합·원장·진행표, README 10-1절) 추가 |
| E-05-02 | 11/5 | 디렉터리 트리 검사 `check_structure` 추가(`core`에 `--all`, 로컬 CI `core`에 `--diff`), 11/5 PM 결정 |
| E-05-01 | 11/5 | 저장소 배정에 맞춘 브랜치 모델(통합 `dev`·배포 `main`): 승격 PR의 `evidence` 형식 검사 분기, 이슈 원본 링크 `dev` |
| E-05-02 | 11/5 | GitHub Actions CI(`ci.yml`) 폐지, 로컬 검사 `tools/precheck.py`와 `.githooks/pre-push`로 대체(조직 공용 Actions 분량, 11/5 PM 결정). 실제 소요 시간 검사 폐지 |
| E-05-02 | 11/5 | 훅과 `precheck`가 git 저장소 지정 환경 변수를 지움(`clean_env`, 훅은 precheck 호출 때만). 물려받은 `GIT_DIR`이 다른 저장소를 바꾸지 않는 회귀 테스트 |
| P-27 | 11/6 | `check_docs` 규칙 6을 라우팅 2단 구조에 맞춤: 새 최상위 폴더는 루트 `README.md`, 두 번째 단계는 루트 README 또는 `docs/routing/<A~E>.md`. 삭제된 경로는 문서 변경으로 치지 않음 |
| P-10 | 11/7 | 통과 기록: 깨끗한 트리에서 전체 통과한 커밋을 `<git 공용 폴더>/precheck-pass/<SHA>`에 기록, 훅은 `--check-pass`로 같은 비교 기준이면 검사 생략(11/7 PM 결정, 끄기 `PALDO_PRECHECK_NO_CACHE=1`) |
| P-10 | 11/9 | 빠른 검사 `--quick`(작업 중, 바뀐 파일과 이어진 테스트 모듈만, `tools/precheck_scope.py`), 통과 기록 없음, 요약 줄 `precheck(빠른):`은 PR 본문 검사에서 거부. 전체 검사·훅·통과 기록 동작은 그대로(11/9 PM 결정) |
| P-10 | 11/9 | 무거운 테스트 9개(`SLOW_TESTS`)는 전체 검사에서도 바뀐 파일과 이어질 때만(`tools/run_tests.py`로 빼고 실행, 요약 줄에 생략 개수), 공용 파일·변경 없음·`--all-slow`면 모두(11/9 PM 결정) |
