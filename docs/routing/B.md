# B 웹 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 게임 웹(Django 뷰·화면·웹소켓)과 응용 유스케이스, 게임 쪽 광고 포트.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/web.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/B_web.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 요청과 파일 연결은 [서버 라우팅](../../server/ROUTING.md)과 [웹 화면 라우팅](../../web/ROUTING.md)에서 본다.
- 이 문서는 B 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, B 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `server/app/` | 응용 | B | 유스케이스와 DTO(`dto.py`) |
| `server/api/`, `web/` | 표현 | B | Django 뷰, 템플릿, htmx 조각, 뷰 모델(`viewmodels.py`), 광고 웹소켓 Consumer(`consumers.py`) |
| `web/templates/`, `web/static/` | 표현 | B | 회의 화면과 htmx 조각, CSS·스크립트·기본 이미지 |
| `server/accounts/` | Django 인증 | B | 계정 폼·가명 프로필·로그인 잠금·운영 명령 |
| `web/templates/accounts/`, `web/templates/admin/` | 표현 | B | 계정 화면과 가명으로 표시하는 관리자 화면 |
| `server/paldo_web/` | Django 프로젝트 | B | 서버 설정, URL 등록, 요청 계측, DB 상태 확인과 Prometheus 지표 |
| `server/tests/` | 테스트 | B | 요청 계측·상태 확인·DB 장애·게임 API·명령 경합 시험 |
| `server/app/ads_port.py` | 응용(게임 쪽) | B | `AdProvider` 포트와 보호 래퍼 `AdGateway` |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 화면이나 API 추가 | 표현 `server/api/`, 응용 `server/app/dto.py`, `docs/contracts/api.md` |
| 게임 JSON API 경로와 실행 흐름 | `server/api/urls.py`, `server/api/views.py`, `server/api/interactions.py` → `server/app/usecases.py`, `server/app/interactions.py` |
| 회의 화면과 에셋 연결 | `server/api/views.py`, `server/api/meeting_view.py`, `server/api/assets.py`, [화면 라우팅](../../web/ROUTING.md) |
| 명령 재전송·동시 요청·부하 사전 시험 | [서버 시험 안내](../../server/tests/README.md) |
| 가입·로그인·로그아웃과 계정 복구 | `server/accounts/urls.py`, `server/accounts/views.py`, `web/templates/accounts/`, [웹 모듈 사용법](../modules/web.md#사용법) |
| 테스트 회차 등록과 고정 판 배정 | `server/api/sessions.py`, `server/app/test_sessions.py`, `server/infra/test_sessions.py`, [서버 라우팅](../../server/ROUTING.md) |
| 광고 지면을 게임 화면에 붙이기 | `server/app/ads_port.py`, `docs/contracts/ports.md` 5절 |
| 광고 비콘·클릭·랜딩 중계 | ASGI `server/paldo_web/asgi.py` → 표현 `server/api/consumers.py`·`ad_relay.py` → 응용 `server/app/ad_events.py` → 포트 `server/app/ads_port.py`. 실제 HTTP 인프라 `server/infra/ad_client.py`는 B-08-03 예정 |
| 웹소켓 재연결·비콘 폐기 사유 | `web/static/js/paldo_ws.js` → `server/api/consumers.py` → `server/api/ad_metrics.py`. 클라이언트·Consumer·실제 Daphne 검증은 [B-07-05 증빙](../evidence/B-07-05.md) |
