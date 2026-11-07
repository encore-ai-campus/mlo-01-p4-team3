# 서버 라우팅

`GET /games/{id}/renders/{job_id}`의 htmx 응답은 공개 추궁 답변의 발언 ID·작업 ID를 대조해 `speech_line.html`에 Kanana 표기를 포함한다. 최초·재조회 화면은 `viewmodels.py`의 같은 공개 발언 판정으로 `meeting.html`에 표시한다. JSON 응답 DTO는 기존 형식을 유지한다.

요청은 [paldo_web/urls.py](paldo_web/urls.py)에서 계정·관리자, 게임 API, 상태 확인과 정적 파일 경로로 나뉜다.

| 요청 | 처리 경로 |
|---|---|
| `GET /` | [accounts/views.py](accounts/views.py)의 계정 메뉴. 자기 가명 ID와 동의한 참가자의 다음 고정 시드 판을 표시한다. |
| `GET·POST /accounts/signup`, `GET·POST /accounts/login`, `POST /accounts/logout` | [accounts/urls.py](accounts/urls.py) → [accounts/views.py](accounts/views.py). 가입은 가명과 동의를 저장하고, 로그인과 로그아웃은 Django 세션을 관리한다. |
| `GET·POST /admin/login/`, `/admin/` | 로그인은 일반 계정과 같은 [잠금 규칙](accounts/lockout.py)을 통과한다. 관리자 화면은 Django admin이 제공한다. |
| `GET·POST /admin/sessions` | [api/sessions.py](api/sessions.py) → [app/test_sessions.py](app/test_sessions.py) → [infra/test_sessions.py](infra/test_sessions.py). 운영자가 고정 회차를 조회하고 등록한다. |
| `POST /games` | [api/views.py](api/views.py) → [api/consent.py](api/consent.py)에서 읽은 가명·동의로 [판 시작](app/usecases.py)을 요청한다. 운영 조립은 회차 배정 정책을 연결한다. htmx는 입력 형식에 관계없이 200 회의 조각을 받고, 일반 HTML 폼은 302로 이동한다. |
| `/games/{id}/…`, `/players/me/progress` | [api/urls.py](api/urls.py) → [api/views.py](api/views.py)·[api/interactions.py](api/interactions.py) → [app/](app/). |
| `POST /games/{id}/utterances`, `POST /games/{id}/utterances/{attempt_id}` | [api/interaction_forms.py](api/interaction_forms.py)가 폼을 공통 DTO로 변환한다. [api/interactions.py](api/interactions.py) → [app/interactions.py](app/interactions.py). [paldo_web/composition.py](paldo_web/composition.py)가 선택한 해석기를 조립할 때 주입한다. JSON은 DTO, htmx는 카드 또는 현재 회의 조각이다. |
| `GET /games/{id}/renders/{job_id}` | [app/interactions.py](app/interactions.py)의 소유권·완료 상태 검사 뒤 JSON 또는 `speech_line.html`을 반환한다. `speech_line.html`은 발언만 갱신하고 완료하면 폴링을 멈춘다. 회의 진술표는 [api/meeting_view.py](api/meeting_view.py)가 공개 주장으로 만든다. |
| `PUT /games/{id}/notebook` | [api/participant_forms.py](api/participant_forms.py)가 JSON·폼을 `SaveNotebookInput`으로 검증한다. `InteractionService.notebook`이 의심도·투표 확신도를 저장하고 204를 반환한다. 동일 값은 같은 날짜·단계에서만 생략하며 다음 날·결선은 별도 기록한다. |
| `POST /games/{id}/survey` | `SubmitSurveyInput`의 응답·전체 건너뛰기를 검증하고 종료 후 저장한다. 최초 JSON은 204, 최초 htmx는 200 `survey_done.html`을 반환한다. 같은 요청은 204이며 htmx는 화면을 새로 읽는다. 다른 내용은 409, 잘못된 입력 조합은 400이다. |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | [paldo_web/probes.py](paldo_web/probes.py). |
| `GET /static/…` | [api/assets.py](api/assets.py). |
| `/ws/game/` | [paldo_web/asgi.py](paldo_web/asgi.py)의 출처·세션 인증 → [api/routing.py](api/routing.py) → [api/consumers.py](api/consumers.py). `hello`의 폐기 사유는 [api/ad_metrics.py](api/ad_metrics.py)에 집계하고, `ad.impression`은 [app/ad_events.py](app/ad_events.py)로 중계한다. |
| `GET /ads/click?t=…`, `GET /ads/landing/{creative_id}` | [api/ad_relay.py](api/ad_relay.py) → [app/ad_events.py](app/ad_events.py) → [app/ads_port.py](app/ads_port.py). 실패 시 홈 이동 또는 돌아가기 링크를 제공한다. |
| `GET /ads/slot/{slot}` | [api/ad_slots.py](api/ad_slots.py) → B-07-05의 광고 게이트웨이 조립 → `AdGateway.fetch`. 게임 명령과 분리해 광고 HTML 조각만 반환한다. 미제공·실패는 200 빈 조각이다. |

- [전체 API 계약](../docs/contracts/api.md)

자유 입력의 직전 상대는 응용이 공개 캐묻기 발언에서 보완한다. 재입력 경로도 시도에 보존된 선택 인물·근거·직전 상대를 전달하며, 확인 요청은 선택한 카드의 누락 슬롯과 근거 모호성만 검사한다.
- [서버 설계](DESIGN.md) · [서버 시험](tests/README.md) · [웹 화면 라우팅](../web/ROUTING.md)
