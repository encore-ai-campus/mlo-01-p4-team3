# 게임 명령 시험

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
