# 팔도 (paldo)

무당이 되어 마을에 숨은 씐 자를 찾는 추리 게임이다.

- 기획서: [`docs/paldo_design_v1.md`](docs/paldo_design_v1.md)
- 팀 일정과 역할: [`docs/team_plan.md`](docs/team_plan.md)
- BRD(업무 요구): [`docs/brd.md`](docs/brd.md) · PRD(제품 요구·합격 기준, 요구사항 기준 문서): [`docs/prd.md`](docs/prd.md)
- 계약 문서: [`docs/contracts/`](docs/contracts/)(색인·DB 스키마 위치: [`README.md`](docs/contracts/README.md)) · 디렉터리 트리(새 파일 위치): [`structure.md`](docs/contracts/structure.md) · 영역 간 호출 지도: [`integration.md`](docs/contracts/integration.md)
- **일자별·담당자별 태스크와 완료 조건**: [`docs/tasks/`](docs/tasks/README.md) (공통 완료 정의, 증빙 양식, 로그·계측 규칙, 결정 대기, 통합 판정 표)
- 태스크 증빙: `docs/evidence/<태스크ID>.md`, 발표 기록: `docs/presentation/`, 리포트: `docs/reports/`
- 모듈 문서(무엇을·왜·사용법·설정값·한계): [`docs/modules/`](docs/modules/README.md), 트러블 기록: `docs/troubles/<흐름>.md`(발표 트랙은 `S.md`). 기준은 `docs/tasks/README.md` 10절
- 진행 관리: GitHub Issues(태스크 마크다운에서 생성, `docs/tasks/README.md` 11절). 저장소 배정 전에는 NAS 번들 제출·PM 병합·원장(`docs/tasks/README.md` 10-1절)

## 계층과 의존 방향

```
server/api (표현) → server/app (응용) → engine (도메인)
server/infra (인프라) → engine (포트 구현)
```

- `engine/`은 표준 라이브러리만 쓴다. Django, Mongo, Kafka, ML, LLM 라이브러리를 import하지 않는다.
- `server/app`은 `engine`과 광고 요청·응답 DTO(`ads.domain.model`)만 import한다. Django는 import하지 않는다.
- `contracts/`(데이터셋 계약)는 `engine/`이 import하지 않는다.
- 광고: `ads/web → ads/app → ads/domain`, `ads/infra → ads/domain`. `ads/domain`은 표준 라이브러리만 쓴다. `engine/`과 `ads/`는 서로 import하지 않는다. 게임 서버는 `server/app/ads_port.py`(AdProvider)로만 광고를 부른다.

## 영역 안내

영역 안의 폴더와 작업 위치는 흐름별 문서에 있다. 이 README에는 영역 진입점과 공용 항목만 둔다(P-27). 새 최상위 폴더나 공용 항목이 아니면 이 파일 대신 해당 영역 문서를 고친다.

| 흐름 | 영역 | 주요 폴더 | 안내 문서 |
|---|---|---|---|
| A | 엔진 | `engine/`(`engine/sim/`은 D) | [`docs/routing/A.md`](docs/routing/A.md) |
| B | 웹 | `server/api/`, `server/app/`, `web/` | [`docs/routing/B.md`](docs/routing/B.md) |
| C | 인프라·파이프라인·운영 | `server/infra/`, `pipeline/`, `ops/` | [`docs/routing/C.md`](docs/routing/C.md) |
| D | ML·광고 | `engine/sim/`, `intent/`, `ml/`, `ads/` | [`docs/routing/D.md`](docs/routing/D.md) |
| E | 콘텐츠·LLM·에셋 규격·저장소 운영 | `content/`, `llm/`, `.github/`, `.githooks/`, `docs/ops/`, `docs/assets/` | [`docs/routing/E.md`](docs/routing/E.md) |
| S | 발표 트랙 | `docs/presentation/` | [`docs/tasks/P_presentation.md`](docs/tasks/P_presentation.md) |

## 공용 폴더 안내

여러 흐름이 함께 쓰는 폴더다. 영역 폴더는 위 "영역 안내"의 문서를 본다.

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `engine/testing/` | 테스트 지원 | 공용 | 모든 포트의 가짜 구현 |
| `requirements/` | 의존성 | 공용 | 영역별 Python 패키지(`web`·`ml`·`llm`·`pipeline`, Python 3.14) |
| `contracts/` | 데이터 계약 | 공용 | 학습 행, 의도 예시, 시나리오 스키마 |
| `fixtures/` | 생성물 | 공용 | 계약 예시. `tools/build_fixtures.py`가 만든다 |
| `tools/` | 도구 | 공용 | 픽스처 생성, 합성 데이터 생성, 태스크 문서 파서(`tasks_md.py`)·이슈 동기화(`tasks_to_issues.py`), 저장소 배정 전 임시 운영(`interim.py` 표현·응용, `interim_rules.py` 도메인) |
| `tools/perf/` | 도구(측정) | 각 흐름 | 측정 스크립트. 결과 JSON은 `tools/evidence_result.py`로 `docs/evidence/perf/<ID>/`에 쓴다(엔진 `engine_bench.py`) |
| `tests/contract/` | 테스트 | 공용 | 계약 테스트(unittest) |
| `tests/tools/` | 테스트 | 공용 | 도구 테스트(태스크 파서·이슈 동기화·임시 운영 흐름은 임시 git 저장소와 임시 폴더로) |
| `docs/evidence/` | 문서(증빙) | 각 흐름 | 태스크 증빙 `<ID>.md`, 측정 결과 JSON `perf/<ID>/<이름>.json`(`tools/evidence_result.py`로만 쓴다) |
| `docs/modules/`, `docs/troubles/` | 문서 | 각 흐름 | 모듈 문서, 흐름별 트러블 기록(양식 `_template.md`) |

"예정"인 폴더는 담당 흐름이 처음 코드를 넣을 때 만든다.

## 공용 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 이벤트나 학습 데이터 형식 변경 | `engine/domain/events.py`, `contracts/datasets.py`, `docs/contracts/events.md` |
| 개인정보 처리 | `docs/contracts/privacy.md` |
| 서버와 버전 | `docs/contracts/tech_stack.md`, `ops/runbook/vm_roles.md` |
| 다른 흐름 없이 개발 시작 | `docs/contracts/parallel_dev.md` |
| 새 파일·폴더를 어디에 만들지 | `docs/contracts/structure.md`(1절 트리, 6절 허용 폴더 목록) |
| 새 폴더 만들기(허용 목록 밖) | PM 승인 → `docs/contracts/structure.md` 6절·1절 먼저 고침 → 검사 `python -m tools.check_structure --diff <base>`(로컬 CI `core`, GitHub CI `core`는 `--all`) |
| 다른 영역을 어떻게 부르는지(포트·HTTP·토픽·주소 변수·가짜·통합 시점) | `docs/contracts/integration.md` |
| 오늘 내가 할 일과 완료 조건 | `docs/tasks/<흐름>.md`(A_engine, B_web, C_infra, D_ml_ads, E_pm), 발표 작업은 발표 트랙 `docs/tasks/P_presentation.md`(S-xx, 담당 P-24) |
| 태스크 완료 증빙 쓰기 | `docs/tasks/README.md` 4절 양식 → `docs/evidence/` |
| 태스크 문서화(트러블·docstring은 태스크마다, 모듈 문서는 흐름별 하루 1회 일괄 갱신) | `docs/tasks/README.md` 10절 → `docs/modules/`, `docs/troubles/` |
| 이슈 만들기·동기화, 오늘 열린 태스크 보기 | `python -m tools.tasks_to_issues --dry-run`, `docs/tasks/README.md` 11절 |
| dev 받기·충돌 풀기, 여럿이 고치는 파일(README 표 등)에 행 넣기 | `docs/tasks/README.md` 3절 "브랜치 모델" |
| 로컬 검사(precheck)·증빙·문서화·비밀정보 검사 규칙 바꾸기 | 표현 `.githooks/pre-push`, 응용 `tools/precheck.py`, 규칙 `tools/check_evidence.py`·`check_docs.py`·`check_secrets.py`·`check_structure.py`, 예외 `tools/secrets_allowlist.txt`, 설명 `docs/modules/ci.md` |
| 저장소 배정 전 제출·병합(NAS 번들, 로컬 CI, 원장, 진행표) | 표현·응용 `tools/interim.py`, 규칙(도메인) `tools/interim_rules.py`, 기록 `docs/ops/interim_ledger.md`, 절차 `docs/tasks/README.md` 10-1절 |
| 태스크 조건 바꾸기 | `docs/tasks/<흐름>.md`를 고치는 PR(이슈 본문은 직접 고치지 않음) |
| 로그·지표 이름 정하기 | `docs/tasks/README.md` 5절 |

## 명령

```
git config core.hooksPath .githooks                       # 처음 한 번: push 때 precheck 자동 실행
python -m tools.precheck                                  # 로컬 검사(구조·비밀·테스트(assets 제외)·문서·증빙). PR 본문에 마지막 줄
git fetch origin && git merge origin/dev                  # PR 직전에 dev 받기(시작 때는 fetch 후 origin/dev에서 브랜치). 리베이스·강제 푸시 금지
python -m tools.precheck --pr-body pr.md                  # PR 본문(Task·Closes/Refs)까지. 에셋을 바꿨으면 --with-assets
python -m unittest discover -s tests -t .                 # 계약 테스트
python -m tools.build_fixtures                            # 픽스처 재생성(계약 변경 후)
python -m tools.gen_synthetic --games 50 --seed 7 --ads 20000 --out out/synthetic
python -m tools.tasks_to_issues --dry-run                 # 태스크 → 이슈 계획(실제 생성은 --apply --repo)
python -m tools.tasks_to_issues --hours                   # 흐름별 일별 시간(문서화·일괄 갱신 포함, 흐름별 한도, 발표 트랙은 별도 줄)
# 브랜치: 통합 dev(모든 PR 대상, 기본 브랜치) / 배포 main(사용자 테스트 전·시연 전 승격만). docs/tasks/README.md 3절
```

텍스트 파일은 UTF-8(BOM 없음), 줄바꿈 LF다(`.gitattributes`가 고정, Windows 스크립트 `.ps1`·`.bat`·`.cmd`만 CRLF).

접속 정보(IP, 계정, 비밀번호)는 이 저장소에 넣지 않는다.
