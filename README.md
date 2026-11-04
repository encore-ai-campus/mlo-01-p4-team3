# 팔도 (paldo)

무당이 되어 마을에 숨은 씐 자를 찾는 추리 게임이다.

- 기획서: [`docs/paldo_design_v1.md`](docs/paldo_design_v1.md)
- 팀 일정과 역할: [`docs/team_plan.md`](docs/team_plan.md)
- BRD(업무 요구): [`docs/brd.md`](docs/brd.md) · PRD(제품 요구·합격 기준, 요구사항 기준 문서): [`docs/prd.md`](docs/prd.md)
- 계약 문서: [`docs/contracts/`](docs/contracts/)
- **일자별·담당자별 태스크와 완료 조건**: [`docs/tasks/`](docs/tasks/README.md) (공통 완료 정의, 증빙 양식, 로그·계측 규칙, 결정 대기, 통합 판정 표)
- 태스크 증빙: `docs/evidence/<태스크ID>.md`, 발표 기록: `docs/presentation/`, 리포트: `docs/reports/`
- 모듈 문서(무엇을·왜·사용법·설정값·한계): [`docs/modules/`](docs/modules/README.md), 트러블 기록: `docs/troubles/<흐름>.md`(발표 트랙은 `S.md`). 기준은 `docs/tasks/README.md` 10절
- 진행 관리: GitHub Issues(태스크 마크다운에서 생성, `docs/tasks/README.md` 11절)

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
| `engine/domain/` | 도메인 | A | 규칙, 상태, 이벤트, 콘텐츠 타입, 배정 검증기(`model.py`, `content.py`, `events.py`, `serde.py`) |
| `engine/ports.py` | 도메인 경계 | A(정의) | 엔진이 바깥에 기대는 인터페이스 |
| `engine/testing/` | 테스트 지원 | 공용 | 모든 포트의 가짜 구현 |
| `engine/sim/` | 도메인 사용 | D | 봇 정책, 시뮬레이터(예정). 결정 층 최적화는 A가 `engine/domain`에서 맡는다 |
| `server/app/` | 응용 | B | 유스케이스와 DTO(`dto.py`) |
| `server/api/`, `web/` | 표현 | B | Django 뷰, 템플릿, htmx 조각, 뷰 모델(`viewmodels.py`) |
| `server/infra/` | 인프라 | C | Mongo 저장소, 아웃박스 발행기, 모델·LLM 어댑터(예정) |
| `pipeline/`, `ops/` | 인프라 | C | Kafka → Spark → Delta, 배포·관측(예정) |
| `ops/compose/` | 인프라 | C | 로컬 개발용 Compose(`dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`), 패키지 확인(`pycheck.py`), 팀원 절차·점검표(`README.md`) |
| `requirements/` | 의존성 | 공용 | 영역별 Python 패키지(`web`·`ml`·`llm`·`pipeline`, Python 3.14) |
| `intent/`, `ml/` | 인프라(모델) | D | 의도 해석기, 씐 자 확률 모델(예정) |
| `content/` | 데이터 | E | 인물, 비밀, 단서, 템플릿(예정) |
| `content/images/`, `docs/assets/`, `tools/assets/` | 데이터(에셋) | E | 이미지 실제·임시 파일, `manifest.json`(콘텐츠 ID → 파일), 생성 기록, 스타일 가이드·규격, ID 1:1 검사(예정) |
| `ads/domain/` | 광고 도메인 | D | 경매, 2등 가격, 예산 예약·확정·해제, 페이싱, CTR, 유효 판정, 대사, 지면 규격(`SLOT_SIZES`)(표준 라이브러리만) |
| `ads/app/`, `ads/infra/`, `ads/web/` | 광고 응용, 인프라, 표현 | D | 요청·비콘·클릭 흐름, MySQL 원장·아웃박스·Kafka, Django 뷰·admin(예정) |
| `ads/testing/` | 테스트 지원 | D | `FakeAdProvider` |
| `server/app/ads_port.py` | 응용(게임 쪽) | B | `AdProvider` 포트와 보호 래퍼 `AdGateway` |
| `llm/` | 인프라 | E | LLM 서비스, LangGraph(예정) |
| `contracts/` | 데이터 계약 | 공용 | 학습 행, 의도 예시, 시나리오 스키마 |
| `fixtures/` | 생성물 | 공용 | 계약 예시. `tools/build_fixtures.py`가 만든다 |
| `tools/` | 도구 | 공용 | 픽스처 생성, 합성 데이터 생성, 태스크 문서 파서(`tasks_md.py`)·이슈 동기화(`tasks_to_issues.py`)·계획 대비 실제 시간 요약(`hours_report.py`) |
| `tests/contract/` | 테스트 | 공용 | 계약 테스트(unittest) |
| `tests/tools/` | 테스트 | 공용 | 도구 테스트(태스크 파서·이슈 동기화) |
| `.github/` | 저장소 운영 | E | 이슈 양식(`ISSUE_TEMPLATE/`), 라벨 정의(`labels.yml`), PR 양식, CI 워크플로(E-05-02) |
| `docs/modules/`, `docs/troubles/` | 문서 | 각 흐름 | 모듈 문서, 흐름별 트러블 기록(양식 `_template.md`) |

"예정"인 폴더는 담당 흐름이 처음 코드를 넣을 때 만든다.

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 게임 규칙 변경 | 도메인 `engine/domain/model.py`, `docs/contracts/domain.md` |
| 엔진과 바깥의 인터페이스 변경 | `engine/ports.py`, `docs/contracts/ports.md` |
| 화면이나 API 추가 | 표현 `server/api/`, 응용 `server/app/dto.py`, `docs/contracts/api.md` |
| 저장소나 Kafka 교체 | 인프라 `server/infra/`, `docs/contracts/ports.md` 2절 |
| 이벤트나 학습 데이터 형식 변경 | `engine/domain/events.py`, `contracts/datasets.py`, `docs/contracts/events.md` |
| 모델 교체·승격·롤백 | `docs/contracts/ml_lifecycle.md` |
| 콘텐츠 추가 | `docs/contracts/content.md` |
| 개인정보 처리 | `docs/contracts/privacy.md` |
| 서버와 버전 | `docs/contracts/tech_stack.md`, 기획서 6.3 |
| 로컬 개발 환경 띄우기·Python 패키지 설치 | `ops/compose/README.md`, `requirements/` |
| 다른 흐름 없이 개발 시작 | `docs/contracts/parallel_dev.md` |
| 오늘 내가 할 일과 완료 조건 | `docs/tasks/<흐름>.md`(A_engine, B_web, C_infra, D_ml_ads, E_pm), 발표 작업은 발표 트랙 `docs/tasks/P_presentation.md`(S-xx, 담당 P-24) |
| 태스크 완료 증빙 쓰기(실제 소요 시간 포함) | `docs/tasks/README.md` 4절 양식 → `docs/evidence/` |
| 태스크 문서화(트러블·docstring은 태스크마다, 모듈 문서는 흐름별 하루 1회 일괄 갱신) | `docs/tasks/README.md` 10절 → `docs/modules/`, `docs/troubles/` |
| 이슈 만들기·동기화, 오늘 열린 태스크 보기 | `python -m tools.tasks_to_issues --dry-run`, `docs/tasks/README.md` 11절 |
| 태스크 조건 바꾸기 | `docs/tasks/<흐름>.md`를 고치는 PR(이슈 본문은 직접 고치지 않음) |
| 로그·지표 이름 정하기 | `docs/tasks/README.md` 5절 |
| 이미지 추가·교체 | 같은 경로에 덮어쓰기만. 규격·파일명은 `docs/assets/spec.md`, 코드는 `manifest.json`·`image_path`로만 참조(`docs/tasks/E_pm.md` `[에셋]`) |
| 아침 실종 발표·피해 장소 공개, 알리바이 명제 | `engine/domain/model.py`(`DisappearanceRecord`, `alibi_propositions`), `docs/contracts/domain.md` 6절 |
| 경매·과금·광고 판정 규칙 변경 | 광고 도메인 `ads/domain/`, `docs/contracts/ads.md` |
| 광고 지면을 게임 화면에 붙이기 | `server/app/ads_port.py`, `docs/contracts/ports.md` 5절 |

## 명령

```
python -m unittest discover -s tests -t .                 # 계약 테스트
python -m tools.build_fixtures                            # 픽스처 재생성(계약 변경 후)
python -m tools.gen_synthetic --games 50 --seed 7 --ads 20000 --out out/synthetic
python -m tools.tasks_to_issues --dry-run                 # 태스크 → 이슈 계획(실제 생성은 --apply --repo)
python -m tools.tasks_to_issues --hours                   # 흐름별 일별 시간(문서화·일괄 갱신 포함, 흐름별 한도, 발표 트랙은 별도 줄)
python -m tools.hours_report --until 11-05                # 계획 대비 실제 소요 시간(증빙의 "실제 소요 시간(h)")
```

접속 정보(IP, 계정, 비밀번호)는 이 저장소에 넣지 않는다.
