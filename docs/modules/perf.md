# 게임 API 성능 시험

판 시작의 콘텐츠 재사용(11/8): `GameService.start`가 읽은 의존성을 요청 안에서 `_detail`에 전달한다. `_detail`은 저장된 판을 다시 읽어 소유권을 확인하고, 콘텐츠 버전이 같을 때만 재사용한다. 다른 버전이면 다시 읽으며 미적재 오류를 숨기지 않는다. 기존 판 재개·동시 최초 생성의 승자 응답·진척 반영·CAS·멱등·저장 응답 유실 복구는 유지한다. 요청 사이 캐시는 없다.

`server/tests/asgi_bench_metrics.py`는 시험 전용 미들웨어와 단계 계측이다. 계정 SQL 실행·진척 조회/등록·게임 생성·엔진·뷰 생성·응답 변환/JSON 생성·저장 응답 변환을 기록한다. 시험 응답의 숫자 헤더로 같은 요청의 서버 시간과 클라이언트 시간을 연결한다. SQL 본문·인자·쿠키·주소는 기록하지 않는다. 서버 시간은 미들웨어 진입부터 응답 반환까지이며, 클라이언트와의 차이에는 연결·요청 전달·서버 진입 전후·응답 전송·읽기/해석이 함께 포함된다. 이를 네트워크 또는 스레드 대기 하나로 단정하지 않는다. 단계 시간은 중첩될 수 있어 p95를 합산하지 않는다. 운영 설정·응답에는 이 미들웨어와 헤더를 넣지 않는다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

이전 판정(개선 전, 11/8): 로컬 재측정은 광고 장애 p95 증가 523.589ms·50.620ms로 50ms 기준 미달이다. 중계 실패 ack·게임 오류 0건과 성능 통과를 구분한다. 이후 B 중복 조회 개선 후 같은 조건 재측정은 통과했다. 장애 증가 -26.441ms·22.157ms이며 VM 검증은 보류한다. [증빙](../evidence/B-07-05.md)

## 무엇을

B의 명령 재전송·동시 요청 시험과 게임 API 지연 측정 방법을 설명한다. 동시 요청 시험은 수락·충돌·재생 건수와 최종 저장 버전을 확인한다.
`--compare-asgi`는 실제 HTTP WSGI·Daphne 기준선과 광고·웹소켓 비콘 혼합 부하를 비교하는 B-07-05 시험이다.

## 왜

[포트 계약 2절](../contracts/ports.md#2-명령-처리-책임-경계)은 한 버전에서 명령 하나만 저장하고 같은 ID의 명령을 재생하도록 정한다. [B-06-02](../tasks/B_web.md#b-06-02-멱등성버전-충돌동시-요청-시험)는 사용자 10명의 3분 부하에서 게임 API p95 1초 이하를 요구한다. 300ms는 실측과 비교하는 목표다.

[B-07-05](../tasks/B_web.md#b-07-05-웹소켓-연결광고-신호-중계)는 WSGI·Daphne를 같은 조건으로 비교하고, 광고 지연·실패와 실제 인증 비콘을 함께 보내도 게임 API p95 증가가 50ms 이하인지 확인한다. [광고 포트](../contracts/ports.md#5-광고-포트-adprovider-게임-경로-보호)의 게임 오류율 불증가도 함께 기록한다.

## 사용법

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests.test_concurrent_commands --settings=server.paldo_web.test_settings --noinput
python -B server/tests/bench.py --memory --users 10 --minutes 3
python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name local_native_10x3
python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name compose_10x3
python -B tools/perf/api_bench.py --compare-asgi --users 10 --minutes 3 --result-name compose_10x3
python -B server/manage.py test server.tests.mongo_commands.MongoCommands --settings=server.paldo_web.mongo_test_settings --noinput
```

실제 Mongo 명령은 환경 → `server/.env` → Compose 루트 `.env` 순으로 `PALDO_MONGO_URI`를 읽는다. Replica Set이 필요하다. 시험은 임시 SQLite 계정·Django 세션/CSRF와 고유 `b0602_<UUID>_` Mongo 컬렉션 5개를 사용하고 종료 시 자기 자료만 정리한다. URI·쿠키·로그인 ID는 결과에 넣지 않는다. Compose Mongo의 준비·실행 순서는 [서버 시험 안내](../../server/tests/README.md)에 둔다.

INT-07 웹 자동 재현(실제 HTTP 게임 웹 + Mongo 레플리카셋 + 운영 조립, 버튼 명령만으로 3판·아침 화면·재생·409·서버 재시작 뒤 이어 하기·금지 키)은 `tools/perf/int07_web_run.py`다. 연결 문자열에 DB 이름을 넣고, 결과는 `--record`로 `docs/evidence/perf/INT-07/`에 쓴다. 계정 DB는 실행용 SQLite 파일이다. Django·pymongo가 필요한 준비(계정·세션·회차 등록)와 실행 설정은 `server/tests/int07_setup.py`·`int07_run_settings.py`에 두고 하위 프로세스로 부른다(`tools/`는 제3자 라이브러리 import 금지). 콘텐츠 버전이 이미 있는 DB에서는 적재가 실패해 멈추므로 빈 DB에서 돌린다. [INT-07 증빙](../evidence/INT-07.md)

```bash
PALDO_MONGO_URI=<레플리카셋 URI, /paldo_web 포함> python -m tools.perf.int07_web_run --out <임시 폴더> --record < /dev/null
```

B-10-08: `python -B -m tools.perf.account_assets --browser-sample <로그인 계측 JSON>`은 로컬 WOFF2 합계와 명시적 미리보기의 Navigation/Resource Timing 표본을 대조한다. 1280·360px 로그인만 받으며 계측은 `ui_theme_preview --font-probe`로 실행한다. `initial_load_ms`는 navigation `loadEventEnd`, `font_ready_ms`는 load 뒤 `document.fonts.ready`와 다음 화면 갱신 시점이다. 내려받은 글꼴 바이트는 Resource Timing의 `encodedBodySize` 합계이며 네트워크 전송량·VM 성능·화면 품질 합격을 뜻하지 않는다. 결과는 `perf/B-10-08/login-<폭>.json`이다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `--users` | 10 | 독립 판을 진행하는 시험 사용자 수 | 동시 요청 수가 바뀐다 |
| `--minutes` | 3 | 측정 시간(분) | 지연 표본 수가 바뀐다 |
| `--memory` | 사전 시험 필수 | 녹화 엔진과 메모리 저장 대역 사용 | Mongo 부하 결과로 사용하지 않는다 |
| `--result-name` | 생략 | 실제 Mongo 결과 JSON 이름 | 지정하면 `docs/evidence/perf/B-06-02/<이름>.json`에 표준 형식으로 쓴다 |
| `PALDO_MONGO_URI` | 없음 | 시험 대상 Replica Set | 환경·`server/.env`·Compose 루트 `.env`로 받는다 |
| `--compare-asgi` | 끔 | WSGI·Daphne·정상 혼합·3초 지연·실패의 다섯 시나리오를 실제 소켓으로 순차 측정 | `--minutes`는 시나리오당 시간이며 기본 약 15분. 결과는 B-07-05에 저장 |

## 한계

`api_bench.py`는 녹화 엔진과 실제 Mongo 게임·진척·콘텐츠를 연결한다. 사용자마다 독립 판을 초당 한 요청으로 진행하는 closed-loop 시험이다. nearest-rank p50·p95에 시작·명령 응답을 모두 넣으며 오류도 제외하지 않는다. 응답 오류 또는 p95 1,000ms 초과면 종료 코드 1이다. 이벤트는 모두 `load_test=true`로 표시한다.

전송은 Django test client이며 실제 소켓·Daphne·HTTPS·VM MySQL·Kafka 부하를 포함하지 않는다. 자유 입력·보조 저장·광고도 측정하지 않는다. Windows 네이티브 Replica Set 결과와 태스크의 Compose Mongo 결과를 구분하며, 10명·3분은 B-06-02 기준이고 P-07의 통합 30판·60판 측정을 대신하지 않는다. [B-06-02 증빙](../evidence/B-06-02.md)

위 한계는 기본 B-06-02 모드다. `--compare-asgi`는 실제 HTTP·인증 웹소켓과 기존 가짜 광고 제공자를 사용한다. WSGI는 스레드형 wsgiref, Daphne는 프로세스 1개이며 같은 임시 SQLite 계정·실제 Mongo·녹화 입력으로 비교한다. 게임 API 지연은 시작·명령만 집계하고 광고·비콘은 별도 건수로 센다. 오류 0건·게임 p95 1초 이하·장애 p95 증가 50ms 이하와 비콘 실제 전송/실패를 검사한다. VM·실제 광고 HTTP·HTTPS·Kafka·실제 엔진 처리량과 P-07 통합 부하는 포함하지 않는다. [B-07-05 증빙](../evidence/B-07-05.md)

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-10-08 | 11/9 | 로그인 브라우저 Timing·로컬 글꼴 용량을 분리 계측하는 account_assets, 결과 JSON과 로컬 표본의 한계 기록 |
| B-06-02 | 11/6 | 실제 Mongo API 부하 CLI·20개 HTTP 경합·재생/충돌/거부 검증. 저장소 Compose Mongo 10명·3분 p95 244.861ms·오류 0, 네이티브 결과와 분리 |
| B-06-03 | 11/6 | C Mongo 연결 조립·실제 회차 재개 검증. Compose API p95 측정은 B-06-02 증빙, VM·ASGI 비교는 B-07-05에서 관리 |
| E-07-03 | 11/8 | INT-07 웹 자동 재현 도구 `int07_web_run.py` 추가 |
| B-07-05 | 11/7 | 11/8 실제 루프백 WSGI·Daphne와 광고·인증 비콘 혼합 부하 비교 모드 추가. VM 검증과 분리 |
