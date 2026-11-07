# 게임 웹

## 무엇을

Django가 게임 JSON API와 회의 화면을 제공한다. 응용 서비스의 공개 상태를 화면 모델로 바꾸고, htmx 행동 요청의 결과를 회의 조각으로 돌려준다.
화면 이미지는 manifest의 콘텐츠 ID로 찾고, Django 상태 확인 경로와 요청 지표로 웹 서비스 상태를 확인한다.
계정 화면은 가입·로그인·로그아웃을 처리한다. 게임 API는 계정에서 발급한 가명 ID로 판과 하위 자원의 소유권을 확인한다.
운영자는 테스트 회차에 참가자와 고정 시드 목록을 등록한다. 참가자는 자신의 회차에서 등록 순서대로 판을 진행한다.
ASGI는 기존 HTTP와 광고 비콘 웹소켓을 나눈다. 광고 클릭·랜딩도 게임 웹 출처에서 중계한다.
수첩의 인물별 의심도, 투표 직전 확신도, 종료 후 세 점수와 자유 의견 설문을 화면에서 저장한다.

## 왜

[API 계약 1·3·6절](../contracts/api.md)에 따라 JSON 응답과 htmx 조각에 같은 게임 상태를 쓰며, 진행 화면에는 플레이어에게 공개된 데이터만 전달한다.
[에셋 규격 4절](../assets/spec.md)에 맞춰 파일 경로를 manifest에서 찾아 이미지 교체 시 화면 경로를 그대로 쓴다.
[개인정보 계약](../contracts/privacy.md)에 따라 로그인 정보와 플레이 기록은 분리해 보관한다. 계정 화면은 Django 세션 인증과 CSRF 검사를 사용한다.

## 사용법

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests --settings=server.paldo_web.test_settings --noinput
python -B server/preview.py --port 8769
```

미리보기는 `http://127.0.0.1:8769/accounts/login`에서 연다. 실행 콘솔에 표시되는 미리보기 계정으로 로그인하면 녹화 판으로 이동한다. 녹화 판은 기록된 입력 순서에 맞춰 행동을 선택한다.

[화면 라우팅](../../web/ROUTING.md)과 [화면 설계](../../web/DESIGN.md)에 요청별 응답과 갱신 규칙을 정리했다.

계정 화면은 `/accounts/signup`, `/accounts/login`, `/accounts/logout`을 사용한다. 새 계정 스키마는 환경 변수를 설정하고 저장소 루트에서 `python server/manage.py migrate`로 준비한다.
로그인 성공 시 기존 로그인 여부와 관계없이 세션 ID를 교체한다.

1. 현장에서 본인임을 확인한 뒤 `python server/manage.py changepassword <로그인 ID>`를 실행한다. 새 비밀번호는 가입과 같은 규칙으로 검사되며 변경 전 세션은 무효가 된다.
2. 로그인 잠금을 해제하려면 `python server/manage.py unlock_account`를 실행하고 프롬프트에 로그인 ID를 입력한다.

고정 회차는 운영자 로그인 뒤 `/admin/sessions`에서 등록한다. 회차 ID, 참가자 화면에 표시된 가명 ID, 실행에 쓸 규칙·엔진·콘텐츠 버전을 입력한다. 고정 판 목록은 [사용자 테스트 계획의 순서](../user_test/plan.md#8-고정-시드-판-목록)에 따라 `preset_id`와 `seed`를 가진 JSON 배열로 입력한다. 참가자는 로그인 뒤 홈에서 다음 판을 시작하거나 진행 중인 판을 이어 간다.

운영 게임 서비스(실제 엔진·Mongo, A-07-03)로 로컬에서 판을 하려면 저장소 루트에서 다음 순서로 한다. 값은 셸 환경 변수로만 넣는다.

1. 계정 MySQL·Mongo(레플리카셋)를 띄우고 `DJANGO_SECRET_KEY`, `PALDO_PUBLIC_ORIGIN`, `PALDO_AUTH_DB_*`를 설정한다(로컬 Compose는 `ops/compose/README.md`).
2. `PALDO_MONGO_URI`에 DB 이름 `paldo_web`을 넣은 레플리카셋 연결 문자열을, `PALDO_GAME_SERVICE_FACTORY`에 `server.infra.game_factory.build_game_service`를 넣는다. 연결·컬렉션(`games`·`progress`·`content`·`b_test_sessions`·`b_ad_identities`)은 B 실행 자원(`server/paldo_web/mongo.py`)이 맡고, 운영 조립은 `b_interactions`만 더한다.
3. 콘텐츠를 한 번 적재한다: `python -m tools.load_content content/bundle_v1.json`(버전 `content-1.0`). 적재 도구는 B 실행 자원이 읽는 `content` 컬렉션에 쓴다(11/7 이름 통일).
4. `python server/manage.py migrate`, 운영자 계정은 `python server/manage.py createsuperuser`로 만든다.
5. 서버를 띄운다: `python -m daphne -b 127.0.0.1 -p 8000 server.paldo_web.asgi:application`. 브라우저는 `PALDO_PUBLIC_ORIGIN` 주소로 연다.
6. 참가자로 가입해 홈의 가명 ID를 확인하고, 운영자로 `/admin/sessions`에서 회차를 등록한다. 버전은 규칙 `python -c "from engine.domain import params; print(params.RULE_PARAMS_VERSION)"`가 출력하는 값(`engine/domain/params.py`의 `RULE_PARAMS_VERSION`, 결정 층 파라미터가 바뀌면 함께 바뀐다), 엔진 `paldo-engine`, 콘텐츠 `content-1.0`이다. 고정 판은 `[{"preset_id": "base6", "seed": 1}]`처럼 넣는다.
7. 참가자로 로그인해 홈에서 판을 시작한다.

웹소켓 실행은 환경을 준비한 뒤 `python -m daphne -b 127.0.0.1 -p 8769 server.paldo_web.asgi:application`을 사용한다. 프로세스는 하나로 유지하고 외부 연결은 C 프록시를 거친다. `configure_ad_gateway`로 실제 광고 제공자를 조립하기 전에는 중계가 실패 응답과 대체 화면으로 끝난다. `server/preview.py`는 HTTP 화면용이며 웹소켓 실행 명령을 대신하지 않는다.

로컬 통신 검증: `python -m server.tests.ws_smoke`. 임시 SQLite·가짜 광고를 별도 Daphne 프로세스에 연결한 뒤 정리한다. 클라이언트 검증: `node server/tests/ws_client_test.cjs`.

광고 지면은 `GET /ads/slot/{slot}`에서만 `AdGateway.fetch`를 호출한다. 로비·종료 화면의 자리는 htmx `load`로 채우며 게임 명령은 광고를 부르지 않는다. `review_side`는 조각만 제공하고 복기 화면 배치는 B-10-01에서 한다. 운영 제공자를 조립하지 않으면 빈 광고로 끝나며 가짜를 자동 주입하지 않는다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `PALDO_PUBLIC_ORIGIN` | 필수 | 브라우저가 접속하는 게임 웹 출처 | 허용 호스트·CSRF 출처·보안 쿠키가 바뀜 |
| `PALDO_AUTH_DB_*` | [통합 계약 4.1](../contracts/integration.md) | Django 계정 MySQL 접속값 | 인증·세션·관리자 테이블의 연결 대상이 바뀜 |
| `PALDO_SIGNUP_INVITE_CODE` | 비어 있음 | 비어 있으면 공개 가입 | 값이 있으면 일치하는 초대 코드를 제출해야 가입 가능 |
| `SESSION_COOKIE_AGE` | 1,209,600초 | 세션 유효 기간 | 새 세션의 기본 유효 기간이 바뀜 |
| `PALDO_GAME_SERVICE_FACTORY` | 비어 있음. 운영 예: `server.infra.game_factory.build_game_service` | 운영 게임 서비스를 조립하는 함수 경로 | 운영 게임 API가 사용하는 서비스가 바뀜. 비어 있으면 게임 API를 쓸 수 없다 |
| `PALDO_MONGO_URI` | 비어 있음 | Mongo 게임·진척·콘텐츠·회차·광고 가명 접속값. 운영 조립은 같은 DB에 보조 기록 `b_interactions`를 더한다 | DB 경로 생략 시 `paldo_web`을 사용한다. 콘텐츠 적재 도구·발행기와 같은 DB를 쓰도록 경로에 DB 이름을 넣는다. 미설정 시 Mongo 게임·회차 조립을 사용할 수 없다 |
| `ASSET_MANIFEST` | `content/images/manifest.json` | 콘텐츠 ID, URL, 이미지 경로 목록 | 화면 조회와 정적 이미지 응답에 함께 적용 |
| 미리보기 `--port` | 8766 | 로컬 HTTP 포트 | 루프백 미리보기 접속 주소가 바뀜 |

## 한계

자유 입력 화면은 `utterance_input.html`·`speech_cards.html`, 추궁 답변은 `speech_line.html`에서 처리한다. JSON·htmx가 같은 수명주기를 사용한다. 게임 서비스 조립 때 해석기를 한 번 생성한다. Django 설정 `INTENT_PARSER_FACTORY = "intent.parser.BaselineIntentParser"`로 D-07-01 기준선으로 전환할 수 있다. 동일 화면 시험과 실제 후보·버전 기록을 [B-07-01 증빙](../evidence/B-07-01.md)에 기록했다. 해석 지연은 `/metrics`의 `paldo_intent_parse_duration_seconds{stage,status}` 히스토그램으로 노출하며 p95는 `histogram_quantile(0.95, sum by (le) (rate(paldo_intent_parse_duration_seconds_bucket[5m])))`로 조회한다.

실제 기준선 수신 확인은 B-07-01에서 수행했다. 다중 카드의 확인 가능 여부는 카드별로 정하고, 직전 대화 상대는 공개 캐묻기 기록에서 보완한다. 재입력에도 선택 인물·근거·직전 상대를 전달한다. B-06-03은 C 게임 저장소를 연결하고, 자유 입력 흐름의 영속 보조 저장은 A-07-03 운영 조립이 `b_interactions`로 더한다. P-31 (나)에 따라 런타임 LLM 서비스 교체는 없으며 폴백·미리 생성한 문장 변형 경로를 사용한다.
B-07-02의 설문은 같은 API에서 응답과 전체 건너뛰기를 받는다. 건너뜀의 세 점수는 `null`로 기록한다. 수첩·확신도·설문의 `paldo.player` 아웃박스는 메모리 시험으로 확인했다. 실제 Kafka 수신은 [C 인수 뒤 확인](../../server/DESIGN.md#b-07-02-인수계약-확인)한다. 슬라이더와 저장 안내는 로컬 Alpine.js 3.14.9로 동작한다.

미리보기는 임시 SQLite와 녹화 엔진, 메모리 게임 저장소를 사용한다. 현재 C 저장소는 `paldo_web/mongo.py`의 명시적 조립으로 연결하며 회차 정책도 같은 자원에 고정한다. 기존 사용자 정의 팩토리는 `prepare_start`를 비워 두면 B 기본 회차 정책을 사용한다. 운영 게임 서비스는 `server/infra/game_factory.py`(A-07-03)가 이 실행 자원(`open_mongo_runtime`)에 실제 엔진(NPC 규칙 v0, 콘텐츠 버전별)과 규칙 의심도 기반 가짜 의심 모델을 넘겨 만들고, 그 게임 서비스에 보조 기록 Mongo 저장소(`server/infra/interaction_store.py`, `<접두어>b_interactions`), 기준선 해석기, 템플릿 렌더러를 끼운다. 보조 기록 저장은 같은 프로세스 안에서 판별 잠금, 프로세스 사이에서 판 문서 임대(30초, 요청이 도는 동안 10초마다 연장, 최대 5초 대기)로 직렬화하고, 임대를 5초 안에 못 잡거나 만료 뒤 늦은 저장은 503으로 거절하며 덮어쓰지 않는다. Mongo 일시 오류 중에는 연장을 다시 시도한다. Mongo가 임대 시간(30초) 내내 응답하지 않으면 연장이 끊겨 다른 프로세스가 임대를 잡을 수 있다(그동안 같은 Mongo에 쓰는 게임 저장도 실패하며, 게임 명령은 판 저장소 버전 검사로 한 번만 반영된다). 배포 계정 DB 권한, 운영 게임 저장소, 실제 광고 요청의 식별자는 통합 환경에서 확인한다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-05-01 | 11/5 | Django 설정, 상태 확인, 요청 로그와 계측 |
| B-05-02 | 11/5 | 게임 JSON API 열 개, 입력 DTO 검증, 가짜 엔진 회귀 시험 |
| B-05-03 | 11/5 | 회의 전체·조각 화면, htmx 행동 폼, manifest 이미지 연결 |
| B-06-01 | 11/6 | 진술표, 투표·결선·감금 공개, 밤 점 대상 화면 |
| B-06-02 | 11/6 | 실제 Compose Mongo에서 같은 ID 재생·버전 충돌·거부·20개 동시 명령 검증. 10명·3분 측정 CLI와 루트 `.env` 연결. 최신 dev 합성 규칙 버전의 회차 등록·판 메타 보존 보완, 수치는 `perf.md` 참고 |
| B-06-03 | 11/6 | 운영자 회차 관리·고정 판 배정·광고 가명. C 저장소 연결·실제 엔진 재기동, Mongo 저장 응답 유실의 1회 재시도·중복 방지 검증 |
| B-06-04 | 11/6 | 가입·로그인·잠금·계정 저장소 검증. VM DB와 광고 요청 확인 대기 |
| B-07-05 | 11/7 | ASGI·인증 웹소켓·광고 중계·재연결. P-29의 비콘 폐기 사유 검증·집계와 페이지 간 보고 반영. VM·성능 시험 대기 |
| B-07-01 | 11/7 | 자유 입력 카드의 모든 반응·실패 화면, 해석기 선택·계측, 발언 폴링과 단계 취소. 신규 조건 8의 Kanana 답변 표기·폴링/재조회 유지 검증 |
| B-07-02 | 11/7 | 수첩·확신도·설문과 전체 건너뛰기 화면, P-29 입력·재전송 검증. 날짜·단계별 동일 확신도 기록 보완. 실제 토픽 수신 대기 |
| A-07-03 | 11/7 | 운영 게임 서비스 조립 `server.infra.game_factory.build_game_service`(B 실행 자원 위에 실제 엔진·의심 모델·보조 기록 저장·기준선 해석기·템플릿 렌더러)과 로컬 실행 순서 |
| B-07-03 | 11/7 | 로비·결과 광고 지연 로드, 3지면 조각·가명 분리·serve별 노출 비콘·동일 출처 클릭 검증. 미제공·실패의 200 빈 조각 검증. 복기 배치는 B-10-01 범위 |

B-06-03의 실제 Mongo 조립은 `server/paldo_web/mongo.py`를 사용한다. 엔진·모델을 명시적으로 넘겨 `open_mongo_runtime`을 호출하고 HTTP 처리 전 `configure()`로 연결한다. 기존 `PALDO_GAME_SERVICE_FACTORY` 함수 안에서는 `factory_service()`를 반환한다. 고정된 콘텐츠 버전·같은 고정 판을 복원한다. 접속 정보는 기존 `PALDO_MONGO_URI` 하나를 사용한다. 고유 컬렉션의 실제 시험은 `server/tests/mongo_game_sessions.py`이며 VM·부하·보조 저장은 후속 검증이다.
