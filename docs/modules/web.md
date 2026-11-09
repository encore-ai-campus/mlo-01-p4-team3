# 게임 웹

판 시작의 저장 후 재조회는 유지하고 같은 요청의 같은 버전 콘텐츠만 재사용한다. 시험 경로는 `asgi_bench.py` → `asgi_bench_app.py` → 시험 전용 `asgi_bench_metrics.py`·`asgi_bench_settings.py`이며 숫자 헤더로 요청별 시간만 연결한다. 운영 URL·DTO·화면 흐름·계약 조건은 유지한다.

일반 명령은 소유권을 확인한 요청 내 상태·콘텐츠를 재사용하고, 광고 가명은 기존 값을 조회한 뒤 최초 생성만 upsert한다. 소유권·멱등·CAS·세션 폐기·majority 조건을 유지한다. 시험 전용 단계별 계측과 상세 결과는 `server/tests/asgi_bench_app.py`·`asgi_bench.py`, 동작 회귀는 `test_api.py`·`test_ad_identity_reads.py`·`mongo_game_sessions.py`에 있다.

## 무엇을

B-10-02는 고정 지도와 주민 전체 초상 목록을 연결한다. manifest의 regions·URL과 공개 인물 이름만 쓰며 출처는 SOURCES.md 첫 줄을 화면 하단에 표시한다. 역할·실제 이동은 표시하지 않는다.

Django가 게임 JSON API와 회의 화면을 제공한다. 응용 서비스의 공개 상태를 화면 모델로 바꾸고, htmx 행동 요청의 결과를 회의 조각으로 돌려준다.
화면 이미지는 manifest의 콘텐츠 ID로 찾고, Django 상태 확인 경로와 요청 지표로 웹 서비스 상태를 확인한다.
계정 화면은 가입·로그인·로그아웃을 처리한다. 게임 API는 계정에서 발급한 가명 ID로 판과 하위 자원의 소유권을 확인한다.
로그인·동의한 사용자는 홈의 "새 게임"으로 회차 없는 일반 판을 바로 시작한다. 시드는 서버가 난수로 정한다(E-08-14).
실험용으로 운영자가 테스트 회차에 참가자와 고정 시드 목록을 등록하면, 참가자는 자신의 회차에서 등록 순서대로 판을 진행한다.
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

일반 판은 홈의 "새 게임 시작"으로 시작한다. 공개 프리셋(지금 `base6`)만 고를 수 있고 시드는 서버가 정하며, 끝나지 않은 일반 판은 홈의 "이어 하기"로 돌아간다. 판 메타의 콘텐츠 버전은 `PALDO_CONTENT_VERSION`에서 읽으므로 운영에서는 적재한 콘텐츠 버전(예: `content-1.0.1`)을 넣는다. 입력 규칙은 [API 계약 2-3절](../contracts/api.md).

고정 회차는 운영자 로그인 뒤 `/admin/sessions`에서 등록한다. 회차 ID, 참가자 화면에 표시된 가명 ID, 실행에 쓸 규칙·엔진·콘텐츠 버전을 입력한다. 고정 판 목록은 [사용자 테스트 계획의 순서](../user_test/plan.md#8-고정-시드-판-목록)에 따라 `preset_id`와 `seed`를 가진 JSON 배열로 입력한다. 참가자는 로그인 뒤 홈에서 다음 판을 시작하거나 진행 중인 판을 이어 간다.

운영 게임 서비스(실제 엔진·Mongo, A-07-03)로 로컬에서 판을 하려면 저장소 루트에서 다음 순서로 한다. 값은 셸 환경 변수로만 넣는다.

1. 계정 MySQL·Mongo(레플리카셋)를 띄우고 `DJANGO_SECRET_KEY`, `PALDO_PUBLIC_ORIGIN`, `PALDO_AUTH_DB_*`를 설정한다(로컬 Compose는 `ops/compose/README.md`).
2. `PALDO_MONGO_URI`에 DB 이름 `paldo_web`을 넣은 레플리카셋 연결 문자열을, `PALDO_GAME_SERVICE_FACTORY`에 `server.infra.game_factory.build_game_service`를 넣는다. 연결·컬렉션(`games`·`progress`·`content`·`b_test_sessions`·`b_ad_identities`)은 B 실행 자원(`server/paldo_web/mongo.py`)이 맡고, 운영 조립은 `b_interactions`만 더한다.
3. 콘텐츠를 한 번 적재한다: `python -m tools.load_content content/bundle_v1.json`(버전 `content-1.0.1`). 적재 도구는 B 실행 자원이 읽는 `content` 컬렉션에 쓴다(11/7 이름 통일).
4. `python server/manage.py migrate`, 운영자 계정은 `python server/manage.py createsuperuser`로 만든다.
5. 서버를 띄운다: `python -m daphne -b 127.0.0.1 -p 8000 server.paldo_web.asgi:application`. 브라우저는 `PALDO_PUBLIC_ORIGIN` 주소로 연다.
6. 참가자로 가입해 홈의 가명 ID를 확인하고, 운영자로 `/admin/sessions`에서 회차를 등록한다. 버전은 규칙 `python -c "from engine.domain import params; print(params.RULE_PARAMS_VERSION)"`가 출력하는 값(`engine/domain/params.py`의 `RULE_PARAMS_VERSION`, 결정 층 파라미터가 바뀌면 함께 바뀐다), 엔진 `paldo-engine`, 콘텐츠 `content-1.0.1`(3번에서 적재한 버전과 같아야 한다)이다. 고정 판은 `[{"preset_id": "base6", "seed": 1}]`처럼 넣는다.
7. 참가자로 로그인해 홈에서 판을 시작한다. 회차 없이 "새 게임"으로 하려면 서버를 띄우기 전에 `PALDO_CONTENT_VERSION=content-1.0.1`을 설정한다(이 경우 6의 회차 등록은 필요 없다).

웹소켓 실행은 환경을 준비한 뒤 `python -m daphne -b 127.0.0.1 -p 8769 server.paldo_web.asgi:application`을 사용한다. 프로세스는 하나로 유지하고 외부 연결은 C 프록시를 거친다. `configure_ad_gateway`로 실제 광고 제공자를 조립하기 전에는 중계가 실패 응답과 대체 화면으로 끝난다. `server/preview.py`는 HTTP 화면용이며 웹소켓 실행 명령을 대신하지 않는다.

로컬 통신 검증: `python -m server.tests.ws_smoke`. 임시 SQLite·가짜 광고를 별도 Daphne 프로세스에 연결한 뒤 정리한다. 클라이언트 검증: `node server/tests/ws_client_test.cjs`.

광고 지면은 `GET /ads/slot/{slot}`에서만 `AdGateway.fetch`를 호출한다. 로비·종료 화면의 자리는 htmx `load`로 채우며 게임 명령은 광고를 부르지 않는다. `review_side`는 조각만 제공하고 복기 화면 배치는 B-10-01에서 한다. 운영 제공자를 조립하지 않으면 빈 광고로 끝나며 가짜를 자동 주입하지 않는다.

## B-10-07 UI 인계

PALDO_GAME_UI=vn(기본)은 확정 VN 화면, classic(선택)은 기존 화면이다. 모드별 템플릿 캐시를 분리한다. 공개 UI 모델은 PlayerView를 보강하며 API·도메인 DTO·명령·DB는 유지한다. 무대 창은 원래 폼을 이동하고 자동 명령을 만들지 않는다. 대사창 25%, 전체 발언 펼침·전신 건너뛰기·탭 읽기 보존을 연결한다. 여성 무당은 사용자 확정 v5를 공식 sprite/portrait ID에 함께 반영했다.

[PM 검토](../../web/PM_UI_REVIEW.md) · [되돌림](../../ops/proxy/README.md#게임-ui-선택과-되돌림) · [증빙](../evidence/B-10-07.md). 첫 등장·추방/처형·엔딩 컷과 표정은 E 인수 잔여이며 제목·호랑이는 이번 범위에서 제외하고 모바일은 900px 미만 무대 아래 배치로 확정됐다. 전체 종료 대신 Refs로 연결한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `PALDO_ADS_INTERNAL_URL` | 비어 있음 | 광고 서버 내부 출처. HTTP 제공자를 최초 요청 때 조립 | 변경 후 게임 프로세스를 다시 시작한다. 브라우저 공개 출처가 아님 |
| `PALDO_PUBLIC_ORIGIN` | 필수 | 브라우저가 접속하는 게임 웹 출처 | 허용 호스트·CSRF 출처·보안 쿠키가 바뀜 |
| `PALDO_AUTH_DB_*` | [통합 계약 4.1](../contracts/integration.md) | Django 계정 MySQL 접속값 | 인증·세션·관리자 테이블의 연결 대상이 바뀜 |
| `PALDO_SIGNUP_INVITE_CODE` | 비어 있음 | 비어 있으면 공개 가입 | 값이 있으면 일치하는 초대 코드를 제출해야 가입 가능 |
| `SESSION_COOKIE_AGE` | 1,209,600초 | 세션 유효 기간 | 새 세션의 기본 유효 기간이 바뀜 |
| `PALDO_GAME_SERVICE_FACTORY` | 비어 있음. 운영 예: `server.infra.game_factory.build_game_service` | 운영 게임 서비스를 조립하는 함수 경로 | 운영 게임 API가 사용하는 서비스가 바뀜. 비어 있으면 게임 API를 쓸 수 없다 |
| `PALDO_MONGO_URI` | 비어 있음 | Mongo 게임·진척·콘텐츠·회차·광고 가명 접속값. 운영 조립은 같은 DB에 보조 기록 `b_interactions`를 더한다 | DB 경로 생략 시 `paldo_web`을 사용한다. 콘텐츠 적재 도구·발행기와 같은 DB를 쓰도록 경로에 DB 이름을 넣는다. 미설정 시 Mongo 게임·회차 조립을 사용할 수 없다 |
| `PALDO_CONTENT_VERSION` | 비어 있음. 운영 예: `content-1.0.1` | 회차 없는 일반 판의 판 메타 `content_version`(Mongo에 적재된 활성 버전) | 새 일반 판의 콘텐츠가 바뀜. 비어 있으면 일반 판 시작은 503, 적재되지 않은 버전이면 시작 시 503 |
| `ASSET_MANIFEST` | `content/images/manifest.json` | 콘텐츠 ID, URL, 이미지 경로 목록 | 화면 조회와 정적 이미지 응답에 함께 적용 |
| 출처 요약 | `content/images/SOURCES.md` 첫 비어 있지 않은 줄 | 게임 하단의 도구·이용 안내 | 다음 조회에 반영, 누락은 확인 필요 표시 |
| `server/app/presets_enabled.json` | `["base6"]` | 새 일반 판에 공개할 프리셋 ID 목록 | 파일만 바꾸면 홈과 일반 판 시작 허용 목록이 함께 바뀜. 추가 공개는 PM 승인 이후. 회차 판·기존 판 재개는 유지 |
| 미리보기 `--port` | 8766 | 로컬 HTTP 포트 | 루프백 미리보기 접속 주소가 바뀜 |
| `PALDO_CHANNEL_LAYER` | `memory`(비어 있어도 같음) | 채널 레이어. `memory`는 프로세스 안, `redis`는 Redis(선택, E-09-10) | `redis`면 `PALDO_REDIS_URL` 필수, 기동 때 연결 실패면 멈춤. 지금 웹소켓은 광고 신호 전용이라 프로세스 2개도 `memory`로 충분 |
| `PALDO_REDIS_URL`, `PALDO_REDIS_PREFIX` | 비어 있음, `paldo-web` | Redis 채널 레이어 주소(`redis://127.0.0.1:6379/0`, 같은 VM)와 키 접두어 | `PALDO_CHANNEL_LAYER=redis`일 때만 쓴다 |
| `PALDO_EXTRA_ALLOWED_HOSTS` | 비어 있음 | 지표 수집기가 대상 주소의 호스트로 올 때 더할 호스트 이름(쉼표, E-09-10). 로컬 Compose Prometheus는 `host.docker.internal` | 와일드카드·포트가 들어가면 기동 거부 |
| `PALDO_WEB_INSTANCE` | 비어 있음(실행 도구가 포트로 채움) | 웹 프로세스 구분. 8000이 아니면 로그를 `out/logs/web-<포트>.jsonl`에 씀 | 숫자가 아니면 기동 거부 |

홈·종료 화면의 단서 링크로 본인 진척을 확인하고 다음 고정 판 시작 폼에서 열린 결말을 선택한다. API는 기존 `ending_choice`를 사용한다. 선택형 도전은 전송·메타 계약 및 공개 판정 인수 전 비활성이며 세부 인수 항목은 서버 설계에 있다.

## 한계

자유 입력 화면은 `utterance_input.html`·`speech_cards.html`, 추궁 답변은 `speech_line.html`에서 처리한다. JSON·htmx가 같은 수명주기를 사용한다. 게임 서비스 조립 때 해석기를 한 번 생성한다. Django 설정 `INTENT_PARSER_FACTORY = "intent.parser.BaselineIntentParser"`로 D-07-01 기준선으로 전환할 수 있다. 동일 화면 시험과 실제 후보·버전 기록을 [B-07-01 증빙](../evidence/B-07-01.md)에 기록했다. 해석 지연은 `/metrics`의 `paldo_intent_parse_duration_seconds{stage,status}` 히스토그램으로 노출하며 p95는 `histogram_quantile(0.95, sum by (le) (rate(paldo_intent_parse_duration_seconds_bucket[5m])))`로 조회한다.

실제 기준선 수신 확인은 B-07-01에서 수행했다. 다중 카드의 확인 가능 여부는 카드별로 정하고, 직전 대화 상대는 공개 캐묻기 기록에서 보완한다. 재입력에도 선택 인물·근거·직전 상대를 전달한다. B-06-03은 C 게임 저장소를 연결하고, 자유 입력 흐름의 영속 보조 저장은 A-07-03 운영 조립이 `b_interactions`로 더한다. P-31 (나)에 따라 런타임 LLM 서비스 교체는 없으며 폴백·미리 생성한 문장 변형 경로를 사용한다.
B-07-02의 설문은 같은 API에서 응답과 전체 건너뛰기를 받는다. 건너뜀의 세 점수는 `null`로 기록한다. 수첩·확신도·설문은 실제 Mongo 보존·새 프로세스 재시작·경합과 TLS Kafka `paldo.player` 수신을 확인했다. 응답 유실은 독립 저장 계획과 고정 이벤트 키로 복구한다. 전송 재생은 event_id로 중복 제거한다. [저장·전송 범위](../../server/DESIGN.md#b-07-02-인수계약-확인) 슬라이더와 저장 안내는 로컬 Alpine.js 3.14.9로 동작한다.

미리보기는 임시 SQLite와 녹화 엔진, 메모리 게임 저장소를 사용한다. 현재 C 저장소는 `paldo_web/mongo.py`의 명시적 조립으로 연결하며 회차 정책도 같은 자원에 고정한다. 기존 사용자 정의 팩토리는 `prepare_start`를 비워 두면 기본 시작 정책(회차 없으면 일반 판, 있으면 B 회차 정책, `server/app/open_start.py`)을 사용한다. 운영 게임 서비스는 `server/infra/game_factory.py`(A-07-03)가 이 실행 자원(`open_mongo_runtime`)에 실제 엔진(NPC 규칙 v0, 콘텐츠 버전별)과 규칙 의심도 기반 가짜 의심 모델을 넘겨 만들고, 그 게임 서비스에 보조 기록 Mongo 저장소(`server/infra/interaction_store.py`, `<접두어>b_interactions`), 기준선 해석기, 템플릿 렌더러를 끼운다. 보조 기록 저장은 같은 프로세스 안에서 판별 잠금, 프로세스 사이에서 판 문서 임대(30초, 요청이 도는 동안 10초마다 연장, 최대 5초 대기)로 직렬화하고, 임대를 5초 안에 못 잡거나 만료 뒤 늦은 저장은 503으로 거절하며 덮어쓰지 않는다. Mongo 일시 오류 중에는 연장을 다시 시도한다. Mongo가 임대 시간(30초) 내내 응답하지 않으면 연장이 끊겨 다른 프로세스가 임대를 잡을 수 있다(그동안 같은 Mongo에 쓰는 게임 저장도 실패하며, 게임 명령은 판 저장소 버전 검사로 한 번만 반영된다). 배포 계정 DB 권한, 운영 게임 저장소, 실제 광고 요청의 식별자는 통합 환경에서 확인한다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-09-07 | 11/9 | 자유 입력 카드 단계 거르기(투표·결선 의심 → 투표 카드, 0장이면 현재 단계 행동 안내), 아침·밤 입력 칸 비활성, 심문 양식 진술 체크 → 밤·시간 맞춤·직전 대상·질문 유지, 명령 패널·자유 입력 화면 아래 고정 막대(`.command-bar`), 설문 없이 떠난 끝난 판의 건너뜀 보완(다음 홈 요청·18:00 마감 직후 관리 명령 `backfill_surveys`), 기능 없는 체크박스 정리(선택형 도전·자유 입력 진술 선택 삭제, 진술 선택은 쓰는 명령에만 접힌 `details.evidence-picker`) |
| E-09-08 | 11/9 | `server/app/progress.py` `apply_progress`(고백 단서·한풀이 해금 멱등 반영)를 `GameService.command` 저장 직후에 연결(진척 저장 실패는 `paldo.progress` 오류 기록만, 복구는 재처리), 판 시작 결말 검사를 `ending_available`로(퇴마는 진척 문서와 무관하게 허용) |
| B-10-02 | 11/9 | manifest 고정 지도·장소 이름·전체 초상 13종·출처 연결. 좌표/출처 갱신·읽기 전용 회귀, Chrome 모바일·확대·완주와 용량·첫 로딩 기록 |
| B-05-01 | 11/5 | Django 설정, 상태 확인, 요청 로그와 계측 |
| B-05-02 | 11/5 | 게임 JSON API 열 개, 입력 DTO 검증, 가짜 엔진 회귀 시험 |
| B-05-03 | 11/5 | 회의 전체·조각 화면, htmx 행동 폼, manifest 이미지 연결 |
| B-06-01 | 11/6 | 진술표, 투표·결선·감금 공개, 밤 점 대상 화면 |
| B-06-02 | 11/6 | 실제 Compose Mongo에서 같은 ID 재생·버전 충돌·거부·20개 동시 명령 검증. 10명·3분 측정 CLI와 루트 `.env` 연결. 최신 dev 합성 규칙 버전의 회차 등록·판 메타 보존 회귀, 수치는 `perf.md` 참고 |
| B-06-03 | 11/6 | 최신 dev 실제 Mongo 8개·MySQL 156개 재검증, 완료 조건과 통합 보류 범위 구분.  운영자 회차 관리·고정 판 배정·광고 가명. C 저장소 연결·실제 엔진 재기동, Mongo 저장 응답 유실의 1회 재시도·중복 방지 검증 |
| B-06-04 | 11/6 | 가입·로그인·잠금·계정 저장소 검증. 11/8 인증 실패·판 완주와 광고 포트 DTO 4건의 인증 정보 비노출 회귀 보강. 실제 HTTP 광고 수신은 B-08-03 연결 뒤 검증, VM DB 별도 |
| B-07-05 | 11/7 | ASGI·인증 웹소켓·광고 중계·재연결과 P-29. 11/8 실제 HTTP WSGI·Daphne 및 광고·비콘 혼합 부하 도구 추가. 최초 Mongo majority 지연 실패·반복 통과·최종 코드 재측정 실패를 보존. B 중복 조회 개선 후 동일 조건 통과. 과거 DB 지연 재발 여부는 운영에서 C와 확인. 대상 VM 검증 보류 |
| B-07-01 | 11/7 | 자유 입력 카드의 모든 반응·실패 화면, 해석기 선택·계측, 발언 폴링과 단계 취소. 신규 조건 8의 Kanana 답변 표기·폴링/재조회 유지 검증 |
| B-07-02 | 11/7 | 실제 Mongo·프로세스 재시작·TLS Kafka 수신, 응답 유실 복구 계획·동일 이벤트 중복 방지 보완.  수첩·확신도·설문과 전체 건너뛰기 화면, P-29 입력·재전송 검증. 날짜·단계별 동일 확신도 기록과 5단계 확신도 버튼 보완. 실제 토픽 수신 완료 |
| A-07-03 | 11/7 | 운영 게임 서비스 조립 `server.infra.game_factory.build_game_service`(B 실행 자원 위에 실제 엔진·의심 모델·보조 기록 저장·기준선 해석기·템플릿 렌더러)과 로컬 실행 순서 |
| B-07-03 | 11/7 | 로비·결과 광고 지연 로드, 3지면 조각·가명 분리·serve별 노출 비콘·동일 출처 클릭 검증. 미제공·실패의 200 빈 조각 검증. 복기 배치는 B-10-01 범위 |
| E-08-14 | 11/8 | 회차 없는 일반 판 시작(서버 랜덤 시드, 공개 프리셋, 배포 버전, 시작 UUID 중복 방지), 홈 새 게임·이어 하기, 기본·Mongo 조립 모두 시작 정책 연결. 회차 판 경로 유지 |
| B-08-02 | 11/8 | 공개 프리셋 파일과 일반 판 연동, 무당 초상 선택·기본값, 공개 8인 녹화 15화면·렌더 측정, 거절 코드 로그·심문 양식 제한·PM 결정의 Kanana 표기 제거. 현재 dev의 6인 공동 완주·E 재확인은 대기이며 태그는 선행 조건이 아님. 11/10 태그 리허설은 B-09-03 |
| B-08-01 | 11/8 | 공개 결과·수집 단서·결말 선택 화면. 11/9 최신 dev에서 일반 판·고정 회차에 연결하고 시작 UUID·초상·첫 결말 보존을 재검증. 콘텐츠 버전별 문안 보관·갱신 후 기존 결말/단서 유지 회귀. 11/9 판 종료 화면 안 결과·복기·원한 단서 탭(조각 지연 로드), 공개된 선택형 도전 목록, 퇴마 상시 선택 |
| B-10-01 | 11/9 | 복기 탭 `review_panel.html`: 종료 뒤에만(그 전 403) 정체·숨긴 비밀·이 판 단서와 `review_side` 지면 1개. 행동 이유·해설·승률 그래프는 E-10-10 병합 뒤 |
| B-08-03 | 11/8 | 내부 HTTP 제공자·환경 조립, 재시도 없는 4경로·헤더 분리·실패 화면 회귀와 로컬 장애 시험 CLI, 실패 후 홈의 요청 내 진척·판 조회 재사용. D 인수·5분 장애 시험·실제 배치는 대기 |
| B-10-07 | 11/9 | 창귀 UI 인계·v5 전신/초상·읽기 보완·classic/vn 설정·PM 검토, 전체 완료 아님 |
| E-09-10 | 11/9 | 게임 웹 Daphne 2개(8000·8002)와 Caddy `least_conn`·`/healthz` 능동 확인, 채널 레이어 설정 전환(`memory` 기본·`redis` 선택, 연결 실패 시 기동 거부), 프로세스별 로그 파일, 비교 부하 `tests/multiproc_bench.py`(`api_bench.py --web-processes`·`--front`·`--result-name`), 동시성 구조와 한계(server/DESIGN.md) |

B-06-03의 실제 Mongo 조립은 `server/paldo_web/mongo.py`를 사용한다. 엔진·모델을 명시적으로 넘겨 `open_mongo_runtime`을 호출하고 HTTP 처리 전 `configure()`로 연결한다. 기존 `PALDO_GAME_SERVICE_FACTORY` 함수 안에서는 `factory_service()`를 반환한다. 고정된 콘텐츠 버전·같은 고정 판을 복원한다. 접속 정보는 기존 `PALDO_MONGO_URI` 하나를 사용한다. 고유 컬렉션의 실제 시험은 `server/tests/mongo_game_sessions.py`이며 B-06-03의 회차·가명·동의 재검증은 완료했고 운영 조립·PM 브라우저 재개 판정도 병합됐다. VM·ASGI 성능은 B-07-05, 실제 광고 요청은 B-08-03에서 관리한다. 보조 저장의 실제 검증은 B-07-02 증빙에 있다.

B-08-01 콘텐츠 문안은 `server/story_catalog.json`에 버전별로 보관한다. E 콘텐츠 갱신 시 새 버전 공개 문안을 추가하고 기존 버전은 유지한다. 게임은 메타 버전, 진척은 진척 버전으로 조회해 콘텐츠 갱신 후에도 원문을 보존한다. 현재 원문과 보관본의 일치·버전 갱신 전후 화면 보존은 `server/tests/test_story_ui.py`에서 검사한다.

B-08-03은 PALDO_ADS_INTERNAL_URL을 주입하면 기존 광고 Gateway에 내부 HTTP 제공자를 연결한다. 상세 운영/시험 및 D 인수 범위는 [서버 설계](../../server/DESIGN.md#b-08-03-내부-광고-http-연결)와 [증빙](../evidence/B-08-03.md)을 따른다. 루트 .env.example의 기존 키를 사용하며 실제 접속값은 기록하지 않는다.

C-08-01은 모델 계측과 서버 예외 종류를 기록하며 검증 부하 도구를 제공한다.
