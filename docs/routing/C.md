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
| `server/infra/` 운영 조립 | 인프라(조립) | A(A-07-03) | 웹 게임 서비스 조립 `game_factory.py`(`PALDO_GAME_SERVICE_FACTORY`): B 실행 자원 `server/paldo_web/mongo.py`에 실제 엔진·의심 모델을 넘기고 보조 기록·해석기·템플릿 렌더러를 끼운다. B 보조 기록 Mongo 저장 `interaction_store.py`(`b_interactions`). 시험 `server/tests/test_game_factory.py` |
| `pipeline/`, `ops/` | 인프라 | C | Kafka → Spark → Delta, 배포·관측, HTTPS 프록시 `ops/proxy/`(예정) |
| `pipeline/spark/` | 인프라(배치) | C | 공식 게임 봉투 검증(`bronze_contract.py`), Kafka → Delta bronze 멱등 적재(`ingest_bronze.py`). 사용법·미완 범위는 `pipeline/README.md` |
| `ops/compose/` | 인프라 | C | 로컬 개발용 Compose(`dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`), 패키지 확인(`pycheck.py`), 팀원 절차·점검표(`README.md`) |
| `ops/` VM 점검 | 인프라(운영) | C | VM 점검표 `inventory_check.md`, 호스트 점검 `inventory_host.ps1`(노트북 Windows), VM 안 점검 `tools/perf/vm_probe.sh` |
| `ops/runbook/` | 인프라(운영) | C | 실행 시간표 `schedule.md`, VM 역할·3차↔4차 이름·디스크 예산 `vm_roles.md`, 사용자 테스트 웹 장애 대처 `user_test_incident.md`(현장 진행 E, 서버 조치 C) |

## 작업별 위치

| 하려는 일 | 볼 곳 |
|---|---|
| 광고·계정 MySQL 생성·권한 분리·PJ4 복제·앱 실행 | `ops/mysql/README.md`, `provision.py`, `verify.py`, `pj4_channel.py`, `replication_check.py --channel pj4`, `service_runtime.py` |
| 공식 토픽·게임 Outbox 발행 | `pipeline/topics.yaml`, `pipeline/apply_topics.py`, `server/infra/outbox_publisher.py`, `pipeline/consumers/dedup.py`, `tests/infra/` |
| 저장소나 Kafka 교체·장애 후 대조 | 인프라 `server/infra/`, `docs/contracts/ports.md` 2절, `tools/reconcile_game_events.py`, `pipeline/spark/export_faults.py`, `server/tests/live_stack.py` |
| 웹 운영 조립(엔진·의심 모델·해석기·렌더러·보조 기록 연결) 바꾸기 | 인프라 `server/infra/game_factory.py`. Mongo 연결·컬렉션은 B 실행 자원 `server/paldo_web/mongo.py`, 웹 조립 경계 `server/paldo_web/composition.py`, 로컬 실행 순서 [`docs/modules/web.md`](../modules/web.md) 사용법 |
| Mongo 계약·CAS·콘텐츠·전용 DB 운영 전환 | `tests/infra/test_mongo_repo.py`, `tools/perf/mongo_repo_bench.py`, `tools/load_content.py`, `ops/provision_mongo.py`, `ops/mongo.md` |
| 공식 게임·광고 bronze·silver·학습 export·재처리 | `pipeline/README.md`, `pipeline/spark/{ingest_bronze,build_silver,join_labels,verify_run}.py`, `pipeline/install_bronze_service.py`, 계약 테스트 `test_pipeline_bronze.py`·`test_pipeline_silver.py` |
| 로컬 개발 환경 띄우기·Python 패키지 설치 | `ops/compose/README.md`, `requirements/` |
| VM 상태 점검(기동·자원·디스크·스냅샷) | `ops/inventory_check.md`(방법과 20대 표), `ops/inventory_host.ps1`, `tools/perf/vm_probe.sh` |
| RunPod GPU 실험·자원 확정 조건 | `ops/runbook/runpod.md`, `vm_roles.md`, `schedule.md`. E-11-12 계정별 Pod 실행(E가 네 계정 운영, C는 자기 계정 결제 설정·API 키 전달만, C-11-02 완료 조건)·사용량 제한 체크리스트는 `ops/runbook/gpu_demo.md`, 감시·Pod 스크립트는 `llm/bench/watch_pods.py`·`run_pod.sh` |
| 시간대별 VM 사용(사용자 테스트·학습·LLM), VM 역할·디스크 예산 | `ops/runbook/schedule.md`, `ops/runbook/vm_roles.md` |
| HTTPS·인증서 경고·브라우저·프록시 지연 확인 | `ops/proxy/{README.md,Caddyfile,browser_check.py,header_probe.py}`, `tools/perf/api_bench.py --proxy-profile`, `server/tests/proxy_bench.py` |

## C-08-03 작업 위치

| 작업 | 위치 |
|---|---|
| C-08-03 작업 | ops/k3s/ 설치·Job·이미지와 pipeline/simulation.py → pipeline/sinks/kafka_sink.py. 이미지 빌드는 images/sim/Dockerfile. |
