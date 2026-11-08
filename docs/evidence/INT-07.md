# INT-07 통합 1 (11/7)
- 상태: 부분 판정, 미통과 항목 담당 배정. 9개 항목 중 통과 0, 엔진 몫·자동 테스트 몫만 확인했다. #5·#7·#8은 선행 작업 미완으로 미통과로 기록하고 담당·기한을 붙였다(11/7 사용자 결정). #1·#3·#4·#6의 실제 웹·Mongo 경로는 운영 게임 서비스 조립이 없어 재현하지 못했다. 공통 완료 정의의 완료를 뜻하지 않는다.
- 11/8 자동 판정(E-07-03, 11/7 사용자 승인): 실제 엔진·HTTP 게임 웹(daphne)·Mongo 레플리카셋·운영 조립으로 #1·#3·#4·#6을 자동 재현하고 #2 시나리오 테스트를 돌렸다. 자동 확인 통과 #1·#6, 자동 확인 완료·사람 브라우저 확인 대기 #2·#3·#4, 미통과 유지 #5·#7·#8·#9. 최종 판정은 PM이 한다(아래 "11/8 자동 판정"·"사람 브라우저 확인").
- 11/8 PM 판정(사람 브라우저 확인 반영): 통과 #1·#3·#4·#6, 미통과 #2(웹 재현 5개 중 2개, 나머지 3개는 통합 2에서 재확인), 미통과 유지 #5·#7·#8·#9. 9개 항목 중 통과 4.
- 11/8 재판정(C-07-02 #251·C-06-03 #249·C-07-01 #250·C-07-04 #252 병합 뒤, 증빙 기준, PM 결정): #5 통과, #8 통과. 통과 #1·#3·#4·#5·#6·#8, 미통과 #2·#7·#9. 9개 중 통과 6. 실제 스택 재실행은 하지 않았고 #9 동시 기동 확인 때 함께 본다.
- 11/8 재판정 2(D-07-02 #269 병합 뒤, dev c15cbab, 증빙 기준, PM 결정): #7 통과. 통과 #1·#3·#4·#5·#6·#7·#8, 미통과 #2·#9. 9개 중 통과 7. #2는 사람 웹 재현 3개, #9는 통합 환경 동시 기동이 남아 새 증빙이 없다.
- 11/8 통합 환경 동시 기동·웹 재현(로컬 Compose, 아래 "11/8 통합 환경 동시 기동·웹 재현" 절): #9 확인 완료(Python 구성요소 6개 지표 누락 0, 3.14 아닌 프로세스 0, 컨테이너 3개 정상), #1 통합 환경 1판 확인 완료(명령 6개, 500·503 0건), #2 남은 반례 3개 자동 웹 재현 3/3(사람 브라우저 확인 대기). 통과 여부는 PM 판정 대기이고 위 재판정 2의 통과 수(7/9)는 바꾸지 않았다.
- 커밋: edae77caaa794eb2009bd6d66e5d1d8eb432b0c6
- 작성자(흐름): E
- 환경: 로컬(노트북 Windows 11, Python 3.14.6). 통합 환경(Compose의 웹·Mongo·Kafka·광고 서버 동시 기동)은 11/8 로컬 Compose로 구성해 확인했다(아래 "11/8 통합 환경 동시 기동·웹 재현"). Mongo 실제 저장소 시험은 C-06-01 증빙의 VM 레플리카셋 결과를 근거로 삼는다(접속 정보는 저장소 밖).
- 판정 주관: E-07-03(PM). 판정 기준은 `docs/tasks/E_pm.md`의 INT-07 표다.
- 작성일: 2026-11-07

## 명령

```powershell
# 엔진 몫 시나리오 테스트: dev(edae77c) 코드에 미병합 브랜치 task/A-07-03(f741d36)의 tests/engine/test_int07.py만 얹어 저장소 밖 임시 폴더에서 실행
git archive origin/dev | tar -x -C <임시 폴더>
git show task/A-07-03:tests/engine/test_int07.py > <임시 폴더>/tests/engine/test_int07.py
python -B -m unittest tests.engine.test_int07 tests.engine.test_projection

# Mongo·발행기 테스트(로컬에는 테스트용 Mongo 주소가 없어 건너뜀, 실제 결과는 C-06-01·C-06-02 증빙)
python -B -m unittest tests.infra.test_mongo_repo tests.infra.test_outbox_publisher

# 선행 태스크 PR 확인
gh pr list --state merged --search "[<ID>] in:title"
```

11/8 자동 판정(dev a69b75a, 이 작업 트리). Mongo는 로컬 임시 단일 노드 레플리카셋(로컬 mongod 8.3, 빈 DB에서 시작, 실행 뒤 삭제). 연결 문자열 형식은 `mongodb://<호스트:포트>/paldo_web?replicaSet=<이름>`이다.

```bash
# #1·#3·#4·#6 웹 자동 재현: 설정 생성 → 계정 DB 이주·참가자·로그인 세션 → 콘텐츠 적재 → 고정 회차 등록 → daphne → HTTP 3판
PALDO_MONGO_URI=<레플리카셋 URI> python -m tools.perf.int07_web_run --out <임시 폴더> --record < /dev/null

# #2 반례 시나리오(test_int07은 #239로 dev에 병합) + 투영 검사
python -m unittest -v tests.engine.test_int07 tests.engine.test_projection < /dev/null

# 판정 규칙 단위 시험
python -m unittest tests.tools.test_int07_web_run < /dev/null

# #2 남은 반례 3개의 시드·버튼 순서 찾기(엔진 직접, 웹 운영 조립과 같은 구성) + 결정성 시험
python -m tools.perf.int07_scenarios --out out/int07_scenarios.json < /dev/null
python -m unittest tests.tools.test_int07_scenarios < /dev/null

# #9 통합 환경 동시 기동 + 그 위 #1 1판·#2 웹 재현(로컬 Compose Mongo·Kafka·MySQL이 떠 있고 Kafka에 TLS 리스너를 더한 상태)
python -m server.tests.int07_stack --scenarios out/int07_scenarios.json --kafka-config <발행기 TLS JSON> --out <임시 폴더> --project paldo-int07 --record < /dev/null
```

## 출력

```text
tests.engine.test_int07 + tests.engine.test_projection (dev edae77c + A-07-03 테스트 파일)
Ran 29 tests in 87.230s
OK
[A-06-02] games=200 observations=9276 decisions=9276 truth_reads_during_decide=0 truth_reads_outside=18001

tests.infra.test_mongo_repo + tests.infra.test_outbox_publisher (로컬)
Ran 9 tests, OK (skipped=10, 테스트용 Mongo 주소 없음)
```

11/8 자동 판정 출력:

```text
python -m tools.perf.int07_web_run --record (빈 DB, 19.1초)
content_version_inserted content-1.0
#1 자동 확인 통과: 완주 3/3, 명령 [6, 6, 13], 500·503 0건, 판 오류 0건, 최종 화면 결과 일치 True, 진척 기록 True
#3 자동 확인 완료, 사람 브라우저 확인 대기: 아침 화면 확인 4회, 최대 공개 밤 1, 빠진 문장 0
#4 자동 확인 완료, 사람 브라우저 확인 대기: 재전송 재생 True·같은 화면 True, 오래된 버전 409, 서버 재시작 뒤 같은 화면 True·완주 True
#6 자동 확인 통과: 응답 86개(JSON 58, HTML 28) 금지 키 0건

tests.engine.test_int07 + tests.engine.test_projection (dev a69b75a)
Ran 29 tests in 27.885s
OK
[A-06-02] games=200 observations=9276 decisions=9276 truth_reads_during_decide=0 truth_reads_outside=17931

tests.tools.test_int07_web_run
Ran 15 tests, OK
```

선행 태스크 PR 확인 결과(11/7 기준, 병합): C-06-01 #225(Mongo 저장소·실제 레플리카셋 검증), C-06-02 #226(토픽 10개·아웃박스 발행·소비 중복 제거), C-06-03 #228(광고·계정 DB 분리, 부분 완료), C-06-04 #229, C-07-01 #223(Kafka→Delta bronze, 부분 구현), D-05-03 #222(광고 서버 API, 부분 구현), D-06-01 #206, A-07-01 #210, A-07-02 #212, B-07-01 #217·#220, B-07-02 #218, B-07-03 #221, B-07-05 #216. PR 없음: C-07-02, C-07-04, D-07-02, A-07-03(엔진 몫 테스트는 브랜치에만 있음).

C 흐름 보고(11/7): Mongo·아웃박스는 구현과 실제 검증을 마치고 병합했다. 광고 토픽·DB·계정은 만들었고 광고 서버 연결과 DB 복제가 남았다. Spark 전체 적재·학습 연결, Kafka 장애 무손실 시험, HTTPS는 완료되지 않았다.

C 흐름 진행 구분(11/7 갱신, dev d10ac85 기준). 아래 "완료"는 그 태스크의 구현·단위·실저장소 검증 완료를 뜻하며, INT-07 항목(게임 웹 전체 경로) 통과를 뜻하지 않는다. INT-07 판정은 PM이 한다(11/7 결정, #240).

| C 작업 | 구분 | 근거 | INT-07에서 남은 것 |
|---|---|---|---|
| C-06-01 Mongo 게임·진척·콘텐츠 저장소 | 완료 | #225 병합, 이슈 #49 닫힘. 실제 레플리카셋 `tests.infra.test_mongo_repo` 9개 통과, 동시 commit 20개 중 성공 1건(C-06-01 증빙) | #1·#4: 게임 웹에서 저장·재개 재현(사람 확인) |
| C-06-02 아웃박스 발행·공식 토픽·소비 중복 제거 | 완료 | #226 병합, 이슈 #50 닫힘. `tests.infra.test_outbox_publisher` 6개(재시도·ack 후 표시·만료 lease 거부) 통과, 공식 토픽 10개(게임 6·광고 4) 생성·설정 확인, 실제 Kafka 수신 11건(원본 10 + 고의 중복 1) → 고유 10건, 미발행 `paldo_outbox_pending_events` 0(C-06-02 증빙) | #5: Kafka 장애 무손실과 대조 스크립트(C-07-02) |
| 콘텐츠 적재 컬렉션 이름 통일 | 부분 완료 | `content_bundles` → `content`는 #239(A-07-03)로 병합. C 검증 DB는 실행별 고유 테스트 컬렉션만 사용 | 병합 뒤 운영 Mongo 재적재 필요, 완료 여부 C 확인 대기 |
| C-06-03 광고·계정 DB 분리 | 완료(11/8 갱신) | #228·#249 병합, 이슈 #51 닫힘. pj4 복제 채널 복구, 광고·계정 웹 TLS 실배포, 광고 serve·노출·클릭 → MySQL Outbox → Kafka → bronze | 없음(#8 재판정 통과) |
| C-07-01 Kafka → Delta | 완료(11/8 갱신) | #223·#250 병합, 이슈 #53 닫힘. 200판 17,823건 bronze 누락 0·중복 0, silver 19,191행, 라벨 200판, 학습 행 14,460 | 없음(#7 재판정 통과, D-07-02 #269) |
| C-07-02 Kafka 장애 무손실 시험·대조 스크립트 | 완료(11/8 갱신) | #251 병합, 이슈 #54 닫힘. 정지·재개 중 104건 누락 0·중복 0·해시 불일치 0 | 없음(#5 재판정 통과) |
| C-07-04 HTTPS 프록시 | 완료(11/8 갱신) | #252 병합, 이슈 #55 닫힘. HTTPS·wss 동작, mixed content 0 | INT 항목 아님 |

게임 웹의 운영 게임 서비스는 `PALDO_GAME_SERVICE_FACTORY`로 조립한다(`server/paldo_web/composition.py`, 기본값 비어 있음). C-06-01 증빙은 "B 조립에서 `MongoGameRepository`·`MongoProgressRepository`·`MongoContentSource`를 주입"한다고 적었지만, 이 값을 채우는 조립 함수는 dev에 아직 없다. 그래서 실제 엔진·웹·Mongo로 판을 진행하는 웹 재현(#1·#3·#4·#6의 웹 부분)은 여전히 할 수 없다. (11/7 갱신: 조립 함수 `server.infra.game_factory.build_game_service`가 A-07-03 #239로 병합됐다. 웹 재현은 사람 확인과 운영 Mongo 콘텐츠 재적재 뒤 PM이 다시 판정한다.)

## 11/8 자동 판정

"자동 확인 통과"는 자동 확인이 합격선 전체를 채운 항목, "자동 확인 완료, 사람 브라우저 확인 대기"는 합격선에 웹 재현(사람 확인)이 들어 있어 자동 몫만 끝난 항목이다. 둘 다 PM 최종 판정 전이었고, PM 판정은 아래 "사람 브라우저 확인 결과 (11/8)"와 "수치" 표 통과 칸에 반영했다.

실행 구성: 운영 조립 `PALDO_GAME_SERVICE_FACTORY=server.infra.game_factory.build_game_service`(실제 엔진 `PaldoEngine(RuleV0Policy)`, 의심 모델은 운영 조립과 같은 `FakeSuspicionModel`, 템플릿 렌더러), 게임·진척·콘텐츠·회차·보조 기록은 Mongo 레플리카셋, 요청은 실제 HTTP(daphne, 로그인 세션 쿠키·CSRF 토큰). 계정 DB는 MySQL이 아니라 실행용 SQLite 파일이다(로컬에 MySQL 없음). 고정 회차는 A-07-03 서버 시험과 같은 base6 시드 70301·70302·70303, 규칙 버전 `rule-0.5.decision-1`, 콘텐츠 `content-1.0`. 명령은 화면 정보의 합법 명령(버튼으로 낼 수 있는 것) 중에서만 고른다(`bench_player_command`).

| 항목 | 합격선 | 자동 결과 | 수치(결과 파일) | 남은 것 |
|---|---|---|---|---|
| 1 | 3판 완주, 오류 0 | 자동 확인 통과 | 6인·창귀 1 3판 완주(승리 창귀 3), 명령 6·6·13개, 추궁 답변 렌더 23건(템플릿 폴백 즉시 종료), 500·503 0건, 최종 화면 결과 = 마지막 명령 응답 결과, 진척에 3판 기록 | 통합 환경(MySQL 계정 DB·운영 Mongo)에서 같은 명령 1회는 #9 동시 기동 때 |
| 2 | 시나리오 테스트 + 웹 1회씩 재현 | 자동 확인 완료, 사람 브라우저 확인 대기 | `tests.engine.test_int07` 반례 3개 시험(결선 재동점 무감금·무당 감금·마지막 씐 자 감금, 점괘·고백 전환, 거짓 점괘 공개)과 `test_projection` 포함 29개 통과. 웹 재현은 자동화하지 않음(특정 투표·점괘 상황을 무작위 합법 명령으로 만들 수 없음) | 사람의 웹 1회씩 재현 |
| 3 | 0일째·1일째 모두 | 자동 확인 완료, 사람 브라우저 확인 대기 | 날이 바뀔 때마다 회의 화면 HTML 확인 4회, 0일째 밤("1일째 아침: …에서 발견됐다")은 3판 모두, 1일째 밤("2일째 아침: …")은 3판째에서 확인(밤마다 같은 줄 안의 피해 장소 문장 검사), 빠짐 0 | 사람이 브라우저 화면에서 문장이 보이는지 확인 |
| 4 | 테스트 + 웹에서 재현 | 자동 확인 완료, 사람 브라우저 확인 대기 | 같은 `command_id` 재전송 → `replayed: true`·같은 화면, 같은 버전 새 명령 → 409. 2판째 3명령 뒤 게임 웹 프로세스 종료·재시작 → 같은 화면(`state_version` 포함 전체 일치) → 이어서 완주 | 사람이 브라우저에서 두 번 누르기·새로고침·재시작 뒤 이어 하기 |
| 6 | 판 3개 전체 HTML·JSON 금지 키 0 | 자동 확인 통과 | 실행 중 받은 응답 86개(JSON 58: 판 시작·명령·재생·409·화면·렌더·진척, HTML 28: 판 시작 3 + 명령마다 게임 화면 25, 결과 화면 포함) 전부에서 `claim_truths`·`npc_states`·`beliefs`·`private`·`night_plans` 0건. JSON은 키, HTML은 본문 전체 검사 | 없음(브라우저가 따로 받는 정적 파일은 판 데이터가 아니라 검사 대상에서 뺐다) |

결과 파일: `docs/evidence/perf/INT-07/web_run.json`(수치), `docs/evidence/perf/INT-07/web_run_detail.json`(판별 명령 수·렌더·공개된 밤·재생·재시작 기록). 실패 항목은 없어 원인·담당 기록은 없다.

## 사람 브라우저 확인 (30~40분)

통합 자리에서 사람 1명이 한다. 운영 조립 로컬 실행 순서는 `docs/modules/web.md` 사용법을 따르고, 계정은 동의를 마친 참가자 계정 하나를 쓴다. 결과는 PM에게 항목 번호와 예/아니오, 아니오면 화면 캡처로 알린다.

| 순서 | 항목 | 할 일 | 시간 |
|---|---|---|---|
| 1 | 준비 | 콘텐츠 적재(`python -m tools.load_content content/bundle_v1.json`), `/admin/sessions`에서 규칙 버전 `rule-0.5.decision-1`로 base6 회차 등록, 참가자로 로그인 | 5분 |
| 2 | #1 | 브라우저에서 버튼만 눌러 한 판을 결과 화면까지 진행. 오류 화면·멈춤이 없는지 | 10분 |
| 3 | #3 | 1일째 아침 화면에 "1일째 아침: …에서 발견됐다", 2일째까지 가면 "2일째 아침:" 줄도 같이 보이는지 | 3분 |
| 4 | #4 | 버튼 빠르게 두 번 누르기 → 한 번만 반영. 탭 두 개에서 같은 판을 열고 한쪽에서 진행한 뒤 다른 쪽에서 누르기 → 두 번 반영되지 않고 새로고침하면 최신 화면. 게임 웹 재시작 뒤 새로고침 → 같은 화면에서 이어 하기 | 7분 |
| 5 | #2 | 판을 진행하며 반례 상황(결선 재동점 무감금, 무당 감금, 점괘·고백 전환, 마지막 씐 자 감금, 거짓 점괘 공개)이 나오면 화면이 규칙대로인지 1회씩 확인. 나오지 않은 상황은 "미재현"으로 적는다 | 10분 |
| 6 | #6 | 개발자 도구 네트워크 탭과 페이지 소스에서 금지 키 5개를 검색해 0건인지 | 5분 |

## 사람 브라우저 확인 결과 (11/8)

PM이 위 확인표대로 직접 진행했다. 환경은 "11/8 자동 판정"과 같은 운영 조립 로컬 실행(게임 웹 daphne, Mongo 로컬 단일 노드 레플리카셋, 계정 DB 실행용 SQLite)이고, 운영자 화면에서 base6 시드 70301·70302·70303 회차(규칙 `rule-0.5.decision-1`, 콘텐츠 `content-1.0`)를 등록해 참가자 계정 하나로 했다. 판별은 PM의 화면 확인이고, 게임 웹 접근 로그와 Mongo 판 기록으로 시각·상태 버전을 맞춰 보았다. 로그·계정 DB는 저장소 밖 임시 폴더에 두었다가 지웠다.

| 항목 | 결과 | 확인 내용 |
|---|---|---|
| #1 | 예 | 1판째를 버튼만으로 결과 화면·설문까지 진행(명령 13개, 2일째 무당 감금으로 창귀 승리). 500·503 0건, 멈춤 없음. 입력 거절 422가 1건 있었다(1일째 심문, 3초 뒤 넘어가기로 진행). 422는 엔진이 명령을 거절할 때의 정해진 응답이고 화면은 "행동을 적용하지 못했습니다" 안내와 현재 화면을 다시 보여 준다(`web/templates/meeting.html`). 심문 양식은 엔진이 거절하는 조합을 고를 수 있다("왜 말이 다른가"에 관련 진술 2개 미만 → `missing_slot`, 밤 칸에 오늘 이후 숫자 → `illegal_target`). 거절 코드는 서버 로그에 남지 않아 이번 건이 어느 경우인지는 확정하지 못했다. 오류 화면·멈춤이 아니라 입력 거절이므로 #1의 오류로 세지 않았다(PM 판정, 아래 후속) |
| #3 | 예 | 1일째 아침 "1일째 아침: …에서 발견됐다", 2일째 아침 "2일째 아침:" 줄이 화면에 보임 |
| #4 | 예 | 두 번 누르기 → 한 번만 반영. 탭 두 개에서 한쪽 진행 뒤 다른 쪽 누르기 → 409, 새로고침하면 최신 화면. 2판째 1명령(상태 버전 1) 뒤 게임 웹 프로세스 종료·재시작 → 새로고침 같은 화면 → 다음 명령 수락(상태 버전 2) |
| #6 | 예 | 네트워크 탭에서 금지 키 5개 검색, 응답 본문 0건(`private`은 `Cache-Control: private, no-store` 머리글에서만 나옴) |
| #2 | 아니오 | 웹 재현 결선 재동점 무감금 예, 무당 감금 예. 점괘·고백 전환, 마지막 씐 자 감금, 거짓 점괘 공개는 판 진행 중 조건이 나오지 않아 미재현. 시나리오 테스트는 5개 모두 통과. PM 결정으로 웹 5개 재현을 채울 때까지 미통과로 두고 통합 2(11/9)에서 재확인 |

후속(담당 B, 11/8 PM 결정으로 B-08-02 완료 조건에 넣음, 11/9 정식 테스트 전):

- 게임 웹 접근 로그(`server/paldo_web/middleware.py`)에 명령 거절 코드(`missing_slot` 등)를 한 칸 더해 422 원인을 서버에서 추적할 수 있게 한다. 요청 본문은 남기지 않는다.
- 심문 양식(`web/templates/meeting_actions.html`)이 엔진이 거절할 조합(모순 질문의 진술 2개 미만, 지난 밤이 아닌 밤 번호)을 미리 막거나 안내하게 한다.

## 11/8 통합 환경 동시 기동·웹 재현 (로컬 Compose)

PM 판정용 자동 확인이다(E-07-03, 11/8 사용자 지시 "INT-07은 할 수 있는 건 해 봐"). 통과 여부는 PM이 정한다.

구성(기준 문서 "Compose의 웹·Mongo·Kafka·광고 서버 동시 기동" 그대로):

- 컨테이너: `ops/compose/dev.yml`의 `mongo`(레플리카셋 `rs0`)·`kafka`·`mysql`(광고 DB `paldo_ads`·계정 저장소 `paldo_auth`). Compose 프로젝트 이름은 `paldo-int07`로 따로 띄웠다. 이 노트북의 `paldo-dev` 볼륨은 지금 없는 옛 `.env` 계정으로 만들어져 새 `.env`와 맞지 않고, 볼륨 삭제(`down -v`)는 PM 허락 사항이라 건드리지 않았다. Garage·Prometheus·Grafana는 이 확인 대상이 아니라 띄우지 않았다.
- Python 3.14 프로세스 6개(노트북 Python 3.14.6): 게임 웹 daphne(운영 조립 `build_game_service`, 계정 DB = Compose MySQL), 게임 아웃박스 발행기(`server.infra.outbox_publisher`), 광고 서버(`ads/manage.py runserver`), 광고 작업자 expire·publish·refund.
- Kafka TLS: 게임 아웃박스 발행기는 검증된 TLS만 허용한다(`verified_tls_required`). 그래서 실행 때만 Compose 덮어쓰기 파일로 Kafka에 TLS 리스너(`localhost:9094`)를 더했다. 저장소 밖 임시 폴더에서 `openssl`로 1회용 CA와 브로커 인증서(SAN `localhost`·`kafka`)를 만들고, 덮어쓰기 파일에 `KAFKA_LISTENERS`·`KAFKA_ADVERTISED_LISTENERS`·`KAFKA_LISTENER_SECURITY_PROTOCOL_MAP`에 `TLS` 리스너를 더한 뒤 `KAFKA_SSL_KEYSTORE_TYPE=PEM`·`KAFKA_SSL_KEYSTORE_LOCATION`(키+인증서 PEM)·`KAFKA_SSL_CLIENT_AUTH=none`을 두었다. 발행기 TLS JSON은 `security.protocol=SSL`·`ssl.ca.location`(그 CA)·`broker.address.family=v4`다. 저장소의 `dev.yml`은 바꾸지 않았다. 광고 작업자는 평문 리스너(`PALDO_KAFKA_BOOTSTRAP`)를 쓴다.
- 토픽: 로컬 단일 브로커라 토픽 계획(`pipeline/topics.yaml`) 10개를 복제 1로 만들었다(운영 브로커는 복제 3으로 미리 생성).
- 판: 판마다 참가자 1명과 고정 회차(시드 하나)를 `server.tests.int07_setup`으로 만들고 게임 웹 HTTP로만 진행했다. 로컬 Compose라 공식 데이터(Garage `pj4/official/...`)와 섞이지 않는다. 도구는 Mongo·Kafka·광고 DB·계정 DB·발행기 TLS 주소가 하나라도 이 노트북(`localhost`·`127.0.0.1`)이 아니면 아무것도 쓰기 전에 멈추고, 선택한 Compose 프로젝트가 연 포트와 그 주소(계정 DB·발행기 TLS 포함)가 다르면 이주·적재 전에 멈춘다.

결과(브랜치 커밋 539d17a, `docs/evidence/perf/INT-07/stack_run.json`·`stack_run_detail.json`, 수집 시각 2026-11-08 15:11 UTC):

| 구성요소 | `/metrics` 필수 지표 | 프로세스 Python |
|---|---|---|
| 게임 웹(daphne) | `paldo_repo_commit_duration_seconds` 있음(Mongo 저장소, 판 진행 뒤) | 3.14.6 |
| 게임 아웃박스 발행기 | `paldo_outbox_publish_lag_seconds` 있음 | 3.14.6 |
| 광고 서버 | `paldo_ad_serve_total` 있음(serve 1건 뒤) | 3.14.6 |
| 광고 작업자 expire·publish·refund | `paldo_ad_worker_processed_total` 각각 있음 | 3.14.6 ×3 |
| 컨테이너 mongo·kafka·mysql | healthy 3/3, 호스트 포트 = 구성요소 연결 주소 | (Python 아님) |

- 같은 실행 동안 Kafka 끝 오프셋이 264건 늘었다(`paldo.game` 96, `paldo.observations` 162, `paldo.truth` 2, `paldo.llm` 3, `ad.serve` 1). 게임 웹 → Mongo 아웃박스 → 발행기(TLS) → Kafka, 광고 serve → MySQL Outbox → 광고 publish 작업자 → Kafka가 동시에 돈 것이다.
- #1(통합 환경에서 같은 명령 1회): 계정 DB를 Compose MySQL로 바꾼 운영 조립에서 버튼 명령 1판(시드 70301, `int07_web_run`과 같은 명령 선택)을 명령 6개로 결과(창귀 승리)까지 진행했다. 500·503 0건, 받은 응답 전체 금지 키 0건.
- #2 남은 반례 3개 자동 웹 재현: `tools.perf.int07_scenarios`로 시드와 버튼 순서를 찾고(엔진은 같은 시드·같은 명령이면 같은 판), 그 순서를 게임 웹 명령 API로 그대로 보내 화면 정보로 확인했다. 3/3 재현, 명령 거절 0건, 게임 화면 HTML 200.

| 반례 | 시드 | 웹 화면 정보로 확인한 것 |
|---|---|---|
| 거짓 점괘 공개 | 2 | 1일째 밤 과부 점괘 = 탁함(무당 개인 기록), 2일째 공개 기록 = 맑음 |
| 마지막 씐 자 감금 | 10 | 1일째 투표로 훈장(씐 자) 감금 → 결과 마을 승리, 이유 `all_possessed_locked` |
| 점괘·고백 전환 | 6 | 1일째 밤 머슴 점괘 = 탁함 → 2일째 캐묻기에서 머슴 고백 → 2일째 밤 머슴 점괘 = 맑음 |

사람 브라우저 확인 순서(규칙 `rule-0.5.decision-1`·콘텐츠 `content-1.0`, `/admin/sessions`에 base6 해당 시드 회차를 등록하고 참가자로 시작한다. 판마다 새 참가자 또는 새 회차. "넘어가기"는 아침·심문 단계의 진행 버튼, "침묵"은 말하기 단계의 침묵):

- 시드 2(거짓 점괘 공개): 넘어가기 → 침묵 → 넘어가기 → 침묵 → 머슴에게 투표 → 밤에 과부 점치기 → 넘어가기 → 점괘 공개에서 "1일째 밤 과부 = 맑음" 선택. 공개 기록에 맑음, 무당 수첩(개인 점괘)에는 탁함이 보이면 된다.
- 시드 10(마지막 씐 자 감금): 넘어가기 → 훈장 의심 → 넘어가기 → 훈장 의심 → 훈장에게 투표. 훈장 감금과 마을 승리 결과 화면이 보이면 된다.
- 시드 6(점괘·고백 전환): 넘어가기 → 머슴 의심 → 넘어가기 → 침묵 → 대장장이에게 투표 → 밤에 머슴 점치기(탁함) → 넘어가기 → 침묵 → 머슴에게 행적 캐묻기(0일째 밤 초저녁) → 머슴에게 행적 캐묻기(0일째 밤 한밤, 여기서 고백) → 넘어가기 → 머슴 의심 → 과부에게 투표 → (결선) 대장장이에게 투표 → 밤에 머슴 점치기. 2일째 밤 점괘가 맑음이면 된다.

한계: 컨테이너 이미지·설정은 `dev.yml` 그대로지만 Kafka TLS 리스너와 토픽 복제 1은 로컬 실행용으로 더했다. 광고 서버는 Django 개발 서버(`runserver`)로 띄웠다(광고 모듈 문서 사용법). 게임 웹이 광고 서버를 부르는 게이트웨이는 운영 조립이 아직 없어(`configure_ad_gateway`, 웹 모듈 문서) 이 실행에서도 연결하지 않았다. 실행 로그·`.env`·인증서는 저장소 밖에 두었다.

## 수치

| 조건 | 기준 | 측정값 | 측정 방법(지표 이름 또는 스크립트) | 결과 파일 | 통과 |
|---|---|---|---|---|---|
| 1 | 3판 완주, 오류 0 | 11/8 통과: 실제 HTTP 웹·Mongo 자동 3판 완주(500·503 0건), 사람 브라우저 1판 결과 화면까지 진행(위 "사람 브라우저 확인 결과") | `tools/perf/int07_web_run.py`, 사람 확인 | `docs/evidence/perf/INT-07/web_run.json` | 예 |
| 2 | 시나리오 테스트 + 웹 1회씩 재현 | 11/8 미통과: 시나리오 테스트 5개 통과, 웹 재현 2개(결선 재동점 무감금, 무당 감금), 미재현 3개(점괘·고백 전환, 마지막 씐 자 감금, 거짓 점괘 공개). 담당 E, 기한 통합 2(11/9). 11/8 통합 환경 자동 웹 재현: 남은 3개 모두 재현(시드 2·10·6), 사람 브라우저 확인 대기 | `tests.engine.test_int07`, `tools.perf.int07_scenarios`·`server.tests.int07_stack`, 사람 확인 | `docs/evidence/perf/INT-07/stack_run_detail.json` | 아니오(PM 판정 대기) |
| 3 | 0일째·1일째 모두 | 11/8 통과: 자동 아침 화면 확인 4회 빠짐 0, 사람 브라우저에서 1일째·2일째 아침 문장 확인 | `tools/perf/int07_web_run.py`, 사람 확인 | `docs/evidence/perf/INT-07/web_run_detail.json` | 예 |
| 4 | 테스트 + 웹에서 재현 | 11/8 통과: 자동 재전송 재생·409·재시작 뒤 같은 화면, 사람 브라우저 두 번 누르기·탭 두 개·재시작 뒤 이어 하기 | `tools/perf/int07_web_run.py`, 사람 확인 | `docs/evidence/perf/INT-07/web_run_detail.json` | 예 |
| 5 | `reconcile_game_events.py` 불일치 0 | 11/8 재판정 통과(C-07-02 #251, 실제 스택): 브로커·발행기 정지·재개 중 두 판 Mongo 104건 = Kafka 고유 104건 = Delta bronze 104건, 누락 0·중복 0·해시 불일치 0, 정지 중 HTTPS API 오류 0. 불일치 출력은 계약 시험 `tests.contract.test_reconcile_events` | `tools/reconcile_game_events.py` | `docs/evidence/perf/C-07-02/live_faults.json` | 예 |
| 6 | 판 3개 전체 HTML·JSON 금지 키 0 | 11/8 통과: 자동 응답 86개 금지 키 0건, 사람 브라우저 네트워크 탭 검색 0건 | `tools/perf/int07_web_run.py`, 사람 확인 | `docs/evidence/perf/INT-07/web_run.json` | 예 |
| 7 | D-07-02 수치 존재 | 11/8 재판정 통과(D-07-02 #269 병합, 이슈 #79 닫힘): 실제 엔진 200판(rule-v0) 학습 행 12,364행으로 rule_v0·logistic·lightgbm 비교(test 30판, log loss 1.183387·0.866493·0.848075). C Delta export(silver 버전 1)에서 학습 코드로 14,460행을 넘겨 같은 학습 경로가 돌아감(연결 확인용, 11/8 PM 결정). C 몫은 C-07-01 #250 | C-07-01·D-07-02 증빙, `ml.suspicion.train` | `docs/evidence/perf/D-07-02/suspicion.json`, `docs/evidence/perf/D-07-02/delta_connectivity.json` | 예 |
| 8 | 테스트 + 토픽 오프셋 증가 | 11/8 재판정 통과: 경매·유효 판정 픽스처 HTTP 재생(D-05-03 `test_auction_fixtures_over_http`), MySQL 과금 원장·예약·확정·환입(D-06-02 원장 시험, 실제 세 토픽 고유 3건), 실제 광고 HTTP serve·노출·클릭 → MySQL Outbox → Kafka → 공식 bronze 적재(C-06-03 #249 조건 2·3, C-07-01 ad-verification). 토픽 오프셋 수치 대신 Kafka를 거친 bronze 적재로 도달을 확인했다. D-05-03 2번(환입·대사 전체 HTTP 재생)과 D-06-02 8번(ad-01 운영 작업자)은 각 태스크의 남은 범위 | D-05-03·D-06-02·C-06-03·C-07-01 증빙 | `docs/evidence/perf/D-06-02/local_stack.json` | 예 |
| 9 | 구성요소 누락 0, 3.14 아닌 프로세스 0 | 구성요소별 증빙: 웹 `/metrics`·Python 3.14.6(B-05-01, B-07-05), Mongo 저장소 `paldo_repo_commit_duration_seconds`·Python 3.14.4(C-06-01), 발행기 `paldo_outbox_publish_lag_seconds`(C-06-02), 광고 서버 `/metrics`·Python 3.14.6(D-05-03). 구성요소가 통합 환경에 함께 떠 있지 않아 동시 기동 기준의 누락 0·프로세스별 버전 확인은 하지 못함. 런타임 LLM은 P-31 (나)로 구성요소에서 빠짐. 담당 전원, 기한 통합 2(11/9) 전. 11/8 로컬 Compose 동시 기동: Python 구성요소 6개(게임 웹·발행기·광고 서버·광고 작업자 3) 필수 지표 누락 0, 3.14 아닌 프로세스 0(모두 3.14.6), 컨테이너 3개 healthy, 같은 실행 중 Kafka 264건 증가. Kafka TLS 리스너·토픽 복제 1은 로컬 실행용 추가 | `server.tests.int07_stack` | `docs/evidence/perf/INT-07/stack_run.json`, `stack_run_detail.json` | PM 판정 대기 |

## 미통과 항목 담당·기한 (11/7 사용자 결정)

재판정은 PM이 한다. B·C·D의 통합 참여 태스크(B-07-04·C-07-03·D-07-04)는 11/7 사용자 결정으로 취소했고, 아래 막는 작업(원래 구현 태스크)에서 담당이 고친다.

| 항목 | 막는 작업 | 담당 | 기한 |
|---|---|---|---|
| #5 | 해결(11/8 재판정 통과, C-07-02 #251) | C | - |
| #8 | 해결(11/8 재판정 통과, C-06-03 #249·D-05-03·D-06-02) | C·D | - |
| #7 | 해결(11/8 재판정 2 통과, C-07-01 #250·D-07-02 #269) | C·D | - |
| #2 웹 재현 3개 | 11/8 PM 판정으로 #1·#3·#4·#6 통과. #2는 점괘·고백 전환, 마지막 씐 자 감금, 거짓 점괘 공개 웹 재현이 남음. 운영 Mongo 콘텐츠 재적재는 C 확인 대기. 11/8 자동 웹 재현 3/3 완료, 사람 브라우저 확인(위 순서, 약 10분)과 PM 판정만 남음 | E(재현), C(재적재) | 통합 2(11/9) |
| #9 | 통합 환경 동시 기동. 11/8 로컬 Compose 동시 기동 확인 완료, PM 판정 대기 | 전원 | 통합 2(11/9) 전 |

INT 항목은 아니지만 사전 테스트(E-08-10, 11/8)에 필요한 C-07-04(HTTPS 프록시, 참가자 안내 문구 → E)는 11/8 사전 테스트 전까지다.

## 로그

- 엔진 몫 테스트는 unittest 출력만 있다(위 "출력"). 별도 로그 파일은 없다.
- 로컬 Django 회귀(`server.tests`)는 돌리지 않았다. B-07-01 증빙의 회귀 결과를 근거로 삼았다.
- 통합 환경 로그는 운영 게임 서비스 조립과 동시 기동 뒤 이 절에 붙인다. 11/8 로컬 Compose 동시 기동의 구성요소 로그·준비 파일은 저장소 밖 임시 폴더에 두었다가 지웠다. 판별 기록은 `docs/evidence/perf/INT-07/stack_run_detail.json`.
- 11/8 웹 자동 재현의 게임 웹 로그·계정 DB·실행용 설정은 저장소 밖 임시 폴더에 두었다가 지웠다. 판별 기록은 `docs/evidence/perf/INT-07/web_run_detail.json`.

## 트러블

- [트러블 E](../troubles/E.md#e-07-03-통합-1-주관)

## 문서

- `docs/evidence/INT-07.md`
- `docs/troubles/E.md`
- `tools/perf/int07_web_run.py`(11/8 웹 자동 재현 도구, 모듈 머리말에 사용법·계층), 준비 코드 `server/tests/int07_setup.py`·`server/tests/int07_run_settings.py`(`server/tests/README.md`)
