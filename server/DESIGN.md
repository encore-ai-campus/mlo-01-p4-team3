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

## 검증 경계

계정 기능은 격리 MySQL에서 검증했다. 고정 판 배정은 실제 엔진과 녹화 엔진으로 시험하고, B 회차 저장·광고 가명 대응표는 [로컬 Mongo 시험](tests/README.md)으로 확인한다. C의 게임·진척 저장소를 연결한 운영 판, VM 계정 DB, 실제 광고 요청은 통합 환경에서 확인한다.

- [서버 라우팅](ROUTING.md) · [계정 운영 절차](../docs/modules/web.md#사용법)
- [개인정보 계약](../docs/contracts/privacy.md) · [통합 계약](../docs/contracts/integration.md)
