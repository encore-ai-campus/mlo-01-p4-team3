# C 인프라·파이프라인·운영 영역 안내

루트 [`README.md`](../../README.md) "영역 안내"에서 들어오는 2단 라우팅 문서다. 범위: 저장소 어댑터, Kafka → Spark → Delta, Compose·VM·런북·HTTPS 프록시.

- 위치와 역할만 적는다. 상세는 모듈 문서 `docs/modules/infra.md`, `docs/modules/pipeline.md`, `docs/modules/ops.md`(아직 없는 문서는 그 모듈을 처음 바꾸는 날 생긴다. 색인 [`docs/modules/README.md`](../modules/README.md)), 오늘 할 일은 `docs/tasks/C_infra.md`.
- 계층 규칙과 의존 방향은 루트 README "계층과 의존 방향", 파일 위치 원본은 [`docs/contracts/structure.md`](../contracts/structure.md).
- 이 문서는 C 흐름이 관리한다. 다른 흐름이 이 영역에서 작업하며 안내가 필요하면 직접 행을 넣고, C 흐름이 PR에서 확인한다. 행은 기존 분류와 순서를 지켜 관련 항목 근처에 넣는다(`docs/tasks/README.md` 3절 "여럿이 고치는 파일").

## 폴더 안내

| 폴더 | 계층 | 담당 | 역할 |
|---|---|---|---|
| `server/infra/` | 인프라 | C | Mongo 저장소, 아웃박스 발행기, 모델·LLM 어댑터(예정) |
| `server/infra/` Mongo | 인프라 | C | 판 저장 `mongo_repo.py`, 진척 `progress_store.py`, 버전 콘텐츠 `content_store.py`. 실제 Replica Set 시험은 `tests/infra/test_mongo_repo.py`, 콘텐츠 적재는 `tools/load_content.py` |
| `pipeline/`, `ops/` | 인프라 | C | Kafka → Spark → Delta, 배포·관측, HTTPS 프록시 `ops/proxy/`(예정) |
| `pipeline/spark/` | 인프라(배치) | C | 공식 게임 봉투 검증(`bronze_contract.py`), Kafka → Delta bronze 멱등 적재(`ingest_bronze.py`). 사용법·미완 범위는 `pipeline/README.md` |
| `ops/compose/` | 인프라 | C | 로컬 개발용 Compose(`dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`), 패키지 확인(`pycheck.py`), 팀원 절차·점검표(`README.md`) |
| `ops/` VM 점검 | 인프라(운영) | C | VM 점검표 `inventory_check.md`, 호스트 점검 `inventory_host.ps1`(노트북 Windows), VM 안 점검 `tools/perf/vm_probe.sh` |
| `ops/runbook/` | 인프라(운영) | C | 실행 시간표 `schedule.md`, VM 역할·3차↔4차 이름·디스크 예산 `vm_roles.md` |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 광고·계정 MySQL 생성·권한 분리·복제 점검 | `ops/mysql/README.md`, `provision.py`, `verify.py`, `replication_check.py` |
| 저장소나 Kafka 교체 | 인프라 `server/infra/`, `docs/contracts/ports.md` 2절 |
| Mongo 계약·CAS·콘텐츠 적재 확인 | `tests/infra/test_mongo_repo.py`, `tools/perf/mongo_repo_bench.py`, `tools/load_content.py` |
| 공식 게임 토픽 bronze 적재·재처리 | `pipeline/README.md`, `pipeline/spark/ingest_bronze.py`, 계약 단위 테스트 `tests/contract/test_pipeline_bronze.py` |
| 로컬 개발 환경 띄우기·Python 패키지 설치 | `ops/compose/README.md`, `requirements/` |
| VM 상태 점검(기동·자원·디스크·스냅샷) | `ops/inventory_check.md`(방법과 20대 표), `ops/inventory_host.ps1`, `tools/perf/vm_probe.sh` |
| RunPod GPU 실험·자원 확정 조건 | `ops/runbook/runpod.md`, `vm_roles.md`, `schedule.md` |
| 시간대별 VM 사용(사용자 테스트·학습·LLM), VM 역할·디스크 예산 | `ops/runbook/schedule.md`, `ops/runbook/vm_roles.md` |
| HTTPS·인증서 경고 안내 | `ops/proxy/`(C-07-04), `docs/contracts/integration.md` 5절 |
