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

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `engine/domain/` | 도메인 | A | 규칙, 상태, 이벤트, 콘텐츠 타입, 배정 검증기(`model.py`, `content.py`, `events.py`, `serde.py`), 엔진 구현(`engine.py`), 밸런스 상수(`params.py`) |
| `engine/ports.py` | 도메인 경계 | A(정의) | 엔진이 바깥에 기대는 인터페이스 |
| `engine/testing/` | 테스트 지원 | 공용 | 모든 포트의 가짜 구현 |
| `engine/sim/` | 도메인 사용 | D | 봇 정책, 시뮬레이터(예정). 결정 층 최적화는 A가 `engine/domain`에서 맡는다 |
| `server/app/` | 응용 | B | 유스케이스와 DTO(`dto.py`) |
| `server/api/`, `web/` | 표현 | B | Django 뷰, 템플릿, htmx 조각, 뷰 모델(`viewmodels.py`), 웹소켓 Consumer(`consumers.py`, 예정) |
| `server/infra/` | 인프라 | C | Mongo 저장소, 아웃박스 발행기, 모델·LLM 어댑터(예정) |
| `pipeline/`, `ops/` | 인프라 | C | Kafka → Spark → Delta, 배포·관측, HTTPS 프록시 `ops/proxy/`(예정) |
| `ops/compose/` | 인프라 | C | 로컬 개발용 Compose(`dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`), 패키지 확인(`pycheck.py`), 팀원 절차·점검표(`README.md`) |
| `ops/` VM 점검 | 인프라(운영) | C | VM 점검표 `inventory_check.md`, 호스트 점검 `inventory_host.ps1`(노트북 Windows), VM 안 점검 `tools/perf/vm_probe.sh` |
| `ops/runbook/` | 인프라(운영) | C | 실행 시간표 `schedule.md`, VM 역할·3차↔4차 이름·디스크 예산 `vm_roles.md` |
| `requirements/` | 의존성 | 공용 | 영역별 Python 패키지(`web`·`ml`·`llm`·`pipeline`, Python 3.14) |
| `intent/`, `ml/` | 인프라(모델) | D | 의도 해석기, 씐 자 확률 모델(예정) |
| `content/` | 데이터 | E | 인물, 비밀, 단서, 템플릿(예정) |
| `content/images/`, `docs/assets/`, `tools/assets/` | 데이터(에셋) | E(규격·스타일 가이드·임시 파일·검사), D(실제 파일·생성 기록·`cutout.py`, A 흐름 `[에셋]` 태스크, 11/5 결정) | 이미지 실제·임시 파일, `manifest.json`(콘텐츠 ID → 파일), 생성 기록, 스타일 가이드·규격, ID 1:1 검사(예정) |
| `ads/domain/` | 광고 도메인 | D | 경매, 2등 가격, 예산 예약·확정·해제, 페이싱, CTR, 유효 판정, 대사, 지면 규격(`SLOT_SIZES`)(표준 라이브러리만) |
| `ads/app/`, `ads/infra/`, `ads/web/` | 광고 응용, 인프라, 표현 | D | 요청·비콘·클릭 흐름, MySQL 원장·아웃박스·Kafka, Django 뷰·admin(예정) |
| `ads/testing/` | 테스트 지원 | D | `FakeAdProvider` |
| `server/app/ads_port.py` | 응용(게임 쪽) | B | `AdProvider` 포트와 보호 래퍼 `AdGateway` |
| `llm/` | 인프라 | E | LLM 서비스, LangGraph(예정) |
| `llm/bench/` | 인프라(측정) | E | LLM 벤치마크 `run_bench.py`(llama.cpp, 모델 인스턴스 1개 + FIFO 큐로 지연·토큰/초·메모리 측정). 결과 `docs/reports/llm_bench.md` |
| `contracts/` | 데이터 계약 | 공용 | 학습 행, 의도 예시, 시나리오 스키마 |
| `fixtures/` | 생성물 | 공용 | 계약 예시. `tools/build_fixtures.py`가 만든다 |
| `tools/` | 도구 | 공용 | 픽스처 생성, 합성 데이터 생성, 태스크 문서 파서(`tasks_md.py`)·이슈 동기화(`tasks_to_issues.py`), 저장소 배정 전 임시 운영(`interim.py` 표현·응용, `interim_rules.py` 도메인) |
| `tools/perf/` | 도구(측정) | 각 흐름 | 측정 스크립트. 결과 JSON은 `tools/evidence_result.py`로 `docs/evidence/perf/<ID>/`에 쓴다(엔진 `engine_bench.py`) |
| `tests/contract/` | 테스트 | 공용 | 계약 테스트(unittest) |
| `tests/tools/` | 테스트 | 공용 | 도구 테스트(태스크 파서·이슈 동기화·임시 운영 흐름은 임시 git 저장소와 임시 폴더로) |
| `tests/assets/` | 테스트 | E | 에셋 도구 테스트(규격표 해석, 임시 세트 생성·대조) |
| `tests/llm/` | 테스트 | E | LLM 순수 함수 테스트(llama_cpp 없이 돈다) |
| `.github/` | 저장소 운영 | E | 이슈 양식(`ISSUE_TEMPLATE/`), 라벨 정의(`labels.yml`), PR 양식. GitHub Actions CI는 11/5 폐지(로컬 `precheck`로 대체) |
| `.githooks/` | 저장소 운영(표현) | E | `pre-push`: `git push` 때 `python -m tools.precheck`를 돌려 실패하면 푸시를 멈춘다(설치 `git config core.hooksPath .githooks`). 검사 조립은 `tools/precheck.py`(응용), 규칙은 `tools/check_*.py`, 설명은 `docs/modules/ci.md` |
| `docs/evidence/` | 문서(증빙) | 각 흐름 | 태스크 증빙 `<ID>.md`, 측정 결과 JSON `perf/<ID>/<이름>.json`(`tools/evidence_result.py`로만 쓴다) |
| `tests/fixtures/secrets/` | 테스트 데이터 | E | `check_secrets` 탐지 테스트용 가짜 비밀값. 비밀정보 검사에서 이 폴더만 제외 |
| `docs/ops/` | 문서(운영 기록) | E | 팀원 안내서 `interim_guide.md`(받기·작업·제출·검토 절차), 저장소 배정 전 임시 운영 원장 `interim_ledger.md`(⑧ 대체·병합 기록·결함 표, `tools/interim.py`가 검사) |
| `docs/modules/`, `docs/troubles/` | 문서 | 각 흐름 | 모듈 문서, 흐름별 트러블 기록(양식 `_template.md`) |

"예정"인 폴더는 담당 흐름이 처음 코드를 넣을 때 만든다.

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 게임 규칙 변경 | 도메인 `engine/domain/model.py`, `docs/contracts/domain.md` |
| 판 생성·진행 규칙 구현, 목격 확률 등 밸런스 상수 | 도메인 `engine/domain/engine.py`, `engine/domain/params.py` |
| 엔진과 바깥의 인터페이스 변경 | `engine/ports.py`, `docs/contracts/ports.md` |
| 화면이나 API 추가 | 표현 `server/api/`, 응용 `server/app/dto.py`, `docs/contracts/api.md` |
| 저장소나 Kafka 교체 | 인프라 `server/infra/`, `docs/contracts/ports.md` 2절 |
| 이벤트나 학습 데이터 형식 변경 | `engine/domain/events.py`, `contracts/datasets.py`, `docs/contracts/events.md` |
| 모델 교체·승격·롤백 | `docs/contracts/ml_lifecycle.md` |
| 콘텐츠 추가 | `docs/contracts/content.md` |
| 개인정보 처리 | `docs/contracts/privacy.md` |
| 서버와 버전 | `docs/contracts/tech_stack.md`, 기획서 6.3 |
| LLM 모델·스레드·실행 방식 다시 재기 | 인프라 `llm/bench/run_bench.py`(`--help`), 결과·결정 `docs/reports/llm_bench.md` |
| 로컬 개발 환경 띄우기·Python 패키지 설치 | `ops/compose/README.md`, `requirements/` |
| VM 상태 점검(기동·자원·디스크·스냅샷) | `ops/inventory_check.md`(방법과 20대 표), `ops/inventory_host.ps1`, `tools/perf/vm_probe.sh` |
| 시간대별 VM 사용(사용자 테스트·학습·LLM), VM 역할·디스크 예산 | `ops/runbook/schedule.md`, `ops/runbook/vm_roles.md` |
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
| 이미지 추가·교체 | 같은 경로에 덮어쓰기만. 규격·파일명은 `docs/assets/spec.md`, 코드는 `manifest.json`·`image_path`로만 참조(기준 `docs/tasks/E_pm.md` "에셋 작업 기준", 실제 에셋 태스크는 `docs/tasks/A_engine.md` `[에셋]`, 담당 D) |
| 아침 실종 발표·피해 장소 공개, 알리바이 명제 | `engine/domain/model.py`(`DisappearanceRecord`, `alibi_propositions`), `docs/contracts/domain.md` 6절 |
| 경매·과금·광고 판정 규칙 변경 | 광고 도메인 `ads/domain/`, `docs/contracts/ads.md` |
| 광고 지면을 게임 화면에 붙이기 | `server/app/ads_port.py`, `docs/contracts/ports.md` 5절 |
| 광고 비콘·클릭 중계(웹소켓) | 표현 `server/api/consumers.py` → 응용 `server/app/ad_events.py` → 포트 `server/app/ads_port.py` → 인프라 `server/infra/ad_client.py`, `docs/contracts/api.md` 7절 |
| HTTPS·인증서 경고 안내 | `ops/proxy/`(C-07-04), `docs/contracts/integration.md` 5절 |

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
