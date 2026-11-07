# 웹 화면 라우팅

| 요청 | 화면 또는 파일 | 동작 |
|---|---|---|
| `GET /` | `accounts/home.html` | 가입·로그인 메뉴를 제공한다. 로그인 뒤 자기 가명 ID와 로그아웃 폼, 배정된 다음 판을 표시한다. |
| `GET·POST /accounts/signup` | `accounts/signup.html` | 가입 폼과 동의를 표시한다. 가입에 성공하면 계정 메뉴로 이동한다. |
| `GET·POST /accounts/login`, `GET·POST /admin/login/` | `accounts/login.html` | 두 경로가 같은 폼과 로그인 잠금 규칙을 쓴다. |
| `POST /accounts/logout` | `accounts/login.html` | 세션을 폐기하고 로그인 화면으로 이동한다. |
| `GET /admin/` | `admin/base_site.html` | 관리자 화면에 로그인 ID 대신 가명 또는 운영자 표시를 사용한다. |
| `GET·POST /admin/sessions` | `test_sessions.html` | 운영자가 회차와 고정 시드 목록을 등록하고 조회한다. |
| `POST /games` | `accounts/home.html`의 시작 폼 → `GET /games/{id}` 또는 `meeting.html` | 일반 HTML 폼은 302로 회의 화면으로 이동한다. htmx는 폼·JSON 입력 모두 200 회의 조각을 바로 받는다. JSON 응답 요청은 게임 DTO를 받는다. |
| `GET /games/{id}` | `game.html` → `meeting.html` | 전체 화면 또는 회의 조각에 진술표·공개 투표·감금 결과·본인 점괘를 표시한다. JSON 요청은 DTO를 받는다. |
| `POST /games/{id}/commands` | `meeting_actions.html` → `meeting.html` | 대상별 투표·결선·점치기 버튼과 낮의 점괘 공개 폼이 명령을 보내면 htmx가 `#meeting`을 교체한다. |
| `GET /static/assets/…`, `GET /static/ads/…` | `content/images/manifest.json` | 등록된 이미지 파일을 제공한다. |
| `GET /static/game.css`, `GET /static/js/…` | `web/static/` | 화면 스타일과 행동 폼 스크립트를 제공한다. |
| `/ws/game/` | `static/js/paldo_ws.js` | 로그인된 전체 페이지에서 연결 하나를 열고 광고 비콘을 전송한다. 연결·재연결 때 `hello`로 대기열 초과와 페이지 이탈의 폐기 수를 보낸다. 게임 요청은 기존 HTTP를 유지한다. |
| `GET /ads/click`, `GET /ads/landing/{creative_id}` | 서버 광고 중계 | 클릭은 같은 출처 랜딩으로 이동하고 실패하면 홈으로 돌아간다. 랜딩 실패에는 돌아가기 링크를 표시한다. |

- [서버 라우팅](../server/ROUTING.md)
- [웹 화면 설계](DESIGN.md)
