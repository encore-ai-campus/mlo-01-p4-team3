# D ML·광고 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 시뮬레이터, 의도 해석기, 씐 자 확률 모델, 광고 서버.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/sim.md`, `docs/modules/intent.md`, `docs/modules/ml.md`, `docs/modules/ads.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/D_ml_ads.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 D 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, D 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").
- 다른 영역 문서에 있는 D 작업: 광고 비콘·클릭 중계(진입점 B, [`B.md`](B.md)), 실제 에셋 파일·생성 기록(규격·검사와 함께 E, [`E.md`](E.md)).

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `engine/sim/` | 도메인 사용 | D | 플레이어 봇 4종(`bots.py`), GameEngine 포트 실행기(`runner.py`), 결과 지표 표(`report.py`). 결정 층 최적화는 A가 `engine/domain`에서 맡는다 |
| `intent/`, `ml/` | 인프라(모델) | D | 의도 해석기, 씐 자 확률 모델(예정) |
| `ads/domain/` | 광고 도메인 | D | 경매, 2등 가격, 예산 예약·확정·해제, 페이싱, CTR, 유효 판정, 대사, 지면 규격(`SLOT_SIZES`)(표준 라이브러리만) |
| `ads/app/`, `ads/infra/`, `ads/web/` | 광고 응용, 인프라, 표현 | D | 요청·비콘·클릭 흐름, Django 뷰·admin. MySQL 원장·아웃박스·Kafka는 D-06-02 예정 |
| `ads/project/`, `ads/manage.py` | 광고 서비스 설정·진입점 | D | 게임 웹과 독립된 Django 설정·URL·WSGI |
| `tests/ads/` | 서비스 테스트 | D | MySQL 캠페인·admin과 광고 HTTP·예산·판정 검증 |
| `ads/testing/` | 테스트 지원 | D | `FakeAdProvider` |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 모델 교체·승격·롤백 | `docs/contracts/ml_lifecycle.md` |
| 플레이어 봇 실행·같은 시드 재현·지표 표·처리량 측정 | `engine/sim/`, CLI `tools/perf/sim_bench.py`, 테스트 `tests/sim/`, 증빙 `docs/evidence/D-06-01.md` |
| 경매·과금·광고 판정 규칙 변경 | 광고 도메인 `ads/domain/`, `docs/contracts/ads.md` |
| 광고 서버 실행·admin·HTTP 검사·지연 측정 | 표현 `ads/manage.py`·`ads/web/`, 응용 `ads/app/`, 설명 `docs/modules/ads.md`, 측정 `tools/perf/ads_bench.py` |
