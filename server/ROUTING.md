# 서버 라우팅

요청은 [paldo_web/urls.py](paldo_web/urls.py)에서 계정·관리자, 게임 API, 상태 확인과 정적 파일 경로로 나뉜다.

| 요청 | 처리 경로 |
|---|---|
| `GET /` | [accounts/views.py](accounts/views.py)의 계정 메뉴. 자기 가명 ID와 동의한 참가자의 다음 고정 시드 판을 표시한다. |
| `GET·POST /accounts/signup`, `GET·POST /accounts/login`, `POST /accounts/logout` | [accounts/urls.py](accounts/urls.py) → [accounts/views.py](accounts/views.py). 가입은 가명과 동의를 저장하고, 로그인과 로그아웃은 Django 세션을 관리한다. |
| `GET·POST /admin/login/`, `/admin/` | 로그인은 일반 계정과 같은 [잠금 규칙](accounts/lockout.py)을 통과한다. 관리자 화면은 Django admin이 제공한다. |
| `GET·POST /admin/sessions` | [api/sessions.py](api/sessions.py) → [app/test_sessions.py](app/test_sessions.py) → [infra/test_sessions.py](infra/test_sessions.py). 운영자가 고정 회차를 조회하고 등록한다. |
| `POST /games` | [api/views.py](api/views.py) → [api/consent.py](api/consent.py)에서 읽은 가명·동의로 [판 시작](app/usecases.py)을 요청한다. 운영 조립은 회차 배정 정책을 연결한다. htmx는 입력 형식에 관계없이 200 회의 조각을 받고, 일반 HTML 폼은 302로 이동한다. |
| `/games/{id}/…`, `/players/me/progress` | [api/urls.py](api/urls.py) → [api/views.py](api/views.py)·[api/interactions.py](api/interactions.py) → [app/](app/). |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | [paldo_web/probes.py](paldo_web/probes.py). |
| `GET /static/…` | [api/assets.py](api/assets.py). |
| `/ws/game/` | [paldo_web/asgi.py](paldo_web/asgi.py)의 출처·세션 인증 → [api/routing.py](api/routing.py) → [api/consumers.py](api/consumers.py). `hello`의 폐기 사유는 [api/ad_metrics.py](api/ad_metrics.py)에 집계하고, `ad.impression`은 [app/ad_events.py](app/ad_events.py)로 중계한다. |
| `GET /ads/click?t=…`, `GET /ads/landing/{creative_id}` | [api/ad_relay.py](api/ad_relay.py) → [app/ad_events.py](app/ad_events.py) → [app/ads_port.py](app/ads_port.py). 실패 시 홈 이동 또는 돌아가기 링크를 제공한다. |

- [전체 API 계약](../docs/contracts/api.md)
- [서버 설계](DESIGN.md) · [서버 시험](tests/README.md) · [웹 화면 라우팅](../web/ROUTING.md)
