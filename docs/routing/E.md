# E 콘텐츠·LLM·에셋 규격·저장소 운영 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 콘텐츠 묶음, LLM 서비스, 에셋 규격·검사, 이슈 양식·훅·운영 기록.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/content.md`, `docs/modules/llm.md`, `docs/modules/assets.md`, `docs/modules/user_test.md`, `docs/modules/ci.md`, `docs/modules/project.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/E_pm.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 E 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, E 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `content/` | 데이터 | E | 인물, 비밀, 단서, 템플릿(예정) |
| `content/intent/` | 데이터 | E | 의도 해석 학습 예시 `examples_v1.jsonl`(사람 작성 원본 + LLM 변형, 계보 `lineage_root`). 검사 규칙 `tools/intent_examples.py`, 테스트 `tests/intent/test_examples_v1.py`. 형식은 `docs/contracts/intent.md` |
| `content/images/`, `docs/assets/`, `tools/assets/` | 데이터(에셋) | E(규격·스타일 가이드·임시 파일·검사), D(실제 파일·생성 기록·`cutout.py`, A 흐름 `[에셋]` 태스크, 11/5 결정) | 이미지 실제·임시 파일, `manifest.json`(콘텐츠 ID → 파일), 생성 기록, 스타일 가이드·규격, ID 1:1 검사(예정) |
| `llm/` | 인프라 | E | 벤치마크, 오프라인 템플릿 변형 사전 생성(E-10-13), GPU 지연 실험(E-11-12). 런타임 LLM 서비스·LangGraph(E-08-09)는 11/7 P-31 (나)로 취소 |
| `llm/prompt.py`, `validator.py`, `eval_set.py`, `mvp_metrics.py` | 도메인(규칙) | E | 추궁 프롬프트·생성 설정 `GEN_SETTINGS`, 검증기(사유 4종)와 폴백, 평가 세트 생성, `run_eval`과 MVP 지표. 표준 라이브러리와 `llm`만 import(`test_layers.py`). 서비스(E-08-09)·MVP 노트북(E-07-06)이 그대로 부른다 |
| `llm/colab/` | 표현(MVP 제출) | E | 코랩 노트북 `paldo_llm_mvp.ipynb`(설치·드라이브 다운로드·llama.cpp 생성 어댑터·표 출력만, 지표는 zip 안 `llm/mvp_metrics.py`). 커밋본은 출력·파일 ID를 비운다(E-07-06) |
| `llm/bench/` | 인프라(측정) | E | LLM 벤치마크 `run_bench.py`(llama.cpp, 모델 인스턴스 1개 + FIFO 큐로 지연·토큰/초·메모리 측정). 결과 `docs/reports/llm_bench.md`. 의도 예시 변형 생성 `gen_intent_variants.py`(`generate`: llama.cpp 배치 → `out/intent_variants/`, `apply`: 걸러서 `content/intent/examples_v1.jsonl`에 `generated`로 반영·D 검수 시트, E-06-02) |
| `llm/intent_variants.py` | 도메인(규칙) | E | 의도 예시 변형 프롬프트·출력 파싱·보존 검사(자리표시자·형태·부정·말투·새 어휘·점괘 극성). 표준 라이브러리만(`test_layers.py`) |
| `tests/assets/` | 테스트 | E | 에셋 도구 테스트(규격표 해석, 임시 세트 생성·대조) |
| `tests/llm/` | 테스트 | E | LLM 순수 함수 테스트(llama_cpp 없이 돈다) |
| `.github/` | 저장소 운영 | E | 이슈 양식(`ISSUE_TEMPLATE/`), 라벨 정의(`labels.yml`), PR 양식. GitHub Actions CI는 11/5 폐지(로컬 `precheck`로 대체) |
| `.githooks/` | 저장소 운영(표현) | E | `pre-push`: `git push` 때 `python -m tools.precheck`를 돌려 실패하면 푸시를 멈춘다(설치 `git config core.hooksPath .githooks`). 검사 조립은 `tools/precheck.py`(응용), 규칙은 `tools/check_*.py`, 설명은 `docs/modules/ci.md` |
| `tests/fixtures/secrets/` | 테스트 데이터 | E | `check_secrets` 탐지 테스트용 가짜 비밀값. 비밀정보 검사에서 이 폴더만 제외 |
| `docs/user_test/` | 문서(테스트 운영) | E | 사용자 테스트 계획 `plan.md`(일정·진행 순서·동의 문구 인용·설문 확정본·고정 시드 판 목록). 참가자 명단·연락처는 저장소 밖 |
| `docs/ops/` | 문서(운영 기록) | E | 팀원 안내서 `interim_guide.md`(받기·작업·제출·검토 절차), 저장소 배정 전 임시 운영 원장 `interim_ledger.md`(⑧ 대체·병합 기록·결함 표, `tools/interim.py`가 검사) |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 콘텐츠 추가 | `docs/contracts/content.md` |
| 결말 문안 바꾸기 | 데이터 `content/endings_v1.json`, 검사 `python -m tools.check_endings`(규칙·조립 `tools/check_endings.py`), 형식 `docs/contracts/content.md` 1절 `Ending` |
| 의도 예시 추가·검사 | 데이터 `content/intent/examples_v1.jsonl`, 규칙 `tools/intent_examples.py`(집계 `python -m tools.intent_examples`), 형식 `docs/contracts/intent.md`. LLM 변형 규칙은 도메인 `llm/intent_variants.py`, 생성·반영은 인프라 `llm/bench/gen_intent_variants.py` |
| LLM 모델·스레드·실행 방식 다시 재기 | 인프라 `llm/bench/run_bench.py`(`--help`), 결과·결정 `docs/reports/llm_bench.md` |
| 추궁 답변 프롬프트·검증 규칙·지표 정의 바꾸기 | 도메인 `llm/prompt.py`·`llm/validator.py`·`llm/mvp_metrics.py`(테스트 `tests/llm/`). 평가 세트는 `python -m llm.eval_set --seed 1`로 다시 만들고, 측정은 `run_bench.py --eval`(검증 규칙만 바꿨으면 `--eval … --rejudge`로 저장된 기록 재판정) |
| LLM MVP 제출 묶음·코랩 노트북 고치기 | 표현·응용 `tools/build_llm_bundle.py`(zip, `python -m tools.build_llm_bundle --out out/llm_mvp/`), 표현 `llm/colab/paldo_llm_mvp.ipynb`, 테스트 `tests/tools/test_build_llm_bundle.py`. zip·실행한 사본은 `out/`에만 |
| 사용자 테스트 일정·설문 문항·고정 시드 판 바꾸기 | `docs/user_test/plan.md`(동의 문구·수집 항목의 원본은 `docs/contracts/privacy.md`) |
| 이미지 추가·교체 | 같은 경로에 덮어쓰기만. 규격·파일명은 `docs/assets/spec.md`, 코드는 `manifest.json`·`image_path`로만 참조(기준 `docs/tasks/E_pm.md` "에셋 작업 기준", 실제 에셋 태스크는 `docs/tasks/A_engine.md` `[에셋]`, 담당 D) |
