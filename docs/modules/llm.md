# llm — 로컬 LLM 추궁 프롬프트·검증기·벤치마크와 (예정) LLM 서비스

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

NPC 추궁 답변의 말투를 바꾸는 로컬 LLM(llama.cpp, CPU 전용)을 다루는 패키지다. 지금(11/7) `dev`에 있는 것은 다음과 같다.

- 추궁 프롬프트 `llm/prompt.py`(E-07-07, `interrogate-v8`): 엔진의 사실 문장과 반응 의도(해명·회피·역공·고백)를 받아, 사실 요소는 그대로 두고 끝 어미와 앞뒤 덧붙임만 인물 말투 계열로 바꾸게 하는 채팅 메시지를 만든다. 말투 계열별 예시(`STYLE_PROFILES`)와 생성 설정(`GEN_SETTINGS`)이 여기 있다.
- 검증기 `llm/validator.py`(E-07-07): 생성 대사를 받아들일지 판정한다. 거부 사유 4종(`format_length`, `forbidden_entity`, `added_assertion`, `fact_changed`)이고, 거부하면 엔진의 사실 문장을 그대로 쓴다(폴백).
- 평가 세트 생성 `llm/eval_set.py`(공용 픽스처로 추궁 요청 100건 `fixtures/llm/interrogation_eval.json`)와 지표 계산 `llm/mvp_metrics.py`(성공률·사유 분포·단계별 지연·일관성. 모델 호출은 바깥에서 주입).
- 벤치마크 `llm/bench/run_bench.py`(E-06-07, E-07-07에서 실제 프롬프트·검증기와 `--eval` 평가 모드 연결). 벤치마크 모드는 추궁 요청 픽스처(`fixtures/llm/interrogation_requests.json`)로 후보 모델마다 지연·생성 토큰/초·메모리를 재고, 결과 JSON을 증빙(`docs/evidence/perf/E-06-07/`)에 쓴다. HTTP 서비스(`llm/service/`)와 LangGraph 흐름은 E-08-09에서 들어온다(지금은 빈 패키지).

계층: `prompt.py`·`validator.py`·`mvp_metrics.py`·`eval_set.py`는 규칙(순수 함수, 표준 라이브러리만, 단독 테스트 `tests/llm/`)이고, llama.cpp를 부르는 쪽(지금은 벤치마크, 뒤에 서비스 E-08-09)이 인프라다. 의존 방향은 인프라 → 규칙이다. 벤치마크는 인프라(측정)이고, 파일 안에서는 위쪽 순수 함수(`percentile`·`build_messages`·`strip_think`·`validate_output`·`parse_buffer_sizes`·`summarize`, 단독 테스트) / 아래쪽 인프라(`BenchModel` llama.cpp 호출, `process_memory_mib`, `write_outputs`·`import_received` 결과 기록) / `main`(인자 파싱과 흐름 조립)으로 나눈다. `llama_cpp`·`jinja2`는 함수 안에서 늦게 import해, CI `core`는 이 패키지 없이 순수 함수 테스트(`tests/llm/test_run_bench.py`)를 돌린다.

## 왜

- 근거: 기획서 6.3(모델 선정과 지연), 태스크 E-06-07, 결과·결정 [`docs/reports/llm_bench.md`](../reports/llm_bench.md), 증빙 [`docs/evidence/E-06-07.md`](../evidence/E-06-07.md).
- 측정 구조를 서비스(E-08-09)와 같게 둔다: 모델 인스턴스 1개 + FIFO 큐 + 작업자 스레드 1개. 한 라운드에 요청 N건을 동시에 넣고 요청마다 큐 대기·프롬프트 처리·생성·검증을 나눠 잰다. 전체 지연 = 큐 대기 + 처리 + 검증. 동시 요청이 몰릴 때 사용자가 실제로 기다리는 시간이 이 값이라서다.
- 단계별 탈락으로 측정 행렬을 줄인다: ① 모든 후보를 같은 조건(Windows 직접 실행, 스레드 6, 단일 요청)으로 재 상위만 남김 → ② 스레드 4·6·8 비교 → ③ 최종 조합만 동시 2·4건과 개발 도구 켬·끔 비교.
- **결정(잠정, C-06-04 전달용)**: Kanana-2-3B Instruct Q4_K_M, 노트북 3 Windows 직접 실행, 스레드 8, 배정 RAM 6 GB. 동시 4건 p95는 14,419 ms(개발 도구·VM 켬)·12,419 ms(끔)로 기준 15 s 이하다. llm-01 VM 실행 비교 전이라 실행 방식은 잠정이다. 차선은 Qwen3.5-4B(Apache-2.0)다. 근거와 수치는 보고서 요약·3·4절.
- 추궁 프롬프트(E-07-07, 기획서 7.2 3~5단계, 증빙 [`docs/evidence/E-07-07.md`](../evidence/E-07-07.md)): 3B 모델은 지시보다 예시·형식을 강하게 따른다. 그래서 반응 의도별 예시 한 쌍을 말투 계열마다 두고, 예시에는 게임에 없는 장소·날짜를 넣어 베끼면 검증기에서 거부되게 했다. 플레이어가 받는 정보량은 LLM이 실패해도 같아야 하므로, 사실 요소는 바꾸지 못하게 하고 거부되면 사실 문장으로 폴백한다. 사투리 계열은 배경 솔뫼골에 맞춰 강원도식이다(11/6 사용자 결정).
- 다른 기기(VM)에서 잰 결과는 `--out`으로 받아 `--import-json`으로 기록한다. 측정 커밋과 지금 HEAD가 같을 때만 받아, 증빙의 커밋 SHA와 측정 코드가 어긋나지 않게 한다.

## 사용법

```
python -m pip install --only-binary=:all: -r requirements/llm.txt          # llama-cpp-python CPU 휠(빌드 도구 없이)
python -m llm.bench.run_bench --help
python -m llm.bench.run_bench --model <gguf 경로(저장소 밖)> --label kanana2_3b --stage s3 --threads 8 --concurrency 4 --repeats 5 --tag devon
python -m llm.bench.run_bench --model <gguf> --label kanana2_3b --stage s2 --mode vm --threads 8 --out <json>   # 다른 기기에서
python -m llm.bench.run_bench --import-json <json> --origin "VM llm-01"                                       # 받은 결과 기록
python -m llm.bench.run_bench --model <gguf> --label kanana2_3b --eval fixtures/llm/interrogation_eval.json --consistency-n 20 --consistency-seeds 1,2,3,4,5   # 평가 모드(E-07-07)
python -m llm.bench.run_bench --model <gguf> --label kanana2_3b --eval fixtures/llm/interrogation_eval.json --rejudge   # 기록 재판정(검증 규칙만 바뀔 때)
python -m llm.eval_set --seed 1                                                                              # 평가 세트 다시 만들기(같은 시드 = 같은 바이트)
python -m unittest discover -s tests/llm -t .                                                               # 순수 함수 테스트(llama_cpp 없이)
```

벤치마크 모드 결과 이름은 `<stage>_<label>_<mode>_t<threads>_c<concurrency>[_<tag>]`이고, 기본은 `docs/evidence/perf/E-06-07/<이름>.json`에 쓴다. 평가 모드는 `docs/evidence/perf/E-07-07/`, 요청별 기록은 `out/perf/E-07-07/`(커밋 제외)에 쓴다. 로그는 `out/logs/llm_bench.jsonl`(커밋 제외).

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `--threads` | 6 | llama.cpp 스레드 수(결정값 8) | i5-1340P에서 4·6·8 중 8이 가장 빨랐다(차이 약 1 s, 오차 범위일 수 있음) |
| `--concurrency` | 1 | 한 라운드에 동시에 넣는 요청 수 | 늘리면 큐 대기가 지연 대부분을 차지한다(동시 4건 큐 대기 p95 약 10.5 s) |
| `--repeats` | 5 | 라운드 수(요청 수 = repeats × concurrency) | 표본이 작으면 p95가 최댓값에 가깝다 |
| `--max-tokens` | 128 | 생성 상한 토큰 | 임시 프롬프트에서 평균 생성은 약 10 토큰 |
| `--temperature` | 0.7 | 생성 온도(top_p 0.8, top_k 20 고정) | - |
| `--n-ctx` | 1024 | 컨텍스트 길이 | KV 캐시 메모리(Kanana 128 MiB)가 비례한다 |
| `--seed` | 1 | 생성 시드 | - |
| `--mode` | `win` | 실행 방식 표시(`win`·`vm`) | 결과 이름에만 들어간다 |
| `--out` / `--import-json` | 없음 | 다른 기기 측정 결과 내보내기·받기 | 받을 때 측정 커밋 = 지금 HEAD여야 한다 |
| `--eval` / `--eval-seed` / `--eval-n` | 없음 / 0 / 전부 | 평가 모드: 평가 세트 파일, 생성 시드, 앞 N건만 | - |
| `--consistency-n` / `--consistency-seeds` | 0 / `1,2,3,4,5` | 일관성: 앞 N건을 시드마다 반복 | 0이면 일관성을 재지 않는다 |
| `--rejudge` | 꺼짐 | 모델 없이 요청 기록을 지금 검증기로 다시 판정 | 프롬프트가 바뀌었으면 쓰지 않고 다시 잰다 |
| `PROMPT_VERSION`(`llm/prompt.py`) | `interrogate-v8` | 프롬프트 버전(요청 기록에 남는다) | 바꾸면 평가를 다시 잰다 |
| `GEN_SETTINGS`(`llm/prompt.py`) | temperature 0.7, top_p 0.8, top_k 20, max_tokens 128, n_ctx 1024, n_threads 8 | 생성 설정(평가 모드 기본값) | - |
| `MAX_CHARS` / `MAX_SENTENCES` / `MAX_ADDED_HANGUL`(`llm/validator.py`) | 120 / 4 / 25 | 생성 대사 길이·문장 수·사실 문장 대비 덧붙인 한글 수 상한 | 낮추면 폴백이 늘고, 높이면 덧붙임이 길어진다 |

## 한계

- **잠정 결정**: llm-01 VM 실행 비교와 llm-01 Python 3.14의 `llama_cpp` import 확인을 하지 않았다(선행 C-05-01·C-05-02 미완). 그래서 E-06-07 조건 3②·4가 남아 있다.
- E-06-07의 동시 요청 값은 임시 프롬프트(`bench-tmp-1`)로 잰 것이다. 실제 프롬프트(E-07-07)는 평균 약 208 토큰으로 길어, 동시 4건 p95가 늘어난다(v1 참고 측정 18,167 ms). 서비스(E-08-09)에서 줄여야 한다.
- 평가 세트는 공용 픽스처 인물로 만든다. 실제 콘텐츠 인물(`content/bundle_v1.json`)의 말투 계열 매칭은 확인했지만 평가 수치는 픽스처 기준이다. 평가 모드 성공 86 %, 폴백 14건은 모두 사실 변경이나 어색한 대사(E-07-07 증빙).
- 표본이 작다(단계별 9~20건). 지연의 약 75%가 프롬프트 처리다. 고정 접두어 KV 재사용은 재지 않았다(서비스 E-08-09에서 검토).
- 켬·끔 비교는 VM과 개발 도구가 함께 바뀌어 어느 쪽 영향인지 나눌 수 없다.
- Kanana GGUF는 커뮤니티 변환본이고, Kanana Open License는 "Powered by Kanana" 표기와 사용 제한 고지 의무가 있다(보고서 1절).
- 코랩 실행은 이 모듈에서 재지 않았다(스레드 2 Windows 측정으로 추정만, 보고서 5절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-06-07 | 11/6 | 처음 작성: 벤치마크 `llm/bench/run_bench.py`(모델 1개 + FIFO 큐, 큐 대기·프롬프트 처리·생성·검증 분리 측정, 버퍼 메모리, 다른 기기 결과 받기)와 잠정 결정(Kanana-2-3B Q4_K_M, Windows 직접, 스레드 8, RAM 6 GB, 동시 4건 p95 14,419 ms) |
| E-07-07 | 11/7 | 추궁 프롬프트 `llm/prompt.py`(interrogate-v8, 말투 계열별 예시·끝 어미), 검증기 `llm/validator.py`(거부 사유 4종, 폴백), 평가 세트 `llm/eval_set.py`(100건)·지표 `llm/mvp_metrics.py`, 벤치마크 `--eval`·`--rejudge`·일관성 모드. 평가 성공 86 %, 전체 p95 5,575 ms |
| E-07-06 | 11/7 | MVP 제출 묶음 `tools/build_llm_bundle.py`(zip 4개 파일, 결정적)과 코랩 노트북 `llm/colab/paldo_llm_mvp.ipynb`(첫 셀 Powered by Kanana 표기, T4 GPU 경로) 추가. llm 규칙 코드는 바꾸지 않음. 코랩 T4 평가 성공 93 %, 전체 p95 478 ms |
