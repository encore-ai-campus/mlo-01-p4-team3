# 게임 명령 시험

## 공통 UI 미리보기

저장소 루트에서 `python -B -m server.tests.ui_theme_preview --port 8783`을 실행한다. 루프백 주소·시험 계정과 임시 DB·녹화 판으로만 동작한다. 운영 설정·실제 DB·광고와 분리된 검수 환경이며 실행·시험 범위는 [UI 적용 검증](../../web/UI_APPLY.md)을 따른다. 운영 계정 또는 실제 엔진·모델 성능 검증에 사용하지 않는다.

B-10-02의 test_village_assets는 전체/htmx의 지도·초상·하단 출처, manifest 좌표 변경·출처 갱신/이스케이프, 읽기 전용 상태 보존, 밤의 추가 패널 비표시를 검사한다. 기존 test_assets의 이미지 교체와 test_preset_screens의 8인 회귀를 함께 실행한다. 로컬 Chrome 미리보기·첫 로딩 측정은 [증빙](../../docs/evidence/B-10-02.md)을 따른다.

## E-09-10 웹 프로세스 수 비교 부하

`python tools/perf/api_bench.py --web-processes 2,1 --users 30 --minutes 3`(경우 이름으로는 `python -B -m server.tests.multiproc_bench --cases p2_memory,p1_memory,p2_redis --redis-url redis://127.0.0.1:6379/0`)은 B-07-05의 시험 조립(`asgi_bench_app.py`)을 Daphne N개로 띄우고 Caddy(운영 Caddyfile과 같은 분배·상태 확인, `--front docker`는 공식 이미지 `caddy:2.10.2`, `--front binary`는 설치된 caddy)를 앞에 둔 채 같은 부하(사용자당 초당 한 요청 + 웹소켓 비콘 1개)를 준다. 모든 경우가 같은 Caddy 경로를 지나므로 경우끼리 비교하며, Docker Desktop 경유 지연이 포함돼 운영 절대값이 아니다. Mongo는 `PALDO_MONGO_URI`, 결과는 `out/handoff/e0910-multiproc-report.json`. 설정 전환·포트·Caddy·Prometheus 대응 시험은 `tests/ops/test_web_scaling.py`(Redis 통합은 `PALDO_TEST_REDIS_URL`이 있을 때만).

전체 PM UI의 test_ui_review는 게임 에셋 manifest 우선·시안 경로 탈출/외부 URL 대체, 진행 중 복기 403·타인 404·종료 후 HTML/JSON, PM 안내의 로그인 제한, 등록 18/임시 84 구분·확정 v5 원화 연결·manifest 보존·classic/vn 왕복·classic 검토 경로 차단을 검사한다. 원본 전체·후보 갤러리와 계약 차이는 [PM 검토 목록](../../web/PM_UI_REVIEW.md), 새로 준비할 에셋은 [제작 계획](../../web/UI_ASSET_PLAN.md)을 따른다. 원본 시안의 고정 데이터와 실제 판을 혼동하지 않는다.

## B-08-01 결과·진척·결말 선택

`python -B server/manage.py test server.tests.test_story_ui server.tests.test_open_start server.tests.test_preset_screens --settings=server.paldo_web.test_settings --noinput`으로 결과·수집 단서·일반 판/고정 회차 결말·초상·시작 UUID 재생을 확인한다. 진척·해금 입력은 시험 전용 대역이며 E-09-08의 실제 해금·재처리를 검증하지 않는다. 도전 필드는 인수 전까지 임의로 추가하지 않는다. [증빙](../../docs/evidence/B-08-01.md)

## B-08-02 프리셋·8인 화면

외형 선택·기본값, 공개 설정 파일의 홈/일반 판 반영, 8인 15화면 HTTP·HTML, 거절 코드 로그, 심문 양식과 실제 엔진 정상·거절 조합을 검사한다.

```powershell
python -B -m server.tests.record_large8
python -B server/manage.py test server.tests.test_preset_screens server.tests.test_utterance_ui --settings=server.paldo_web.test_settings --noinput
python -B -m server.tests.preset_render_bench
```

첫 명령은 `tools.run_engine_games --preset large8 --games 1 --seed 1` 경로를 실행해 공개 PlayerView·명령만 `recorded_large8.json`으로 기록한다. 녹화 재생은 시험 전용이며 운영 엔진을 대체하지 않는다. 렌더 측정은 임시 SQLite·메모리 녹화에서 15화면을 각 10회 HTTP 전체 HTML로 렌더하고 표준 JSON에 기록한다. 브라우저 표시·실제 Mongo·동시 부하 성능으로 해석하지 않는다. 시험에서만 임시 파일/패치로 `large8`을 공개하며 운영 설정 초기값은 `base6`다. 현재 dev 빌드의 6인 공동 완주·E 재확인은 [별도 대기](../../docs/evidence/B-08-02.md)다. 태그 생성은 그 확인의 선행 조건이 아니며 11/10 태그 리허설은 B-09-03이다.

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 계측의 요청 격리·SQL 원문 비노출 시험은 `server/tests/test_bench_metrics.py`다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

## B-07-02 실제 Mongo·Kafka 시험

`requirements/web.txt`의 패키지와 Docker Desktop Linux 엔진을 준비한다. Mongo는 위에서 쓰는 Compose Replica Set을 실행하고, 환경·`server/.env`·Compose 루트 `.env` 중 하나에 `PALDO_MONGO_URI`를 지정한다. 실제 Kafka는 CA·클라이언트 인증서·키와 hostname 검증을 켠 SSL JSON을 Git 제외 경로에 두고 `PALDO_TEST_KAFKA_CONFIG`에 그 파일 경로만 지정한다. 운영 C 발행기의 TLS 검증을 완화하지 않는다.

로컬 TLS 브로커가 없으면 `python -B -m server.tests.kafka_local`을 실행한다. 저장소와 같은 Kafka 4.3.2 이미지에 고유 컨테이너·루프백 포트·2일 만료 시험 인증서를 만들고 연결 JSON 경로와 컨테이너 이름을 출력한다. 비밀·로그·설정은 `out/handoff/kafka-b0702-<실행ID>/`에만 남는다. PowerShell에서 `$env:PALDO_TEST_KAFKA_CONFIG='<출력된 client.json 경로>'`를 지정한다. C가 제공한 실제 TLS 설정을 사용할 수도 있다.

```powershell
python -B server/manage.py test server.tests.mongo_participant.KafkaParticipants --settings=server.paldo_web.mongo_test_settings --noinput --verbosity=2
```

`MongoParticipants`만 지정하면 Kafka 없이 Mongo 시험을 실행한다. `KafkaParticipants`는 Mongo 시험도 상속하며 브로커 설정 누락을 건너뛰지 않고 실패로 처리한다. 각 실행은 `b0702_<UUID>_` 접두어의 정확한 6개 Mongo 컬렉션만 정리한다. 재시작 시험은 같은 가명과 서버 비밀을 새 Python 프로세스에 전달해 메모리 없이 HTTP를 재실행하며 계정·세션은 자식의 임시 SQLite다. Kafka 시험은 `load_test=true`, 고유 판 ID·소비자 그룹을 사용한다. 실제 C 발행기와 소비자 중복 제거를 호출하지만 VM 복제·장애·Spark 적재는 포함하지 않는다.

시험 종료 후 이 도구가 출력한 고유 컨테이너 이름으로 `docker stop <이름>`·`docker rm <이름>`을 실행한다. 다른 컨테이너·공용 토픽·볼륨을 삭제하지 않는다. 기존 Compose Mongo도 이번 실행에서 기동했으면 `docker compose -p paldo-dev -f ops/compose/dev.yml stop mongo`로 정지한다. 인증서·설정은 Git에 포함하지 않는다.

## B-06-02 로컬 시험

Compose를 준비하려면 저장소 루트에서 `python -B -m ops.compose.run make-env` 뒤 `docker compose -p paldo-dev -f ops/compose/dev.yml up -d --wait mongo`를 실행한다. Docker Desktop은 Linux 엔진이어야 한다. Mongo만 실행하며 다른 Compose 서비스를 띄울 필요는 없다. 컨테이너가 healthy이면 `python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name compose_10x3`을 실행한다. 부하 CLI와 Mongo 전용 시험 설정은 환경 → `server/.env` → Compose 루트 `.env` 순으로 URI를 읽는다. 생성된 `.env`는 Git에서 제외하고 기존 볼륨을 삭제하지 않는다.

실제 Mongo 부하: `python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name local_native_10x3`. 연결은 `PALDO_MONGO_URI` 환경 또는 `server/.env`를 사용하며 Replica Set이 필요하다. 고유 접두어의 컬렉션과 임시 SQLite 계정만 만들고 정리한다. 실제 HTTP 계약·동시 요청 시험은 `python -B server/manage.py test server.tests.mongo_commands.MongoCommands --settings=server.paldo_web.mongo_test_settings --noinput`이다. 기존 메모리 경합 시나리오를 실제 Mongo로 교체해 그대로 실행한다.

측정 경로는 Django test client → 인증·CSRF → B API/응용 → C Mongo이며 엔진은 명시적인 녹화 대역이다. 운영 진입점은 이 시험 모듈을 import하지 않는다. 실제 네트워크·ASGI·VM 부하와 구분하며 p50·p95·오류·완주 판 수를 보고한다. 자세한 기준은 [성능 모듈](../../docs/modules/perf.md)에 둔다.

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests --settings=server.paldo_web.test_settings --noinput
python -B server/tests/bench.py --memory --users 10 --minutes 3
```

동시 명령 시험은 실제 Django 세션·CSRF 검사와 시험용 가명 프로필을 사용한다. 20개 요청을 같은 버전에서 멈춘 뒤 저장을 경합시킨다. 서로 다른 ID는 1건 수락·19건 충돌, 같은 ID는 1건 수락·19건 재생을 기대한다.

메모리 부하 시험은 임시 SQLite 세션과 녹화 엔진·원자 저장 대역으로 사용자마다 독립 판을 진행한다. 응답 p50·p95와 오류 건수를 출력한다.

## B-06-04 계정과 광고 포트 비노출

B-06-04의 `test_accounts.Accounts.test_credentials_absent_from_logs_outbox_and_metrics`는 가입·로그인·실패·잠금·녹화 판 완주를 거쳐 파일 로그·아웃박스·지표와 광고 포트 DTO의 인증 입력 비노출을 함께 검사한다. 광고 요청은 세 지면과 판 종료를 포함한 4건이다. 계정 가명·로그인 세션 키·판 ID·쿼리 위조값도 전달하지 않는다. 이 시험의 `FakeAdProvider`·메모리 회차 조립은 테스트 전용이며 실제 HTTP 광고 서버 수신 검증은 B-08-03 연결 뒤 별도로 한다.

## B-06-03 고정 회차와 광고 가명

`server.tests.test_sessions`는 운영자 인증·CSRF, 고정 판 배정과 사람 판 시작 이벤트를 확인한다. 실제 로컬 Mongo에서는 회차 보존, 동시 광고 가명 발급, 참가자별 삭제를 확인한다.

`PALDO_MONGO_URI`를 환경 변수 또는 `server/.env`에 지정한다. 파일을 직접 읽는 시험 환경에는 `python-dotenv`가 필요하다. Mongo 시험은 루프백 연결에서 임시 시험 DB를 만들고 종료 시 그 DB를 삭제한다.

```powershell
python -B server/manage.py test server.tests.mongo_sessions --settings=server.paldo_web.mongo_test_settings --noinput
```

## C-06-01 인수 상태와 부하 시험 범위

- [통합 계약 G5·G5b](../../docs/contracts/integration.md)의 `GameRepository`와 `ProgressRepository` 구현은 C-06-01에서 병합됐다. 생성·공유 연결은 아래 B-06-03 조립을 사용한다. C의 버전 비교·원자 저장 결과는 [C 증빙](../../docs/evidence/C-06-01.md)에 있다.
- 로컬 Compose Mongo 레플리카셋의 접속 문자열은 `PALDO_MONGO_URI`로 전달한다. 측정에는 격리된 시험 DB를 사용한다.
- 태스크가 지정한 `tools/perf/api_bench.py`에서 인수한 저장소를 연결하고 사용자 10명으로 3분 측정한다. 필수 p95는 1초 이하이며 300ms 목표와 비교해 기록한다.
- 저장소 Compose Mongo 측정은 [B-06-02 증빙](../../docs/evidence/B-06-02.md)에 있다. VM·Daphne·광고 비콘을 함께 쓰는 성능 비교는 B-07-05에서 별도로 검증한다.

## B-06-03 실제 Mongo 게임·회차 통합 시험

`mongo_game_sessions`에는 저장 전 연결 오류·실제 저장 직후 응답 유실·연속 실패 시험이 있다. `AutoReconnect`·`NetworkTimeout` 각각에 대해 동일 commit 두 번 이하, 엔진 한 번, 상태 버전·명령·이벤트 단일 반영과 클라이언트 재전송의 `replayed=true`를 검사한다. DB는 실제 Replica Set이며 오류만 쓰기 호출 경계에 주입한다. 물리 네트워크 단절이나 VM 장애 시험은 아니다.

C-06-01의 게임·진척·콘텐츠 저장소는 dev에 병합됐다. `server/paldo_web/mongo.py`의 B 조립으로 실제 엔진·고정 회차·광고 가명을 연결한다. 기존 환경 변수 또는 `server/.env`의 `PALDO_MONGO_URI`를 사용한다. 이 시험은 콘텐츠 적재 트랜잭션이 필요하므로 Replica Set에 연결해야 한다. 기존 Standalone 서비스나 데이터를 변경하지 않는다. 접속 DB 안의 고유 `b0603_<UUID>_` 컬렉션 5개만 만들고 종료 시 그 컬렉션만 삭제한다. 계정은 기본 시험 설정의 임시 SQLite다.

```powershell
python -B server/manage.py test server.tests.test_mongo_composition --settings=server.paldo_web.test_settings --noinput
python -B server/manage.py test server.tests.mongo_game_sessions --settings=server.paldo_web.mongo_test_settings --noinput --verbosity=2
```

로컬에서 기존 회차 전용 시험까지 함께 실행하려면 `server.tests.mongo_sessions`도 지정한다. 이 기존 시험은 루프백만 허용하고 별도 시험 DB를 사용한다. 게임 통합 시험은 실제 엔진·규칙 NPC와 모델 없는 규칙 투표 경로를 사용한다. 진척 포트 보존 검사는 해금 로직·Kafka 수신·성능 검사를 대신하지 않는다. 대상 VM·실제 부하는 별도로 측정한다.

기존 서비스 팩토리 함수에서는 `runtime = open_mongo_runtime(engine=실제엔진, suspicion=선택한모델)` 뒤 `return runtime.factory_service()`를 사용한다. 연결 정보는 환경의 `PALDO_MONGO_URI`를 읽는다. 서버 기동 전에 직접 설치하는 경로는 `runtime.configure()`다. 실행 자원은 서버 수명 동안 보유하고 서버의 모든 요청 처리가 끝난 뒤 `runtime.close()`한다.

## E-07-03 INT-07 웹 자동 재현 준비

`int07_setup.py`(계정 DB 이주·참가자·로그인 세션·Mongo 회차 등록)와 `int07_run_settings.py`(실행용 SQLite 계정 DB + 실제 Mongo)는 `tools/perf/int07_web_run.py`가 하위 프로세스로 쓰는 준비 코드다. 시험 탐색(`test*.py`) 대상이 아니다. 사용법은 [성능 시험 모듈 문서](../../docs/modules/perf.md)에 있다.
## B-07-05 실제 HTTP·웹소켓 성능 비교

```powershell
python -B tools/perf/api_bench.py --compare-asgi --users 10 --minutes 3 --result-name compose_10x3
```

기존 CLI의 `--compare-asgi`는 시험 전용 `asgi_bench.py`를 호출한다. WSGI, Daphne, 정상 광고·비콘, 3초 광고 지연, 광고 실패를 순차로 각 3분 측정한다(기본 약 15분과 준비 시간). 먼저 기존 Compose Mongo의 Replica Set을 준비하고 `PALDO_MONGO_URI`를 환경 또는 기존 `.env`로 전달한다. 게임·진척·콘텐츠는 실제 저장소, 엔진은 B-06-02와 같은 녹화 엔진, 계정은 임시 SQLite다.

`asgi_bench_app.py`·`asgi_bench_settings.py`는 이 도구의 하위 프로세스 전용이며 운영 실행에 지정하지 않는다. 임시 계정·루프백 서버·고유 `b0705_<UUID>_` 컬렉션을 만들고 종료 시 자기 자료만 지운다. 운영 계정·컬렉션·설정은 바꾸지 않는다. 이벤트의 `load_test=true`를 검사한다. 비콘은 사용자별 초당 한 건이며 ping에 pong으로 응답한다.

모든 시나리오의 게임 오류 0건·p95 1초 이하·판 완주, 혼합 부하의 비콘 ack·연결 오류 0건, 장애 중 실제 실패 ack를 검사한다. 장애 p95는 정상 광고·비콘 혼합 부하 대비 50ms 이하여야 한다. 게임 API 지연에는 광고 조각·비콘 지연을 섞지 않는다. 오류 표본은 제외하지 않는다. 공식 결과 저장은 10명 이상·각 3분 이상에서만 허용한다. 짧은 사전 시험은 `--result-name` 없이 실행한다.

표준 결과는 `docs/evidence/perf/B-07-05/<이름>.json`, 상태 코드·중계 지표를 포함한 상세 결과는 무시되는 `out/handoff/b0705-network-report.json`에 쓴다. 측정값·오류 또는 기준 초과는 종료 코드 1이며 실행 실패는 2다. VM·실제 광고 서버·HTTPS·Kafka 성능을 증명하는 시험은 아니다.

B-08-01 버전 회귀는 현재 E 원문과 B 보관본의 일치, 새 버전 배포 후 기존 판의 두 결말·수집 단서가 전체/htmx/반복 조회에서 유지되는지 검사한다. 새 버전은 파일 읽기 대역이며 실제 콘텐츠 파일·DB는 수정하지 않는다. 기존 버전 보관본의 삭제·덮어쓰기는 금지한다.

## B-08-03 HTTP·장애 검증

- 관련 회귀: python -B server/manage.py test server.tests.test_ad_client server.tests.test_ad_slots server.tests.test_ws server.tests.test_asgi_bench --settings=server.paldo_web.test_settings --noinput
- 짧은 사전 측정: python -B tools/perf/ad_fault_test.py --local-fixture --users 3 --minutes 0.5
- D 인수 후 정상 측정: PALDO_ADS_INTERNAL_URL을 환경에 주입하고 python -B tools/perf/ad_fault_test.py --scenario normal --users 10 --minutes 5 --output out/handoff/ad-normal.json
- D가 장애를 주입한 뒤 같은 조건으로 --scenario stopped|unresponsive|delay|error --baseline out/handoff/ad-normal.json을 각각 실행한다. 장애 종류는 외부 운영자가 제어하고 CLI가 별도 제어 API를 호출하지 않는다.

게임 부하는 기존 asgi_bench의 api_bench 명령 경로를 재사용하며 세 지면·웹소켓 비콘·클릭을 동시에 요청한다. 테스트 계정은 임시 SQLite, 게임은 녹화 엔진·실제 격리 Mongo다. 부하 요청에는 load_test가 붙는다. 결과에는 토큰·쿠키·주소·계정 ID를 기록하지 않는다. local-http-fixture 결과나 5분 미만 실행은 공식 D/VM 합격 증빙이 아니다. [인수 항목·수치](../../docs/evidence/B-08-03.md).
