# 웹 화면 라우팅

자유 입력에서 인물을 생략하면 서버가 공개 기록의 직전 캐묻기 상대를 전달한다. 재입력은 기존 선택 문맥을 유지한다. 다중 카드의 확인 버튼은 카드마다 활성 여부를 판단한다.

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
| `POST /games/{id}/utterances` | `utterance_input.html` → `speech_cards.html` | 공개 인물·근거와 자유 입력을 보내 카드 0~N장을 표시한다. 모호한 근거는 후보 주장 중 두 개를 선택해 수정할 수 있다. 빈 입력은 204로 현재 화면을 유지한다. |
| `POST /games/{id}/utterances/{attempt_id}` | `speech_cards.html` → 카드 또는 `meeting.html` | 확인·수정은 한 명령을 실행한다. 재입력·취소·버튼 전환·해당 없음은 소비하지 않는다. 버튼 전환은 `reaction=switch_to_buttons`를 전송해 시도를 닫는다. 오래된 카드 실행은 409 현재 회의로 교체한다. |
| `GET /games/{id}/renders/{job_id}` | `speech_line.html` | 1초 간격으로 발언 영역의 해당 문장만 교체한다. 완료되거나 단계가 바뀌면 폴링을 멈춘다. 진술표는 `meeting.html`에서 공개 주장으로 표시한다. |
| `GET /static/assets/…`, `GET /static/ads/…` | `content/images/manifest.json` | 등록된 이미지 파일을 제공한다. |
| `GET /static/game.css`, `GET /static/js/…` | `web/static/` | 화면 스타일과 행동 폼 스크립트를 제공한다. |
| `/ws/game/` | `static/js/paldo_ws.js` | 로그인된 전체 페이지에서 연결 하나를 열고 광고 비콘을 전송한다. 연결·재연결 때 `hello`로 대기열 초과와 페이지 이탈의 폐기 수를 보낸다. 게임 요청은 기존 HTTP를 유지한다. |
| `GET /ads/click`, `GET /ads/landing/{creative_id}` | 서버 광고 중계 | 클릭은 같은 출처 랜딩으로 이동하고 실패하면 홈으로 돌아간다. 랜딩 실패에는 돌아가기 링크를 표시한다. |

- [서버 라우팅](../server/ROUTING.md)
- [웹 화면 설계](DESIGN.md)
