# 서버 설계

## 계층과 저장소

`api/`는 HTTP 입력을 검증하고 응답을 만든다. `app/`은 판 시작과 명령 실행을 조정한다. 게임 상태와 진행 저장소는 [게임 서비스 조립 경계](paldo_web/composition.py)에 주입한다. 계정 저장소는 Django의 `default` MySQL이며, 접속값은 `PALDO_AUTH_DB_*`로 받는다.

## 계정과 세션

가입 시 로그인 ID를 정규화하고 비밀번호 해시, UUID 가명 ID, 동의 시각을 저장한다. 초대 코드가 설정된 경우에만 가입할 때 검증한다. 일반·관리자 로그인은 같은 잠금 규칙을 쓰며, 로그인 성공 시 세션 ID를 교체한다.

게임 API는 인증된 계정의 가명 ID로 판과 하위 자원의 소유권을 확인한다. 로그인 ID와 비밀번호 해시는 계정 DB에만 저장하며, 인증 로그에는 결과와 확인된 계정의 가명 ID를 쓴다.

## 테스트 회차

판 시작 응답은 htmx 헤더를 확인해 회의 조각을 반환한다. 일반 HTML 폼은 판 조회 화면으로 이동하며, JSON 응답은 게임 DTO를 사용한다.

활성 운영자(`is_staff`)가 회차 ID, 순서 있는 고정 판 목록, 참가자 가명, 규칙·엔진·콘텐츠 버전을 등록한다. 회차 ID는 유일하며 이미 등록한 구성은 덮어쓰지 않는다. 참가자의 첫 미종료 판이 다음 차례다. 시작 요청은 가입 때 발급된 가명으로 참가 배정과 동의를 확인한다. 같은 회차·참가자·순번은 같은 판 ID를 사용한다. 시작 이벤트에는 `source=human`, `session_id`, `player_pseudonym`이 들어간다.

[B 회차 저장소](infra/test_sessions.py)는 `PALDO_MONGO_URI`의 DB(경로 생략 시 `paldo_web`)에 `b_test_sessions`와 `b_ad_identities`를 둔다. 게임 상태·진척은 기존 게임 포트를 사용한다. 회차 저장소를 사용할 수 없으면 운영자 화면과 회차 판 시작은 503을 반환한다.

후속 광고 연동은 [서버 가명 발급 함수](api/sessions.py)에서 로그인 세션별 광고 UUID를 받는다. 대응표에는 플레이어 가명과 광고 UUID, 로그인 세션을 HMAC으로 변환한 내부 키를 둔다. C-09-02는 `ad_identities(player)`로 광고 기록을 찾고, 광고 기록을 삭제한 뒤 `delete_player(player)`로 대응표와 회차 참가 배정을 제거한다.

## 광고 신호와 ASGI

[ASGI 진입점](paldo_web/asgi.py)은 HTTP를 Django에, `/ws/game/`을 전체 출처 검사와 Django 세션 인증 뒤 Consumer에 보낸다. 허용 출처는 `PALDO_PUBLIC_ORIGIN` 하나다. 비로그인은 수락 직후 4401로 닫고, 매 비콘 전에 DB에서 세션·사용자·인증 해시를 새로 읽어 로그아웃·만료·비밀번호 변경을 확인한다. 크기 제한은 UTF-8 바이트 기준 4,096이며 초당 비콘 5개를 넘으면 연결을 유지하고 거부한다.

Consumer는 검증과 응답만 맡는다. 중계는 `ad_events` → `AdGateway` → `AdProvider` 순서이며 `sync_to_async(thread_sensitive=False)`로 동기 호출을 이벤트 루프 밖에서 실행한다. serve와 중계의 호출 상한은 각각 8이고 회로 차단기는 공유한다. 전체 300ms·연결 100ms, 재시도 없음 규칙을 유지한다. HTTP 클릭은 같은 출처 `/ads/landing/<id>`만 허용하며 실패하면 `/`로 이동한다.

광고 제공자는 `configure_ad_gateway`로 명시적으로 주입한다. 운영에서 가짜로 자동 대체하지 않는다. 미조립 상태는 비콘 실패 응답과 HTTP 대체 화면으로 처리한다. 참가자 헤더나 요청 객체는 포트 인자에 전달하지 않는다. 실제 HTTP 클라이언트의 헤더 검증은 B-08-03에서 이어 간다.

`InMemoryChannelLayer`를 사용하므로 Daphne는 루프백 주소의 프로세스 하나로 실행한다. HTTPS 프록시·VM 검증은 C-07-04와 이어 간다. 로컬 Channels·Daphne 시험은 대상 VM 호환 확정이나 실제 Mongo 성능 증빙을 대신하지 않는다.

P-29에 따라 `hello`를 연결당 한 번 받는다. `dropped_by_reason`의 `queue_full`과 `page_exit`는 각각 0 이상 정수이며 합계가 `dropped`와 같아야 한다. 누락·합계 불일치·알 수 없는 사유·중복 보고는 `bad_message`로 응답하고 집계하지 않는다. 지표는 `paldo_ad_beacon_dropped_total{reason}` 하나를 사용한다. `rate_limited`는 서버가 비콘을 빈도 제한으로 거절한 때만 더한다. [웹소켓 계약](../docs/contracts/api.md#7-웹소켓과-광고-중계-114-pm-결정-b-07-05)

## 검증 경계

계정 기능은 격리 MySQL에서 검증했다. 고정 판 배정은 실제 엔진과 녹화 엔진으로 시험하고, B 회차 저장·광고 가명 대응표는 [로컬 Mongo 시험](tests/README.md)으로 확인한다. C의 게임·진척 저장소를 연결한 운영 판, VM 계정 DB, 실제 광고 요청은 통합 환경에서 확인한다.

- [서버 라우팅](ROUTING.md) · [계정 운영 절차](../docs/modules/web.md#사용법)
- [개인정보 계약](../docs/contracts/privacy.md) · [통합 계약](../docs/contracts/integration.md)
