# Kanana 생성 서버 운영 절차 (E-10-13)

E-10-13(템플릿 변형 사전 생성)에서 캐묻기 반응·복기 해설 문구 변형 후보를 만드는 Kanana 생성 서버의 구성, 시작·정지·확인 방법이다. 11/10 사용자 결정(README 7절 "확정된 결정 (11/10 사용자, E-10-13 생성 수단 Kanana로 변경)")으로 만들었다. 생성 절차·검사·검수 기준의 원본은 [E-10-13](../../docs/tasks/E_pm.md)이고, 생성·검사 코드 사용법은 모듈 문서 [`llm`](../../docs/modules/llm.md)에 있다. 이 문서는 게임 런타임 LLM 재도입이 아니다(P-31 (나) 유지, 게임은 이 서버를 부르지 않는다). 기기 주소·계정·호스트 이름은 이 문서와 저장소에 적지 않고 저장소 밖 비공개 문서(`4th_private`)에만 둔다.

## 1. 구성

```
노트북(E) ──SSH 터널(로컬 8080 → 원격 127.0.0.1:8080)──▶ 생성 기기(CPU, 노트북과 같은 사양)
                                                         llama-server 127.0.0.1:8080 (OpenAI 호환 /v1/chat/completions)
```

| 항목 | 값 |
|---|---|
| 모델 | Kanana-2-3B Instruct Q4_K_M(`kanana-2-3b-instruct.Q4_K_M.gguf`, E-06-07 선정. sha256은 E-10-13 증빙에 기록) |
| 서버 | llama.cpp `llama-server`, 공식 Windows CPU(x64) 빌드 b12242 |
| 실행 인자 | `--host 127.0.0.1 --port 8080 -t 8 -c 4096` |
| 파일 위치 | 생성 기기의 전용 폴더 `C:\mlo1-pj4-team3\` 아래에만 둔다: `llama.cpp\`(실행 파일), `models\`(gguf), `run_server.cmd`(실행 스크립트), `logs\server.log`(로그) |
| 자동 시작 | 작업 스케줄러 `paldo-llm-server`(기기 시작 때, SYSTEM 계정, 실행 시간 제한 없음)가 `run_server.cmd`를 실행 |
| 접속 | SSH 키 로그인. 노트북 `~/.ssh/config`의 별칭과 기기 주소는 `4th_private` |

## 2. 시작·정지·확인

생성 기기에서 명령 프롬프트(cmd)를 관리자 권한으로 열거나, 노트북에서 `ssh <별칭> "<명령>"`으로 실행한다. cmd에는 한 줄씩 넣는다(여러 줄을 한 번에 붙이면 줄이 합쳐진다).

| 할 일 | 명령 |
|---|---|
| 시작 | `schtasks /run /tn paldo-llm-server` |
| 정지 | `schtasks /end /tn paldo-llm-server` |
| 상태 | `schtasks /query /tn paldo-llm-server` |
| 열린 주소 확인 | `netstat -ano \| findstr :8080` → `127.0.0.1:8080 LISTENING`만 있어야 한다(`0.0.0.0`이면 즉시 정지하고 실행 인자를 고친다) |
| 로그 | `C:\mlo1-pj4-team3\logs\server.log` |

## 3. 노트북에서 쓰기

1. 터널 열기(창 하나를 띄워 둔다): `ssh -N -L 8080:127.0.0.1:8080 <별칭>`. 노트북 8080 포트가 이미 쓰이고 있으면 열리지 않으니 먼저 비운다.
2. 확인: `curl http://127.0.0.1:8080/health` → `{"status":"ok"}`.
3. 생성: E-10-13 생성 명령(`llm/` 안)이 `http://127.0.0.1:8080/v1/chat/completions`를 부른다. 결과는 E-07-07 검증기와 `check_templates`로 걸러 통과분만 후보로 남긴다.
4. 끝나면 터널 창을 닫는다. 상시 터널은 두지 않는다.

## 4. 측정값 (11/10, 터널 경유 1회)

| 요청 | 시간 | 생성 속도 |
|---|---|---|
| 짧은 답 1건 | 1.11 s | 17.8 tok/s(프롬프트 처리 106 tok/s) |
| 세 문장 묘사 요청 1건 | 2.34 s(36토큰) | 17.9 tok/s |

오프라인 사전 생성에는 충분한 속도다. 같은 시험에서 주민 역할을 거부하는 답과 문장 수 지시를 지키지 않는 답이 나왔다. 프롬프트 조정과 탈락 사유 기록은 E-10-13 2번에서 한다.

## 5. 라이선스·보안 규칙

- 엔드포인트를 외부에 열지 않는다(Kanana 라이선스 4.1·4.2, `docs/reports/llm_bench.md` 1절). 서버는 127.0.0.1에만 열고, 방화벽에 8080 규칙을 만들지 않는다.
- 모델 파일을 기기 밖으로 넘기지 않는다. 넘기면 3.1 재배포 의무(라이선스 사본·NOTICE·사용 제한 고지)가 생긴다.
- 생성 문장이 게임에 들어가므로 게임 화면에 "Powered by Kanana"를 표시한다(E-10-13 5번).
- 프로젝트 파일은 전용 폴더 밖에 두지 않는다.

## 6. 정리(기기 반납 전)

설정을 되돌리는 순서(노트북 키·접속 설정, 작업 스케줄러, 전용 폴더, OpenSSH 서버 등)와 항목별 상태는 저장소 밖 비공개 문서(`4th_private`의 `LLM_RESET.md`)에 있다. 모델 파일이 기기에 남지 않도록 반납 전에 작업 정지 → 작업 삭제 → 전용 폴더 삭제를 반드시 한다.

## 7. 복기 해설 실시간 생성에서 쓰기 (E-10-01)

11/10 사용자 결정("복기 해설 실시간 생성")으로 게임 서버가 판 종료 뒤 복기 해설 요약을 이 서버로 만든다. 판 진행 중에는 부르지 않는다.

| 항목 | 값 |
|---|---|
| 켜기 | 게임 서버 환경 변수 `PALDO_REVIEW_LLM_URL=http://127.0.0.1:<로컬 포트>`(게임 서버에서 생성 기기로 가는 터널의 로컬 끝). 비우면 꺼짐(작업 미등록·템플릿 해설만·Kanana 표기 숨김) |
| 마감 | `PALDO_REVIEW_DEADLINE_S`(기본 180초). 지나면 템플릿으로 확정 |
| 작업 프로세스 | `python server/manage.py review_worker`(동시 1건, FIFO, 웹과 같은 `PALDO_GAME_SERVICE_FACTORY`·Mongo 설정). 운영은 서비스 1개로 상시 실행 |
| 요청 | 판 하나에 NPC마다 `/v1/chat/completions` 1회(JSON 스키마로 NPC·인용 순서 고정, 온도 0.3, top_p 0.9, 최대 900토큰, 시드 = 판 시드). 요청 제한 min(90초, 마감까지 남은 시간), 연결 오류는 5초 뒤 1회 재시도 |
| 저장 | Mongo `<접두어>review_jobs`(판당 문서 1개). 판 삭제(`privacy_game_cleanup`)가 같이 지운다 |
| 확인 | 복기 화면에서 "족제비가 해설을 다듬고 있어요…" 뒤 "마을 사람들의 속마음" 문장이 보이면 정상. 서버가 꺼져 있으면 묶음이 사라지고 템플릿 해설만 남는다 |
| 실측 | `python server/manage.py review_narration_perf --url http://127.0.0.1:<포트> --games 12 --burst 4 --samples <저장소 밖 경로>` → `docs/evidence/perf/E-10-01/review_narration.json`(집계만) |

게임 서버(app-01) → 생성 기기 상시 터널(포워딩 전용 계정, 설계 D2)은 11/11 적용했다(app-01 `127.0.0.1:18080` → 생성 기기 `127.0.0.1:8080`, 터널 서비스 자동 재접속, app-01에서 health 200 확인). 생성 서버는 11/11부터 슬롯 2개·슬롯당 문맥 4096(`-np 2 -c 8192`)이고 자동 재시작된다. 계정·주소·서비스 설정은 `4th_private`.

## 8. NPC 대사 LLM에서 쓰기 (E-11-13)

11/11 사용자 결정("NPC 대사 LLM 서빙")으로 판 진행 중 회의 발언 다듬기(polish)와 캐묻기 답 실시간 생성(live)에 이 서버를 쓴다. 코드 기본은 꺼짐이다. **운영 값은 polish 켬, live 끔**(11/11 사용자 승인, 표본 30개 승인 완료). live는 운영진이 시연 등 필요할 때만 켜고 끝나면 `npc_llm_mode polish`로 되돌린다.

| 항목 | 값 |
|---|---|
| 상한 스위치 | 게임 서버 환경 변수 `PALDO_NPC_LLM_URL=http://127.0.0.1:<로컬 포트>`(복기와 같은 터널 끝). 비우면 모드와 관계없이 꺼짐(템플릿만·Kanana 표기 숨김) |
| 모드 | `off`·`polish`·`live`(live는 polish 포함). Mongo `<접두어>ops_settings`의 `npc_llm` 문서가 정하고, 없으면 `PALDO_NPC_LLM_MODE`(코드 기본 off). 웹은 10초 안에, 작업 프로세스는 다음 작업부터 반영 |
| 모드 바꾸기 | `python server/manage.py npc_llm_mode {off\|polish\|live\|show} [--live-deadline-ms N] [--polish-queue-max N]`. 재배포·재시작 없음. 급하면 `off`. live는 운영진이 이 명령으로만 켠다 |
| 작업 프로세스 | `python server/manage.py npc_line_worker`(한 번에 한 줄, 실시간 > 다듬기). `review_worker`와 따로 1개 상시 실행해 서버 슬롯 2개를 하나씩 쓴다 |
| 요청 | 줄마다 `/v1/chat/completions` 1회. 다듬기 최대 64토큰·요청 제한 10초, 실시간은 마감(기본 3000ms, 11/11 실측으로 확정)까지. 프롬프트 캐시(`cache_prompt`) 켬 |
| 저장 | Mongo `<접두어>npc_line_jobs`(줄당 문서 1개). 판 삭제(`privacy_game_cleanup`)가 같이 지운다. 본문은 이 문서에만 있고 로그·이벤트에 없다 |
| 확인 | `npc_llm_mode show`, 작업 프로세스 로그 `npc_line_worker`의 `status`(done·rejected·failed·closed). 회의 대사창에서 NPC 발언 앞에 말투 감탄사가 붙으면 다듬기가 도는 것이다 |
| 지표 | `PALDO_LLM_METRIC_FILE`(예 `<수집 폴더>/paldo_llm_{worker}.prom`)을 두 작업 프로세스에 주면 작업마다·대기 중 60초마다 `paldo_llm_*` 지표 파일을 쓴다(E-12-01). 결과 대응: NPC done→ok, discarded·failed(timeout)→late, rejected→rejected, failed(그 밖)→error, closed(모드 off)는 세지 않음. 복기 done 저장→ok, done 저장 못 함·timeout→late, failed→error. `paldo_llm_lines_total{source}`는 화면에 나간 출처(NPC 대사는 줄: ok만 llm, late·rejected·error는 template / 복기는 저장된 해설의 NPC별 LLM·템플릿 수)라 문장 단위 대체 비율을 본다. 작업 꺼내기·처리 중 예외(저장소 장애 포함)는 작업 건수가 아닌 `paldo_llm_worker_loop_errors_total`로 센다. 수집 폴더는 C-12-03 |
| 켜기 순서 | 배포(코드 기본 off) → `npc_line_worker` 상시 실행 → 웹·작업 프로세스에 `PALDO_NPC_LLM_URL` 설정 → `npc_llm_mode polish`(표본 30개는 11/11 승인됨) → `npc_llm_mode show`로 polish 확인 → 운영 비밀 파일 `PALDO_NPC_LLM_MODE=polish`(설정 문서가 없어져도 polish). live는 켜지 않는다 |

