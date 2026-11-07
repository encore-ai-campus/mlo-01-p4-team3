# 게임 API 성능 시험

## 무엇을

B의 명령 재전송·동시 요청 시험과 게임 API 지연 측정 방법을 설명한다. 동시 요청 시험은 수락·충돌·재생 건수와 최종 저장 버전을 확인한다.

## 왜

[포트 계약 2절](../contracts/ports.md#2-명령-처리-책임-경계)은 한 버전에서 명령 하나만 저장하고 같은 ID의 명령을 재생하도록 정한다. [B-06-02](../tasks/B_web.md#b-06-02-멱등성버전-충돌동시-요청-시험)는 사용자 10명의 3분 부하에서 게임 API p95 1초 이하를 요구한다. 300ms는 실측과 비교하는 목표다.

## 사용법

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests.test_concurrent_commands --settings=server.paldo_web.test_settings --noinput
python -B server/tests/bench.py --memory --users 10 --minutes 3
python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name local_native_10x3
python -B tools/perf/api_bench.py --users 10 --minutes 3 --result-name compose_10x3
python -B server/manage.py test server.tests.mongo_commands.MongoCommands --settings=server.paldo_web.mongo_test_settings --noinput
```

실제 Mongo 명령은 환경 → `server/.env` → Compose 루트 `.env` 순으로 `PALDO_MONGO_URI`를 읽는다. Replica Set이 필요하다. 시험은 임시 SQLite 계정·Django 세션/CSRF와 고유 `b0602_<UUID>_` Mongo 컬렉션 5개를 사용하고 종료 시 자기 자료만 정리한다. URI·쿠키·로그인 ID는 결과에 넣지 않는다. Compose Mongo의 준비·실행 순서는 [서버 시험 안내](../../server/tests/README.md)에 둔다.

INT-07 웹 자동 재현(실제 HTTP 게임 웹 + Mongo 레플리카셋 + 운영 조립, 버튼 명령만으로 3판·아침 화면·재생·409·서버 재시작 뒤 이어 하기·금지 키)은 `tools/perf/int07_web_run.py`다. 연결 문자열에 DB 이름을 넣고, 결과는 `--record`로 `docs/evidence/perf/INT-07/`에 쓴다. 계정 DB는 실행용 SQLite 파일이다. Django·pymongo가 필요한 준비(계정·세션·회차 등록)와 실행 설정은 `server/tests/int07_setup.py`·`int07_run_settings.py`에 두고 하위 프로세스로 부른다(`tools/`는 제3자 라이브러리 import 금지). 콘텐츠 버전이 이미 있는 DB에서는 적재가 실패해 멈추므로 빈 DB에서 돌린다. [INT-07 증빙](../evidence/INT-07.md)

```bash
PALDO_MONGO_URI=<레플리카셋 URI, /paldo_web 포함> python -m tools.perf.int07_web_run --out <임시 폴더> --record < /dev/null
```

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `--users` | 10 | 독립 판을 진행하는 시험 사용자 수 | 동시 요청 수가 바뀐다 |
| `--minutes` | 3 | 측정 시간(분) | 지연 표본 수가 바뀐다 |
| `--memory` | 사전 시험 필수 | 녹화 엔진과 메모리 저장 대역 사용 | Mongo 부하 결과로 사용하지 않는다 |
| `--result-name` | 생략 | 실제 Mongo 결과 JSON 이름 | 지정하면 `docs/evidence/perf/B-06-02/<이름>.json`에 표준 형식으로 쓴다 |
| `PALDO_MONGO_URI` | 없음 | 시험 대상 Replica Set | 환경·`server/.env`·Compose 루트 `.env`로 받는다 |

## 한계

`api_bench.py`는 녹화 엔진과 실제 Mongo 게임·진척·콘텐츠를 연결한다. 사용자마다 독립 판을 초당 한 요청으로 진행하는 closed-loop 시험이다. nearest-rank p50·p95에 시작·명령 응답을 모두 넣으며 오류도 제외하지 않는다. 응답 오류 또는 p95 1,000ms 초과면 종료 코드 1이다. 이벤트는 모두 `load_test=true`로 표시한다.

전송은 Django test client이며 실제 소켓·Daphne·HTTPS·VM MySQL·Kafka 부하를 포함하지 않는다. 자유 입력·보조 저장·광고도 측정하지 않는다. Windows 네이티브 Replica Set 결과와 태스크의 Compose Mongo 결과를 구분하며, 10명·3분은 B-06-02 기준이고 P-07의 통합 30판·60판 측정을 대신하지 않는다. [B-06-02 증빙](../evidence/B-06-02.md)

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-06-02 | 11/6 | 실제 Mongo API 부하 CLI·20개 HTTP 경합·재생/충돌/거부 검증. 저장소 Compose Mongo 10명·3분 p95 244.861ms·오류 0, 네이티브 결과와 분리 |
| B-06-03 | 11/6 | C Mongo 연결 조립·실제 회차 재개 검증. Compose API p95 측정은 B-06-02 증빙, VM·ASGI 비교는 B-07-05에서 관리 |
| E-07-03 | 11/8 | INT-07 웹 자동 재현 도구 `int07_web_run.py` 추가 |
