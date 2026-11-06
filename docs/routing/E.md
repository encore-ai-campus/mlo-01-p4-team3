# E 콘텐츠·LLM·에셋 규격·저장소 운영 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 콘텐츠 묶음, LLM 서비스, 에셋 규격·검사, 이슈 양식·훅·운영 기록.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/content.md`, `docs/modules/llm.md`, `docs/modules/assets.md`, `docs/modules/ci.md`, `docs/modules/project.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/E_pm.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 E 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, E 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `content/` | 데이터 | E | 인물, 비밀, 단서, 템플릿(예정) |
| `content/images/`, `docs/assets/`, `tools/assets/` | 데이터(에셋) | E(규격·스타일 가이드·임시 파일·검사), D(실제 파일·생성 기록·`cutout.py`, A 흐름 `[에셋]` 태스크, 11/5 결정) | 이미지 실제·임시 파일, `manifest.json`(콘텐츠 ID → 파일), 생성 기록, 스타일 가이드·규격, ID 1:1 검사(예정) |
| `llm/` | 인프라 | E | LLM 서비스, LangGraph(예정) |
| `llm/prompt.py`, `validator.py`, `eval_set.py`, `mvp_metrics.py` | 도메인(규칙) | E | 추궁 프롬프트·생성 설정 `GEN_SETTINGS`, 검증기(사유 4종)와 폴백, 평가 세트 생성, `run_eval`과 MVP 지표. 표준 라이브러리와 `llm`만 import(`test_layers.py`). 서비스(E-08-09)·MVP 노트북(E-07-06)이 그대로 부른다 |
| `llm/bench/` | 인프라(측정) | E | LLM 벤치마크 `run_bench.py`(llama.cpp, 모델 인스턴스 1개 + FIFO 큐로 지연·토큰/초·메모리 측정). 결과 `docs/reports/llm_bench.md` |
| `tests/assets/` | 테스트 | E | 에셋 도구 테스트(규격표 해석, 임시 세트 생성·대조) |
| `tests/llm/` | 테스트 | E | LLM 순수 함수 테스트(llama_cpp 없이 돈다) |
| `.github/` | 저장소 운영 | E | 이슈 양식(`ISSUE_TEMPLATE/`), 라벨 정의(`labels.yml`), PR 양식. GitHub Actions CI는 11/5 폐지(로컬 `precheck`로 대체) |
| `.githooks/` | 저장소 운영(표현) | E | `pre-push`: `git push` 때 `python -m tools.precheck`를 돌려 실패하면 푸시를 멈춘다(설치 `git config core.hooksPath .githooks`). 검사 조립은 `tools/precheck.py`(응용), 규칙은 `tools/check_*.py`, 설명은 `docs/modules/ci.md` |
| `tests/fixtures/secrets/` | 테스트 데이터 | E | `check_secrets` 탐지 테스트용 가짜 비밀값. 비밀정보 검사에서 이 폴더만 제외 |
| `docs/ops/` | 문서(운영 기록) | E | 팀원 안내서 `interim_guide.md`(받기·작업·제출·검토 절차), 저장소 배정 전 임시 운영 원장 `interim_ledger.md`(⑧ 대체·병합 기록·결함 표, `tools/interim.py`가 검사) |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 콘텐츠 추가 | `docs/contracts/content.md` |
| LLM 모델·스레드·실행 방식 다시 재기 | 인프라 `llm/bench/run_bench.py`(`--help`), 결과·결정 `docs/reports/llm_bench.md` |
| 추궁 답변 프롬프트·검증 규칙·지표 정의 바꾸기 | 도메인 `llm/prompt.py`·`llm/validator.py`·`llm/mvp_metrics.py`(테스트 `tests/llm/`). 평가 세트는 `python -m llm.eval_set --seed 1`로 다시 만들고, 측정은 `run_bench.py --eval` |
| 이미지 추가·교체 | 같은 경로에 덮어쓰기만. 규격·파일명은 `docs/assets/spec.md`, 코드는 `manifest.json`·`image_path`로만 참조(기준 `docs/tasks/E_pm.md` "에셋 작업 기준", 실제 에셋 태스크는 `docs/tasks/A_engine.md` `[에셋]`, 담당 D) |
