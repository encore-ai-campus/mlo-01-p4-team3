# 게임 명령 시험

## B-06-02 로컬 시험

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

## C-06-01 인수 뒤 확인할 사항

- [통합 계약 G5·G5b](../../docs/contracts/integration.md)의 `GameRepository`와 `ProgressRepository` 구현 및 생성 방법을 인수한다. 게임 저장소의 버전 비교·원자 저장 시험 결과도 확인한다.
- 로컬 Compose Mongo 레플리카셋의 접속 문자열은 `PALDO_MONGO_URI`로 전달한다. 측정에는 격리된 시험 DB를 사용한다.
- 태스크가 지정한 `tools/perf/api_bench.py`에서 인수한 저장소를 연결하고 사용자 10명으로 3분 측정한다. 필수 p95는 1초 이하이며 300ms 목표와 비교해 기록한다.
