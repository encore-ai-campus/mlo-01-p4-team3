# llm — 로컬 LLM 추궁 프롬프트·검증기·벤치마크와 (예정) LLM 서비스

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

NPC 추궁 답변의 말투를 바꾸는 로컬 LLM(llama.cpp, CPU 전용)을 다루는 패키지다. 지금(11/7) `dev`에 있는 것은 다음과 같다.

- 추궁 프롬프트 `llm/prompt.py`(E-07-07, `interrogate-v8`): 엔진의 사실 문장과 반응 의도(해명·회피·역공·고백)를 받아, 사실 요소는 그대로 두고 끝 어미와 앞뒤 덧붙임만 인물 말투 계열로 바꾸게 하는 채팅 메시지를 만든다. 말투 계열별 예시(`STYLE_PROFILES`)와 생성 설정(`GEN_SETTINGS`)이 여기 있다.
- 검증기 `llm/validator.py`(E-07-07): 생성 대사를 받아들일지 판정한다. 거부 사유 4종(`format_length`, `forbidden_entity`, `added_assertion`, `fact_changed`)이고, 거부하면 엔진의 사실 문장을 그대로 쓴다(폴백).
- 평가 세트 생성 `llm/eval_set.py`(공용 픽스처로 추궁 요청 100건 `fixtures/llm/interrogation_eval.json`)와 지표 계산 `llm/mvp_metrics.py`(성공률·사유 분포·단계별 지연·일관성. 모델 호출은 바깥에서 주입).
- 벤치마크 `llm/bench/run_bench.py`(E-06-07, E-07-07에서 실제 프롬프트·검증기와 `--eval` 평가 모드 연결). 벤치마크 모드는 추궁 요청 픽스처(`fixtures/llm/interrogation_requests.json`)로 후보 모델마다 지연·생성 토큰/초·메모리를 재고, 결과 JSON을 증빙(`docs/evidence/perf/E-06-07/`)에 쓴다. HTTP 서비스(`llm/service/`)와 LangGraph 흐름은 E-08-09에서 들어온다(지금은 빈 패키지).
- 의도 예시 변형(E-06-02 2단계): 규칙 `llm/intent_variants.py`(프롬프트·출력 파싱·보존 검사, 표준 라이브러리만)와 생성·반영 `llm/bench/gen_intent_variants.py`(llama.cpp 배치 → `out/intent_variants/`, 걸러서 `content/intent/examples_v1.jsonl`에 `generated`로 반영, D 검수 시트 `docs/evidence/perf/E-06-02/variants_review.csv`). 의도 해석기(D) 학습 데이터를 늘리는 오프라인 작업이고 게임 런타임과 관계없다.

- 자유 질문 답변(E-08-13, E-11-12 GPU 시연 선행): 규칙 `llm/free_answer.py`(프롬프트 `free-answer-v2`(E-11-12 자기소개 사실 `s1`·`s2`와 인사·잡담 안내), 첫 줄 `근거: f1,f3` + 답 1~2문장 형식의 스트리밍 파서 `AnswerStream`, 검사 6종 `format_length`·`evidence_range`·`pair_binding`·`polarity`·`leak`·`ungrounded`, 문장 단위 판정 `SentenceGate`, 폴백)와 평가 세트 생성 `llm/free_answer_eval.py`(`fixtures/llm/free_answer_eval.json`, `free-answer-eval-2` 121건: E-08-13 75건은 11/8 사용자 확인 완료, E-11-12 자유 대화 40건·8턴 6건의 새 문장 틀 17개 `chat_sentences`는 11/9 사용자 승인(지문 `APPROVED_CHAT_SENTENCES_SHA256`)). 인프라 `llm/bench/free_answer_load.py`(OpenAI 호환 스트리밍 닫힌 루프 부하, 결과에 유형별 `qtype_mix`), `llm/bench/free_chat.py`(E-11-12 사람 자유 대화 터미널 클라이언트, 턴 기록·요약), `llm/bench/watch_pods.py`(노트북의 RunPod 비용 누계·시간 초과 삭제), `llm/bench/run_pod.sh`(Pod 안 타이머·반출·자기 삭제). 실행 절차는 [`ops/runbook/gpu_demo.md`](../../ops/runbook/gpu_demo.md). 기존 추궁 검증기(`validator.py`)는 완성 문장에 말투만 입히는 계약이라 그대로 두고 따로 만들었다.
- Kanana 생성 서버(E-10-13, 11/10 사용자 결정으로 Claude Code 작성에서 바꿈): 별도 CPU 기기의 llama.cpp `llama-server`(Kanana-2-3B Instruct Q4_K_M, 빌드 b12242, 127.0.0.1:8080, OpenAI 호환)에 노트북에서 SSH 터널로 붙어 템플릿 변형 후보를 만든다. 게임 런타임은 이 서버를 부르지 않는다(P-31 (나) 유지). 운영 절차는 [`ops/runbook/llm_gen.md`](../../ops/runbook/llm_gen.md), 주소·계정은 저장소 밖(`4th_private`). 생성 코드는 아직 없다(E-10-13에서 인프라로 추가, 검사는 위 규칙 함수 재사용).

계층: `prompt.py`·`validator.py`·`mvp_metrics.py`·`eval_set.py`·`free_answer.py`·`free_answer_eval.py`는 규칙(순수 함수, 표준 라이브러리만, 단독 테스트 `tests/llm/`)이고, llama.cpp를 부르는 쪽(지금은 벤치마크, 뒤에 오프라인 사전 생성 E-10-13(Kanana 생성 서버의 OpenAI 호환 API 호출). 서비스 E-08-09는 11/7 P-31 (나)로 취소)이 인프라다. 의존 방향은 인프라 → 규칙이다. 벤치마크는 인프라(측정)이고, 파일 안에서는 위쪽 순수 함수(`percentile`·`build_messages`·`strip_think`·`validate_output`·`parse_buffer_sizes`·`summarize`, 단독 테스트) / 아래쪽 인프라(`BenchModel` llama.cpp 호출, `process_memory_mib`, `write_outputs`·`import_received` 결과 기록) / `main`(인자 파싱과 흐름 조립)으로 나눈다. `llama_cpp`·`jinja2`는 함수 안에서 늦게 import해, CI `core`는 이 패키지 없이 순수 함수 테스트(`tests/llm/test_run_bench.py`)를 돌린다.

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
python -m llm.bench.gen_intent_variants generate --model <gguf> --threads 8 --seed 0                         # 의도 예시 변형 후보(E-06-02, 끊기면 이어서). 모델 경로는 PALDO_LLM_MODEL로도
python -m llm.bench.gen_intent_variants apply                                                                # 후보를 걸러 examples_v1.jsonl에 generated로 반영 + D 검수 시트
python -m llm.free_answer_eval --seed 1                                                                     # 자유 질문 평가 세트(121건, 앞 75건 11/8 사용자 확인 완료)
python -m llm.bench.free_chat --base-url <사설망 주소> --model <이름> --npc jumo --seed 1 --minutes 10 --label chat_s1   # 사람 자유 대화 10분(E-11-12 5번, /끝으로 종료)
python -m llm.bench.free_answer_load --base-url <사설망 주소> --model <이름> --sessions 30 --think 5 15 --warmup 120 --duration 600 --label s2_normal   # 부하(E-11-12)
python -m llm.bench.watch_pods --account B --key-env PALDO_RUNPOD_KEY_B --ledger out/runpod/ledger_B.json   # E 노트북에서 계정마다 하나씩(그 계정 키만 runpodctl에, 확인 질문에 yes)
STAGE=s1 MAX_MINUTES=90 EXPORT_CMD='<반출 명령>' bash llm/bench/run_pod.sh <작업 명령>                          # Pod 안
```

자유 질문 부하 결과는 `out/perf/E-11-12/<label>.json`(커밋 제외, 증빙에 옮길 때 주소·Pod ID 제외)에 쓴다. 자유 대화 세션은 같은 폴더에 `<label>.turns.jsonl`(턴마다 질문·결과·공개 문장)과 `<label>.json`(통과·폴백·오류 비율, 막은 사유별 기록 수, 누출을 막은 턴 수, 공개 문장 재검사 누출 수)을 쓴다. 세션 키는 `PALDO_LLM_KEY` 환경 변수로만 넣는다.

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
| `PROMPT_VERSION` / `GEN_SETTINGS`(`llm/intent_variants.py`) | `intent-variants-v1` / temperature 0.9, top_p 0.9, top_k 40, max_tokens 160, n_ctx 1024 | 의도 예시 변형 생성(E-06-02). 다양성을 위해 추궁보다 온도가 높다 | 바꾸면 후보를 다시 만들고 D 검수도 다시 받는다 |
| `MAX_CHARS` / `MAX_SENTENCES` / `MAX_ADDED_HANGUL`(`llm/validator.py`) | 120 / 4 / 25 | 생성 대사 길이·문장 수·사실 문장 대비 덧붙인 한글 수 상한 | 낮추면 폴백이 늘고, 높이면 덧붙임이 길어진다 |
| `MAX_SENTENCES` / `MAX_ANSWER_CHARS` / `MAX_UNKNOWN_NOUNS`(`llm/free_answer.py`) | 2 / 120 / 2 | 자유 질문 답 문장 수, 글자 수, 문장당 모르는 명사 후보 수 상한 | 명사 상한을 올리면 지어낸 내용이 더 통과한다 |
| `EVENT_WORDS`(`llm/free_answer.py`) | 독살·죽었·피해자 등 31개 | 인용 사실에 없으면 거부하는 사건 단정 어휘 | 늘리면 정상 답이 걸릴 수 있어 반례 표 테스트를 같이 고친다 |
| `TIMEOUT_S`(`llm/bench/free_answer_load.py`) | 30 | 요청 한 건(재생성 포함) 시간 제한. 넘으면 무한대로 센다 | E-11-12 3번과 같이 바꾼다 |
| `LIMITS` / `DELETE_RATIO` / `MAX_MINUTES` / `GRACE_MINUTES`(`llm/bench/watch_pods.py`) | E $5·B $5·C $4.5·D $4.5 / 0.8 / s0 20·s1 90·s2 90 / 10 | 계정 한도(11/8 사용자 결정), 삭제선 비율, 단계별 Pod 상한(`run_pod.sh`와 같음), 시간 초과 삭제 여유 | 바꾸면 `run_pod.sh`·E-11-12 7번·런북을 같이 고친다 |
| `KEY_ENV_PREFIX`(`llm/bench/watch_pods.py`) | `PALDO_RUNPOD_KEY_` | 계정별 API 키 환경 변수 이름 접두어. `--key-env`로 고른 변수 값만 자식 `runpodctl`에 `RUNPOD_API_KEY`로 넘기고 이 접두어의 다른 변수는 뺀다(11/8 사용자 결정으로 E가 네 계정 운영) | 값은 저장소 밖(`4th_private`), 출력하지 않음 |
| `PROMPT_VERSION` / `GEN_SETTINGS` / `MAX_ITEMS` / `MAX_GAME_ITEMS` / `MAX_SENTENCE_CHARS`(`llm/review_narration.py`) | `review-v1` / temperature 0.3, top_p 0.9, max_tokens 900 / 3 / 10 / 70 | 복기 해설 LLM 요약(E-10-01): NPC당 요약 항목, 판 전체 항목 상한(넘으면 마지막이 아닌 투표부터 뺌), 문장 글자 수 상한 | 항목 상한을 올리면 3B 모델 출력이 요청 제한(90초)을 넘을 수 있다 |
| `PALDO_REVIEW_LLM_URL` / `PALDO_REVIEW_DEADLINE_S`(게임 서버 환경 변수) | 비움 / 180 | 복기 해설 생성 서버 주소(비우면 기능·Kanana 표기 꺼짐), 작업 마감 | 주소 값은 저장소 밖. 운영 `ops/runbook/llm_gen.md` 7절 |

## 한계

- **잠정 결정**: llm-01 VM 실행 비교와 llm-01 Python 3.14의 `llama_cpp` import 확인을 하지 않았다(선행 C-05-01·C-05-02 미완). 그래서 E-06-07 조건 3②·4가 남아 있다.
- E-06-07의 동시 요청 값은 임시 프롬프트(`bench-tmp-1`)로 잰 것이다. 실제 프롬프트(E-07-07)는 평균 약 208 토큰으로 길어, 동시 4건 p95가 늘어난다(v1 참고 측정 18,167 ms). 서비스(E-08-09)에서 줄여야 한다.
- 평가 세트는 공용 픽스처 인물로 만든다. 실제 콘텐츠 인물(`content/bundle_v1.json`)의 말투 계열 매칭은 확인했지만 평가 수치는 픽스처 기준이다. 평가 모드 성공 86 %, 폴백 14건은 모두 사실 변경이나 어색한 대사(E-07-07 증빙).
- 표본이 작다(단계별 9~20건). 지연의 약 75%가 프롬프트 처리다. 고정 접두어 KV 재사용은 재지 않았다(서비스 E-08-09에서 검토).
- 켬·끔 비교는 VM과 개발 도구가 함께 바뀌어 어느 쪽 영향인지 나눌 수 없다.
- Kanana GGUF는 커뮤니티 변환본이고, Kanana Open License는 "Powered by Kanana" 표기와 사용 제한 고지 의무가 있다(보고서 1절).
- 코랩 실행은 이 모듈에서 재지 않았다(스레드 2 Windows 측정으로 추정만, 보고서 5절).
- **자유 질문 검사기(`free_answer.py`)는 의미 분석이 아니다.** 어휘 목록과 띄어쓰기·조사 규칙으로 판별하고, 못 잡는 주장은 E-11-12 사람 판정(E)으로 보완한다. 허용 한계(테스트 `tests/llm/test_free_answer.py`의 `LIMIT_CASES`로 고정):
  - 모르는 명사 후보는 문장당 2개까지 통과한다("도끼와 밧줄을 들고 있었소"). 조사 없이 쓴 명사와 평가어("수상하오")는 세지 않는다.
  - 부정이 어느 서술어에 걸리는지 모른다. 사실 문장의 부정 구조와 같게 말해야 통과한다("서낭당에서 아무도 못 봤소" 사실로 "서낭당에 있었소"라고 하면 거부).
  - 부정이 2개 이상이거나 전언(`~다는`, `~라고`)과 함께 나오면 판별 불가로 거부한다("갑이 우물에 없었다는 말은 사실이 아니오"는 뜻이 맞아도 거부).
  - 절 경계는 쉼표와 연결 어미(`고`·`며`·`는데`·`지만`·`다가`·`면서`)뿐이다. "초저녁과 한밤에 주막과 집에 있었소"처럼 한 절에 시간 둘·장소 둘을 묶으면 뜻이 맞아도 거부한다.
  - 사실 문장은 NPC 본인 시점이라고 본다(모든 절에 본인을 더함). 남의 행적을 전해 들은 형식의 사실은 다루지 않는다.
  - 어휘에 없는 시간 표현("그날", "그때")은 시간으로 세지 않는다. "어젯밤"처럼 어휘에 있는 다른 시간은 인용 사실에 없으면 거부한다.
  - 자기소개 사실(`s`로 시작하는 ID)을 인용한 문장은 인물·장소·시간이 하나도 없어도 통과한다(E-11-12, "따져 보는 편이오"). 그래서 자기소개 사실을 인용하고 인물·장소·시간 없이 지어낸 본인 이야기("나는 장기를 잘 두오")는 모르는 명사 상한 안이면 통과한다. 다른 인물·장소·시간을 말하면 그대로 거부한다.
- 평가 세트(`free_answer_eval.json`)의 새로 쓴 문장 12개(`new_sentences`)는 11/8 사용자가 전부 승인했다. 생성기는 `new_sentences`가 승인본 지문(`APPROVED_NEW_SENTENCES_SHA256`)과 같을 때만 `status`를 확인 완료로 쓰고, 다른 시드·콘텐츠·문장 틀로 문장이 바뀌면 확인 전으로 남긴다. 행적은 콘텐츠 묶음 일과 확률로 뽑은 것이라 실제 엔진 판 기록과 같지 않다(사실 문장 형식만 엔진과 같다).
- 부하 클라이언트·Pod 스크립트는 가짜 스트리밍 서버·가짜 `runpodctl`로만 시험했다. 실제 서버의 스트리밍 조각 모양, `runpodctl` 목록 출력 형식, Pod 범위 키의 자기 삭제 권한, `runpodctl`이 `RUNPOD_API_KEY` 환경 변수를 설정 파일보다 먼저 쓰는지(`--key-env` 계정 격리의 전제)는 E-11-12 0단계에서 확인한다(런북 "미확인"). 키 격리가 실패해도 이름의 계정 칸이 다른 Pod는 세지도 지우지도 않지만, 그 계정의 비용은 그 계정 감시가 못 보게 되므로 0단계에서 격리를 확인하기 전에는 시작하지 않는다. 요청 마감(30초)은 연결 전에 정하고 스트림 읽기는 감시 타이머로 끊지만, 응답 머리를 받는 동안은 소켓 무응답 제한만 걸린다(머리를 아주 천천히 보내는 서버는 마감을 넘길 수 있음).

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-06-07 | 11/6 | 처음 작성: 벤치마크 `llm/bench/run_bench.py`(모델 1개 + FIFO 큐, 큐 대기·프롬프트 처리·생성·검증 분리 측정, 버퍼 메모리, 다른 기기 결과 받기)와 잠정 결정(Kanana-2-3B Q4_K_M, Windows 직접, 스레드 8, RAM 6 GB, 동시 4건 p95 14,419 ms) |
| E-07-07 | 11/7 | 추궁 프롬프트 `llm/prompt.py`(interrogate-v8, 말투 계열별 예시·끝 어미), 검증기 `llm/validator.py`(거부 사유 4종, 폴백), 평가 세트 `llm/eval_set.py`(100건)·지표 `llm/mvp_metrics.py`, 벤치마크 `--eval`·`--rejudge`·일관성 모드. 평가 성공 86 %, 전체 p95 5,575 ms |
| E-07-06 | 11/7 | MVP 제출 묶음 `tools/build_llm_bundle.py`(zip 4개 파일, 결정적)과 코랩 노트북 `llm/colab/paldo_llm_mvp.ipynb`(첫 셀 Powered by Kanana 표기, T4 GPU 경로) 추가. llm 규칙 코드는 바꾸지 않음. 코랩 T4 평가 성공 93 %, 전체 p95 478 ms |
| E-08-13 | 11/8 | 자유 질문 답변 규칙 `llm/free_answer.py`(검사 6종, 문장 단위 판정, 폴백)·평가 세트 생성 `llm/free_answer_eval.py`(75건, 11/8 새 문장 12개 사용자 승인)·부하 클라이언트 `llm/bench/free_answer_load.py`·Pod 감시 `watch_pods.py`·Pod 스크립트 `run_pod.sh`, 테스트 2개 파일(반례 22개 전부 거부), 계층 검사 대상에 2개 추가 |
| E-11-12 | 11/8 | 계획 변경(사용자 결정): `watch_pods.py`에 계정 격리 추가. 이름의 계정 칸이 `--account`와 같은 Pod만 세고 지우며(다른 계정·못 읽는 이름은 매 주기 오류), `--key-env`로 계정 키를 자식 `runpodctl`에만 넘긴다. 다른 계정 항목이 든 장부는 지우거나 덮어쓰지 않고 시작을 거부한다(exit 2). E가 네 계정 Pod를 한 노트북에서 운영 |
| E-11-12 | 11/9 | 자유 대화 확장(사용자 결정): 자기소개 사실 `s1`·`s2`와 검사 예외, 프롬프트 `free-answer-v2`, 평가 세트 `free-answer-eval-2`(기존 75건 그대로 + 8턴 6건 + 자유 대화 5유형 40건, 새 문장 틀 17개 사용자 확인 전), 터미널 자유 대화 클라이언트 `llm/bench/free_chat.py`, 부하 결과 `qtype_mix` |
| E-11-12 | 11/9 | 새 문장 틀 17개 사용자 승인: `APPROVED_CHAT_SENTENCES_SHA256` 지문 확정, 평가 세트 `chat_status` 확인 완료. 실행 날짜 11/10 18시 이후 → 11/11 |
| E-06-02 | 11/7 | 의도 예시 변형 규칙 `llm/intent_variants.py`(프롬프트 `intent-variants-v1`, 보존 검사 사유 12종)와 생성·반영 `llm/bench/gen_intent_variants.py`(`generate`·`apply`), 테스트 `tests/llm/test_intent_variants.py`, 계층 검사 대상에 추가 |
| E-10-01 | 11/11 | 복기 해설 LLM 요약 규칙 `llm/review_narration.py`(항목 선정·요청 스키마로 NPC·인용 고정·문장 검사 6종·NPC별 템플릿 대체), 계층 검사 대상에 추가. 흐름·저장·HTTP·작업 프로세스는 `server/` 쪽(라우팅 [E](../routing/E.md)) |
| E-10-13 | 11/10 | 생성 수단 변경(사용자 결정): 템플릿 변형을 별도 CPU 기기의 Kanana 생성 서버(llama.cpp `llama-server`, 127.0.0.1 전용, SSH 터널)로 만든다. 운영 절차 `ops/runbook/llm_gen.md` 추가. 코드는 바꾸지 않음. 터널 경유 시험 짧은 답 1.11 s, 생성 17.8 tok/s |
