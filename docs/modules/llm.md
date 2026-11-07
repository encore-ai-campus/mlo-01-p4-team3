# llm — 로컬 LLM 벤치마크와 (예정) LLM 서비스

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

NPC 추궁 답변의 말투를 바꾸는 로컬 LLM(llama.cpp, CPU 전용)을 다루는 인프라 패키지다. 지금(11/6) `dev`에 있는 것은 벤치마크 `llm/bench/run_bench.py` 하나다. 이 스크립트는 추궁 요청 픽스처(`fixtures/llm/interrogation_requests.json`)로 후보 모델마다 지연·생성 토큰/초·메모리를 재고, 결과 JSON을 증빙(`docs/evidence/perf/E-06-07/`)에 쓴다. HTTP 서비스(`llm/service/`)와 LangGraph 흐름은 E-08-09에서 들어온다(지금은 빈 패키지).

계층: 인프라(측정). 파일 안에서는 위쪽 순수 함수(`percentile`·`build_messages`·`strip_think`·`validate_output`·`parse_buffer_sizes`·`summarize`, 단독 테스트) / 아래쪽 인프라(`BenchModel` llama.cpp 호출, `process_memory_mib`, `write_outputs`·`import_received` 결과 기록) / `main`(인자 파싱과 흐름 조립)으로 나눈다. `llama_cpp`·`jinja2`는 함수 안에서 늦게 import해, CI `core`는 이 패키지 없이 순수 함수 테스트(`tests/llm/test_run_bench.py`)를 돌린다.

## 왜

- 근거: 기획서 6.3(모델 선정과 지연), 태스크 E-06-07, 결과·결정 [`docs/reports/llm_bench.md`](../reports/llm_bench.md), 증빙 [`docs/evidence/E-06-07.md`](../evidence/E-06-07.md).
- 측정 구조를 서비스(E-08-09)와 같게 둔다: 모델 인스턴스 1개 + FIFO 큐 + 작업자 스레드 1개. 한 라운드에 요청 N건을 동시에 넣고 요청마다 큐 대기·프롬프트 처리·생성·검증을 나눠 잰다. 전체 지연 = 큐 대기 + 처리 + 검증. 동시 요청이 몰릴 때 사용자가 실제로 기다리는 시간이 이 값이라서다.
- 단계별 탈락으로 측정 행렬을 줄인다: ① 모든 후보를 같은 조건(Windows 직접 실행, 스레드 6, 단일 요청)으로 재 상위만 남김 → ② 스레드 4·6·8 비교 → ③ 최종 조합만 동시 2·4건과 개발 도구 켬·끔 비교.
- **결정(잠정, C-06-04 전달용)**: Kanana-2-3B Instruct Q4_K_M, 노트북 3 Windows 직접 실행, 스레드 8, 배정 RAM 6 GB. 동시 4건 p95는 14,419 ms(개발 도구·VM 켬)·12,419 ms(끔)로 기준 15 s 이하다. llm-01 VM 실행 비교 전이라 실행 방식은 잠정이다. 차선은 Qwen3.5-4B(Apache-2.0)다. 근거와 수치는 보고서 요약·3·4절.
- 다른 기기(VM)에서 잰 결과는 `--out`으로 받아 `--import-json`으로 기록한다. 측정 커밋과 지금 HEAD가 같을 때만 받아, 증빙의 커밋 SHA와 측정 코드가 어긋나지 않게 한다.

## 사용법

```
python -m pip install --only-binary=:all: -r requirements/llm.txt          # llama-cpp-python CPU 휠(빌드 도구 없이)
python -m llm.bench.run_bench --help
python -m llm.bench.run_bench --model <gguf 경로(저장소 밖)> --label kanana2_3b --stage s3 --threads 8 --concurrency 4 --repeats 5 --tag devon
python -m llm.bench.run_bench --model <gguf> --label kanana2_3b --stage s2 --mode vm --threads 8 --out <json>   # 다른 기기에서
python -m llm.bench.run_bench --import-json <json> --origin "VM llm-01"                                       # 받은 결과 기록
python -m unittest tests.llm.test_run_bench                                                                  # 순수 함수 테스트(llama_cpp 없이)
```

결과 이름은 `<stage>_<label>_<mode>_t<threads>_c<concurrency>[_<tag>]`이고, 기본은 `docs/evidence/perf/E-06-07/<이름>.json`에 쓴다. 로그는 `out/logs/llm_bench.jsonl`(커밋 제외).

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
| `PROMPT_VERSION` | `bench-tmp-1` | 임시 프롬프트·검증 버전 | E-07-07 실제 프롬프트·검증기로 바꾸면 다시 잰다 |
| `MAX_CHARS` | 200 | 임시 검증의 출력 길이 상한(글자) | - |

## 한계

- **잠정 결정**: llm-01 VM 실행 비교와 llm-01 Python 3.14의 `llama_cpp` import 확인을 하지 않았다(선행 C-05-01·C-05-02 미완). 그래서 E-06-07 조건 3②·4가 남아 있다.
- 모든 값은 임시 프롬프트(`bench-tmp-1`)로 잰 것이다. 검증도 사실 문장 포함·200자 이하만 본다. 실제 프롬프트(E-07-07)로 답이 길어지면 동시 4건 p95의 여유(켬 0.6 s)가 사라질 수 있어 다시 잰다.
- 표본이 작다(단계별 9~20건). 지연의 약 75%가 프롬프트 처리다. 고정 접두어 KV 재사용은 재지 않았다(서비스 E-08-09에서 검토).
- 켬·끔 비교는 VM과 개발 도구가 함께 바뀌어 어느 쪽 영향인지 나눌 수 없다.
- Kanana GGUF는 커뮤니티 변환본이고, Kanana Open License는 "Powered by Kanana" 표기와 사용 제한 고지 의무가 있다(보고서 1절).
- 코랩 실행은 이 모듈에서 재지 않았다(스레드 2 Windows 측정으로 추정만, 보고서 5절).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-06-07 | 11/6 | 처음 작성: 벤치마크 `llm/bench/run_bench.py`(모델 1개 + FIFO 큐, 큐 대기·프롬프트 처리·생성·검증 분리 측정, 버퍼 메모리, 다른 기기 결과 받기)와 잠정 결정(Kanana-2-3B Q4_K_M, Windows 직접, 스레드 8, RAM 6 GB, 동시 4건 p95 14,419 ms) |
