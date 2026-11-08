# GPU LLM NPC 대화 시연 실행 절차 (E-08-13, E-11-12)

E-11-12(11/11) GPU 시연에서 B·C·D·E가 각자 RunPod 계정으로 Pod를 띄우고 지울 때 따르는 절차와 사용량 제한 체크리스트다. 측정 설계·판정 기준의 원본은 [E-11-12](../../docs/tasks/E_pm.md)이고, 코드 사용법은 모듈 문서 [`llm`](../../docs/modules/llm.md)에 있다. 이 문서는 게임 런타임 LLM 재도입이 아니다(P-31 (나) 유지). 계정·주소·키·Pod ID는 이 문서와 저장소에 적지 않는다.

## 1. 역할과 단계

| 단계 | 계정 | Pod | 최대 실행(`MAX_MINUTES`) | 하는 일 |
|---|---|---|---|---|
| 0 사전 확인 | E | 4090 × 2 | 20분 | Tailscale 사용자 공간 모드 연결, 자기 삭제, 결과 반출, `watch_pods.py` 감시 확인(아래 4절 미확인 항목) |
| 1 후보 측정 | E·B·C | E 4090 Kanana, B 4090 Qwen3, C L40S EXAONE | 90분 | 평가 세트 전체(세션 1개)와 닫힌 루프 8세션 5분 |
| 2 서비스 시연 | B·C·D·E | 계정마다 복제본 1개, E에 LiteLLM과 부하 발생기 | 90분 | 30세션 정상 10분 + 장애 넘김 5분 + 참고 3분 |

Pod 이름은 `paldo-e1112-<단계 s0|s1|s2>-<계정 소문자>-<번호>`로 짓는다(예: `paldo-e1112-s1-b-1`). `watch_pods.py`는 이 접두어 Pod만 세고 지운다.

## 2. 시작 전 체크리스트(각자)

1. 콘솔 Billing: 선불 잔액은 가능하면 한도만큼만 충전(E $5·B $5·C $4.5·D $4.5), 자동 충전 끄기, 잔액 부족 알림을 "시작 잔액 − 한도"로 걸기. RunPod에는 사용자가 거는 지출 상한이 없다(소프트 상한).
2. 노트북에서 감시 시작: `python -m llm.bench.watch_pods --account <E|B|C|D>`. 1번을 확인했으면 질문에 `yes`. 접두어 없는 기존 Pod가 있으면 경고만 나온다(세지도 지우지도 않음).
3. 노트북을 켜 둘 수 없으면 콘솔에서 직접 지울 마감 시각(Pod 시작 + `MAX_MINUTES` + 10분)을 적어 둔다.
4. 키: RunPod 계정 키는 각자 노트북의 `runpodctl config` 설정에만 둔다. Tailscale 인증 키(임시·사전 승인·1일 만료)와 서버 세션 키(`PALDO_LLM_KEY`)는 E가 당일 저장소 밖으로 전달하고, Pod 환경 변수로만 넣는다. 공개 포트는 열지 않는다.

## 3. Pod 안 실행

```
STAGE=s1 MAX_MINUTES=90 EXPORT_CMD='<결과 반출 명령>' bash llm/bench/run_pod.sh <작업 명령>
```

- `MAX_MINUTES`가 단계 상한(s0 20, s1·s2 90)을 넘거나 정수가 아니면 시작하지 않는다.
- 작업이 끝나거나 시간이 다 되면 반출(60초 제한) → `runpodctl pod delete $RUNPOD_POD_ID`(정지가 아니라 삭제, 3회 재시도). 자기 삭제가 실패하면 `watch_pods.py`가 첫 관측 + `MAX_MINUTES` + 10분에 지운다.
- 로그는 `key|token|authkey` 값이 지워진 채 나온다. 환경 변수 값을 직접 출력하는 명령을 작업에 넣지 않는다.
- 부하 측정: `python -m llm.bench.free_answer_load --base-url <사설망 주소> --model <이름> --sessions <N> --think 5 15 --warmup 120 --duration 600 --seed 1 --label <단계_이름> --server-version … --image … --revision … --options …`. 결과는 `out/perf/E-11-12/`에 쓰고 반출한다.
- 중단 조건(즉시 반출·삭제): 15분 안에 서버 준비 응답 없음(1회 재시도 뒤), 1단계 첫 20건 자동 통과율 < 70 % 또는 세션 1개 첫 표시 p95 > 4초, 비용 80 % 도달(`watch_pods.py`가 자동 삭제).

## 4. 미확인, 0단계 확인

코드에 추측으로 넣지 않은 RunPod 기능이다. 0단계에서 확인하고 결과를 E-11-12 증빙에 적는다.

| 항목 | 지금 처리 | 0단계에서 볼 것 |
|---|---|---|
| Pod 범위 키의 자기 삭제 권한 | `run_pod.sh`가 시도하고 실패하면 exit 3, `watch_pods.py`가 독립 삭제 | `runpodctl pod delete $RUNPOD_POD_ID`가 Pod 안에서 되는지 |
| 잔액 조회 CLI·API | 없음. 콘솔 Billing 수동 확인 + 확인 질문 `yes` | 조회 방법이 있으면 기록만(자동화는 하지 않음) |
| 계정 키 환경 변수 이름 | 쓰지 않음(`runpodctl config` 설정 파일) | - |
| 네트워크 볼륨 목록 명령 | 볼륨을 만들지 않고 콘솔 Storage에서 0개 확인 | - |
| `runpodctl` Pod 목록 출력 형식 | 기본 `runpodctl pod list -o json`, JSON 배열의 `id`·`name`·`costPerHr` | 실제 출력이 다르면 `--list-cmd`·`--fields`로 맞추고 이 표를 고친다 |
| 디스크 요금 | 세지 않음(소프트 상한) | 콘솔 Billing의 Pod별 금액과 장부 누계 차이 |

## 5. 종료 후 체크리스트(각자)

1. `runpodctl pod list`에 접두어 `paldo-e1112-` Pod 0개.
2. 콘솔 Storage에서 이 실험이 만든 저장소 0개(무관한 기존 자원은 건드리지 않는다).
3. 콘솔 Billing의 Pod별 사용 시간·요금을 E에게 전달(증빙 표). 장부 파일(`out/runpod/ledger_<계정>.json`)은 Pod ID가 있으므로 저장소에 올리지 않는다.
4. Tailscale 인증 키와 서버 세션 키는 E가 폐기한다.

## 6. 후보 실현 가능성(문서 확인, 11/8)

모델 카드(Hugging Face)로 확인한 값이다. 확인 못 한 값은 "미확인"이고 0단계·1단계 첫 15분에 확인한다. 메모리는 가중치 + KV(요청당 4k 토큰 × 동시 8세션, fp16) 추정이며 실측이 아니다.

| 후보 | 서버 엔진 지원 | 양자화 형식 저장소 | 채팅 템플릿·비추론 | 메모리 추정 |
|---|---|---|---|---|
| Kanana-2-30B-A3B-instruct-2601 (E, 4090 24GB) | vLLM·SGLang 명령이 모델 카드에 있음, 최소 버전 미확인. llama.cpp 지원 미확인 | 공식 양자화 미확인. 커뮤니티 AWQ W4A16(약 16.6GB, 이전 instruct 기준이라 2601과 같은지 미확인) | `apply_chat_template`. instruct는 추론 모드 없음(추론은 별도 thinking 모델) | 가중치 약 16.6GB + KV 미확인(MLA, 구성값 미확인). 4090에 들어갈지는 첫 15분에 확인 |
| Qwen3-30B-A3B-Instruct-2507 (B, 4090 24GB) | vLLM ≥ 0.8.5, SGLang ≥ 0.4.6.post1, llama.cpp 지원(버전 미확인) | 공식 FP8(블록 128, 약 31GB라 4090에 안 들어감). 4비트는 커뮤니티 AWQ·GGUF(저장소 미정) | 비추론 전용(추론 블록 없음) | 4비트 가중치 약 17GB(추정) + KV 48층 × KV 헤드 4 × 헤드 차원 128(헤드 차원은 구성 파일 확인 필요) ≈ 요청당 0.4GB × 8 ≈ 3GB → 약 20GB |
| EXAONE-4.0-32B (C, L40S 48GB) | vLLM ≥ 0.10.0(공식), transformers ≥ 4.54.0. SGLang 공식 지원 문구가 모순, llama.cpp 버전 미확인 | 공식 AWQ W4A16(`EXAONE-4.0-32B-AWQ`). 공식 FP8 저장소 미확인 | `enable_thinking`을 넘기지 않으면 비추론. 비추론 온도 0.6 미만 권장 | AWQ 가중치 약 19GB(추정) + KV 상한(64층 전부 전역으로 볼 때) 요청당 약 1GB × 8 ≈ 8GB → 약 27GB. 지역·전역 3:1이라 실제 KV는 더 작다(창 크기 미확인) |

라이선스 표기: Kanana를 쓰면 E-11-12 증빙·발표에 "Powered by Kanana", EXAONE은 이름·버전과 1.2-NC 표기(11/8 사용자 결정으로 셋 다 2단계 후보).
