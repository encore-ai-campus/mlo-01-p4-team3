# 모듈 문서

모듈마다 하나씩, "무엇을 · 왜 · 사용법 · 설정값 · 한계 · 변경 이력"을 적는다. 기준과 검사 규칙은 [`docs/tasks/README.md`](../tasks/README.md) 10절, 양식은 [`_template.md`](_template.md).

파일은 그 모듈을 처음 바꾸는 날의 일괄 갱신(흐름별 그날 마지막 태스크의 "모듈 문서 일괄 갱신" 줄)에서 양식을 복사해 만든다. 아래 표의 "코드 위치"는 `check_docs`가 "코드를 바꿨다면" 태스크의 행 필요 여부를 판단할 때 쓴다.

| 모듈 문서 | 코드 위치 | 주 담당 |
|---|---|---|
| `engine.md` | `engine/domain/`, `tools/run_engine_games.py` | A |
| `sim.md` | `engine/sim/` | D |
| `web.md` | `server/api/`, `server/app/`, `web/` | B |
| `infra.md` | `server/infra/` | C |
| `pipeline.md` | `pipeline/`, `tools/reconcile_*.py` | C |
| `ops.md` | `ops/`(Compose, Prometheus·Grafana, k3s) | C |
| `aws.md` | AWS 시연 환경(`ops/aws/`) | C |
| `ml_ops.md` | MLflow, 평가 관문 실행, 재학습 CronJob | C |
| `privacy.md` | `tools/privacy_*.py` | C |
| `intent.md` | `intent/` | D |
| `ml.md` | `ml/` | D |
| `ads.md` | `ads/`, `tools/ad_traffic_sim.py` | D |
| `content.md` | `content/`(인물·비밀·단서·템플릿·의도 예시) | E |
| `llm.md` | `llm/` | E |
| `assets.md` | `content/images/`, `docs/assets/`, `tools/assets/` | E |
| `user_test.md` | 사용자 테스트 운영 절차 | E |
| `ci.md` | `.github/`, `tools/check_*.py` | E |
| `perf.md` | `tools/perf/` | 공용 |
| `project.md` | 저장소 운영(브랜치, 라벨, 이슈 동기화 `tools/tasks_to_issues.py`) | E |
