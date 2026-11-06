# A 엔진 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 판 생성·배정·진행 규칙, 엔진 포트.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/engine.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/A_engine.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 A 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, A 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `engine/domain/` | 도메인 | A | 규칙, 상태, 이벤트, 콘텐츠 타입, 배정 검증기(`model.py`, `content.py`, `events.py`, `serde.py`), 엔진 구현(`engine.py`), 밸런스 상수(`params.py`) |
| `engine/ports.py` | 도메인 경계 | A(정의) | 엔진이 바깥에 기대는 인터페이스 |
| `tools/run_engine_games.py`, `tools/check_handoff_a06.py` | 표현·응용 | A | 실제 엔진 판 실행 → 이벤트 jsonl(`EventSink` 파일 구현), D·C 인수 조건 검사 |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 게임 규칙 변경 | 도메인 `engine/domain/model.py`, `docs/contracts/domain.md` |
| 판 생성·진행 규칙 구현, 목격 확률 등 밸런스 상수 | 도메인 `engine/domain/engine.py`, `engine/domain/params.py` |
| D·C용 엔진 판 이벤트 생성·검사, NPC 정책 선택지 추가 | 표현·응용 `tools/run_engine_games.py`(`POLICIES`), 검사 `tools/check_handoff_a06.py`, 정책 규칙은 도메인 `engine/domain/` |
| 엔진과 바깥의 인터페이스 변경 | `engine/ports.py`, `docs/contracts/ports.md` |
| 아침 실종 발표·피해 장소 공개, 알리바이 명제 | `engine/domain/model.py`(`DisappearanceRecord`, `alibi_propositions`), `docs/contracts/domain.md` 6절 |
