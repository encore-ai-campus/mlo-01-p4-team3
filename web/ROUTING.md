# 회의 화면 라우팅

| 요청 | 화면 또는 파일 | 동작 |
|---|---|---|
| `POST /games` | `meeting.html` | JSON DTO로 판을 시작한다. htmx면 회의 조각을 반환한다. |
| `GET /games/{id}` | `game.html` → `meeting.html` | 일반 브라우저는 전체 화면, htmx는 회의 조각, JSON 요청은 DTO를 받는다. |
| `POST /games/{id}/commands` | `meeting_actions.html` → `meeting.html` | htmx 명령 폼은 `#meeting`을 교체한다. |
| `GET /static/assets/…`, `GET /static/ads/…` | `content/images/manifest.json` | 등록된 이미지 파일을 제공한다. |
| `GET /static/game.css`, `GET /static/js/…` | `web/static/` | 화면 스타일과 행동 폼 스크립트를 제공한다. |

- [서버 경로와 모듈 위치](../docs/routing/B.md)
- [회의 화면 설계](DESIGN.md)
