# ops (운영 구성)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

인프라 계층의 운영 구성이다. 로컬 개발용 Compose(`ops/compose/`), MySQL TLS/분리·PJ4 복제와 앱 런처(`ops/mysql/`), 내부 HTTPS(`ops/proxy/`)를 제공한다.

- `dev.yml`: Mongo(레플리카셋 1노드), Kafka(KRaft 1노드), MySQL, Garage(S3 호환), Prometheus, Grafana를 한 번에 띄운다. 입력은 저장소 루트의 `.env`(비밀값), 출력은 `127.0.0.1`의 서비스 포트다.
- `run.py`(CLI·흐름·docker/HTTP 호출)와 `devenv.py`(규칙: `.env` 생성 규칙, 점검 판정, S3 서명, 로그 레코드): `.env` 만들기, Garage 초기화, 6개 서비스 기능 확인(스모크).
- `pycheck.py`: 필수 Python 패키지 5개의 import·간단한 동작 확인. `spark/delta_rw.py`: Spark 컨테이너 안에서 Delta 쓰기·읽기 확인.
- 영역별 Python 의존성은 저장소 루트 `requirements/`(web·ml·llm·pipeline)에 둔다.

실제 VM 배포는 기존 보호 Ansible 인벤토리를 저장소 밖에서 사용했다. 공개 코드에는 주소·자격정보가 없다. 중앙 Prometheus 수집 대상·경보·대시보드(C-08-01), k3s(C-08-03)는 후속 작업이다.

## 왜

- 기획서 6.1 "배포": 개발은 각자 PC의 Docker Compose 축소 구성, 통합은 VM. 다섯 흐름이 VM 없이도 실제 저장소·브로커로 개발할 수 있게 한다(`docs/contracts/parallel_dev.md`).
- Mongo를 레플리카셋으로 띄우는 이유: VM(3노드)과 같은 연결 방식(`replicaSet=rs0`, `w=majority`)과 변경 스트림을 로컬에서도 쓰기 위해서다. 원자성은 판 문서 하나에 한 번에 쓰는 것으로 보장하며 다중 문서 트랜잭션은 쓰지 않는다(`docs/contracts/ports.md` 2절 ④).
- S3 호환 저장소로 Garage를 고른 이유: VM `lake-storage-01`과 같은 제품이라 Delta·MLflow 설정을 그대로 옮길 수 있다(기획서 6.3, 11/4 PM 결정).
- 포트를 `127.0.0.1`에만 열고 Mongo·MySQL을 비표준 포트(27117·3316)로 둔 이유: 노트북에 Windows용 Mongo·MySQL이 이미 돌고 있을 수 있다(기준 PC가 그랬다).
- 메모리 상한을 서비스마다 건 이유: 노트북마다 VM 4대가 RAM 약 16GB를 쓴다(기획서 6.3). 기준 PC는 기동 전 여유 RAM이 1.7GB였다.
- 점검 스크립트를 표준 라이브러리만으로 쓴 이유: 패키지를 설치하기 전에도 돌아가야 한다.

## 사용법

운영 기동·재처리·재학습 준비·대사·종료는 [운영 인계](../../ops/runbook/README.md)를 따른다. 다른 흐름의 실제 검수 결과는 런북 마지막 표에 기록한다.

진척 저장 중 장애가 난 판은 보호된 `PALDO_MONGO_URI`를 설정하고 `python -m tools.replay_progress --game-id <판 ID>`로 복구한다. 여러 판은 `--game-id`를 반복하고, 플레이어 단위는 `--player`를 쓴다. 전체 판은 `--all`을 명시해야 한다. 발행 여부와 관계없이 고백·종료 이벤트를 읽으며, 판에 고정된 콘텐츠를 사용한다. 재실행 결과의 신규 단서·결말은 0건이어야 한다. 격리 시험에서만 시험용 컬렉션 `--prefix`를 붙인다.

```
py -3.14 -m ops.compose.run make-env                        # .env 생성(있으면 그대로)
docker compose -p paldo-dev -f ops/compose/dev.yml up -d
py -3.14 -m ops.compose.run garage-init                     # 레이아웃·키·버킷(멱등)
py -3.14 -m ops.compose.run smoke                           # 6/6 성공이면 종료 코드 0
.venv\Scripts\python -m ops.compose.pycheck                 # 필수 패키지 5개
docker compose -p paldo-dev -f ops/compose/dev.yml down     # 이 프로젝트만 내림(볼륨 유지)
```

팀원용 전체 절차, Spark + Delta 확인 명령, 점검표는 [`ops/compose/README.md`](../../ops/compose/README.md).

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `.env`의 `PALDO_*`(integration.md 4.1) | `make-env`가 로컬 값으로 채움(주소·포트는 고정값, 비밀은 무작위, MLflow·LLM·광고 서버 주소와 가입 초대 코드는 비움, 게임 웹 출처 `http://localhost:8000`) | 앱이 읽는 주소·비밀. 양식은 `.env.example`(자리표시자만) | 같은 이름을 VM·AWS에서 값만 바꿔 쓴다 |
| `.env`의 `MONGO_INITDB_ROOT_*`, `MYSQL_*`, `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`, `GF_SECURITY_ADMIN_*` | `make-env`가 무작위로 채움(MySQL 계정은 `PALDO_ADS_DB_USER`·`_PASSWORD`와 같은 값) | 컨테이너 초기화용(이미지가 정한 이름) | 첫 기동 뒤 바꾸면 기존 볼륨 계정과 어긋난다. 볼륨을 지우고 다시 띄워야 한다 |
| `ops/compose/.env` | `make-env`가 루트 `.env`에서 생성 | Compose 변수 치환용(`PALDO_ADS_DB_PORT`만). Compose는 `-f ops/compose/dev.yml`이면 `ops/compose/`의 `.env`만 치환에 쓴다 | 루트 `.env`를 고치면 `make-env` 재실행. 다르면 smoke가 실패한다 |
| `PALDO_KAFKA_PORT` | 9092 | Kafka 호스트 포트(광고 주소에도 쓰임) | 클라이언트 주소 변경 |
| `PALDO_ADS_DB_PORT` | 3316(로컬). 비면 3306(VM·AWS) | MySQL 호스트 포트(11/4 PM 결정) | Compose 포트와 앱 접속 포트가 함께 바뀐다 |
| `PALDO_AUTH_DB_*` | 같은 MySQL(`127.0.0.1`·`PALDO_ADS_DB_PORT`와 같은 값), 스키마 `paldo_auth`, 계정 `paldo_auth_dev`, 비밀번호 무작위 | 계정 저장소. MySQL 첫 기동 때 `dev.yml`의 initdb 스크립트가 스키마와 그 스키마 권한만 가진 계정을 만든다(C-06-03 4번과 같은 모양) | 볼륨이 이미 있으면 바뀌지 않는다. `down -v` 뒤 다시 띄운다. 광고와 같은 이름이면 `make-env`·smoke가 거부한다 |
| `PALDO_PUBLIC_ORIGIN` | `http://localhost:8000`(로컬 개발 값, 계약 포트 아님) | 게임 웹 출처(`ALLOWED_HOSTS`·CSRF·웹소켓 출처 검사) | 브라우저 주소와 같아야 한다. `127.0.0.1`로 접속하면 웹소켓 거부 |
| `PALDO_S3_PORT`, `PALDO_GARAGE_ADMIN_PORT` | 3900, 3903 | Garage S3·관리 API | S3 엔드포인트 변경 |
| `PALDO_PROMETHEUS_PORT`, `PALDO_GRAFANA_PORT` | 9090, 3000 | 관측 UI | 접속 주소 변경 |
| Mongo 포트 | 27117(고정) | 레플리카셋 멤버 주소 `localhost:27117` | 바꿀 수 없다(멤버 주소와 같아야 한다) |
| `mem_limit` | mongo 640m, kafka 1g, mysql 512m, garage 256m, prometheus 256m, grafana 384m | 서비스별 메모리 상한(합계 3.0GB) | 줄이면 OOM 재시작, 늘리면 VM과 RAM 경합 |
| Kafka `KAFKA_LOG_RETENTION_HOURS` | 24 | 보존 1일(tech_stack.md 2절) | 디스크 사용량 |
| Mongo `--wiredTigerCacheSizeGB` | 0.25 | WiredTiger 캐시 | 메모리 사용량 |
| 버킷 | `paldo-lake`, `paldo-mlflow` | Delta·MLflow 아티팩트 | `run.py` `BUCKETS` |
| `PALDO_REDIS_PORT`(profile `redis`) | 6379 | 선택 Redis 채널 레이어(E-09-10). 기본 `up`에서는 띄우지 않는다(`--profile redis up -d redis`) | 접속 주소 변경 |

MySQL 생성·분리·PJ4 named channel 복구와 앱 런처는 `ops/mysql/README.md`, HTTPS 기동/종료·Chrome/Edge 경고·CA 지문·실제 브라우저/지연 검증은 `ops/proxy/README.md`를 따른다. Kafka 발행기는 dev에 병합됐다. 11/8 PJ4 두 스키마만 복구해 Replica 재시작 뒤 새 marker를 읽었고, 기존 PJ3 기본 GTID 채널은 별도 미복구로 보존했다. P-31로 상시 LLM은 없고 RunPod는 E-11-12 GPU 서비스 시연(게임 미연결)만 계획한다.

## 한계

11/8 일괄 갱신: C-08-01 관측은 `ops/prometheus/README.md`, C-08-02 광고 집계는 `pipeline/README.md`, C-08-03 Job은 `ops/k3s/README.md`, C-08-04 태그·조회 계정은 `ops/runbook/user_test_release.md`(각 태스크 PR의 파일)에서 사용법·설정·한계를 확인한다. P-31 runtime LLM 없음, 기준선 모델 표기, 합성/부하 분리, 20번째 VM 비실행과 E/D 인계 대기를 반영했다. 각 PR 병합 완료를 뜻하지 않는다.


- 11/8 웹·광고·Outbox·광고 publish/expire/refund와 공식 bronze를 동시 가동했다. 앱 6개 Python 프로세스는 모두 3.14.4이며 web/ads/outbox/광고 작업자 3개 지표 엔드포인트는 모두 HTTP 200이었다. Spark driver/executor 3.14.4도 C-07-01에서 실측했다. 아래 로컬 PC/컨테이너 기록과 VM 실측 범위는 구분한다.
- 다른 물리 노트북 Chrome에서 실제 인증서 경고 UI Advanced/Proceed → wss 101을 확인했다. 자동 테스트 컨텍스트만 사용했고 OS 신뢰는 바꾸지 않았다. Edge는 안내를 제공하며 직접 실측은 하지 않았다.
- Mongo는 C-06-01 후속 작업으로 기존 관리자 보호 파일을 찾아 정상 인증 후 전용 DB·계정으로 전환했다. 원본 24문서 hash 일치·content-1.0 읽기·다른 DB 거부·새 게임 52건 Kafka/Delta 대조를 확인했다. 운영/롤백은 ops/mongo.md다. B 실제 HTTP 광고 어댑터, C-08-02 광고 대사와 E INT 판정은 별도다.

- 이 결과는 **기준 PC 한 대(11/4)**의 것이다. 다른 노트북은 `ops/compose/README.md` 5절 점검표로 각자 확인해야 한다.
- 이미지 `mongo:8.0.34`·`mysql:8.4.11`은 11/4 PM 결정(MySQL은 11/5 VM 설치 버전에 맞춰 8.4.13 → 8.4.11)(CI 서비스 컨테이너와 같은 태그)이다. `apache/kafka:4.3.2`는 VM 실측(C-05-01) 전의 잠정값이다.
- Spark 4.1.3에는 Delta 기본 jar(`delta-spark_2.13:4.4.1`, Spark 4.2.0용)가 맞지 않는다. Spark 4.1용 jar(`delta-spark_4.1_2.13:4.4.1`)로만 확인했다(Spark 공식 컨테이너, Java 17, 로컬 저장). 11/5 PM 승인으로 계약(`tech_stack.md`)에 반영됐다. 11/8 공식 VM 작업에서는 OpenJDK 21·Garage·Spark 4.1용 Delta JAR 조합으로 200판 쓰기/읽기·라벨·재처리를 확인했다(C-07-01).
- Python은 3.14를 먼저 실측했다(C-05-03 조건 2, 11/4 PM 결정). 필수 5개가 모두 성공해 3.12 재시도는 하지 않았다. 11/5 PM이 ML·LLM Python을 3.14 단일로 확정해 `tech_stack.md`에 반영했다. 같은 날 CI를 포함한 프로젝트 전체를 3.14 단일로 정했다.
- Python 3.14 필수 5개는 Windows 네이티브와 Linux 컨테이너(`python:3.14`, Debian 13, glibc 2.41)에서 확인했다. Ubuntu VM(nifi-01, llm-01) 자체에서는 확인하지 않았다(manylinux 휠이라 glibc 2.28 이상이면 같을 것으로 보지만 실측 전이다). VM 확인은 D-07-02 6번(학습 VM)과 E-06-07 3번 ②(llm-01)의 완료 조건이다(11/5 PM 결정).
- `requirements/`의 조사 항목(scikit-learn, shap, mlflow, langgraph, Django, pymongo, PyMySQL, confluent-kafka, prometheus-client, delta-spark)은 3.14 휠이 있는지만 확인했고(`pip --dry-run`), import는 하지 않았다.
- Prometheus는 Mongo·Kafka·MySQL 지표를 아직 수집하지 않는다(익스포터 없음, C-08-01).
- 루트 `.env`와 Compose 치환용 `ops/compose/.env` 두 파일이 있다(명령을 바꾸지 않으려고). `make-env`가 맞추고 smoke가 어긋남을 잡는다.
- 계정 저장소 스키마·계정은 MySQL 볼륨이 빈 첫 기동 때만 만들어진다. `.env`의 `PALDO_AUTH_DB_*`를 나중에 바꾸면 `down -v`로 볼륨을 다시 만들어야 한다.
- Garage 키를 등록할 때 비밀 키가 `docker compose exec` 인자로 넘어간다. 로컬 프로세스 목록에서 보일 수 있다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| C-05-03 | 11/5 | 로컬 개발용 Compose(`ops/compose/dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`·`devenv.py`), 패키지 확인(`pycheck.py`), Delta 확인(`spark/delta_rw.py`), 팀원 절차·점검표(`README.md`), 계정 저장소 스키마·계정 분리(MySQL initdb)와 `PALDO_AUTH_DB_*`·`PALDO_PUBLIC_ORIGIN`·`PALDO_SIGNUP_INVITE_CODE` 반영. 사전 작업(실제 작업일 11/4) |
| C-06-01 | 11/6 | 저장 어댑터는 infra 모듈, 실제 Mongo VM 시험·외부 보호 설정 인계 |
| C-06-02 | 11/6 | 공식 Kafka 10종 토픽 RF3·ISR2·보존1일 실적용, 발행·소비 시험 인계 |
| C-06-03 | 11/6 | MySQL 두 스키마·TLS 계정 분리, 실제 상호 거부·복제 probe 실패와 복구 계획 |
| C-06-04 | 11/6 | P-31 반영·RunPod 측정 전용와 CPU 잠정값 비교, 자원·시간표·Garage 두 노드 유지 정정 |

| C-07-01 | 11/7 | 공식 Spark/Delta·Garage·실제 Python 버전과 지속 bronze 서비스 인계, 실측 11/8 |
| C-07-02 | 11/7 | 합성 HTTPS 실스택 장애 검증·인증 상태 호스트 내 유지·임시 프로세스 종료, 실측 11/8 |
| C-07-04 | 11/7 | Caddy internal TLS·308·헤더·wss·Secure 쿠키·인증서 경고·실제 GET 지연과 참가자 안내, 실측 11/8 |
| C-08-01 | 11/8 | 지표 중계·읽기 수집·6대시보드·6경보·부하 실측 |
| C-08-02 | 11/8 | 광고 귀속·마감·멱등 집계·실제 환입 재대사 |
| C-08-03 | 11/8 | k3s Job·Kafka sink·5호스트 자원 재측정 |
| C-08-04 | 11/8 | E 태그 archive·4th_private 전용 조회 계정 guard |
| E-09-10 | 11/9 | 실행 도구 `web --port 8000·8002`, Caddyfile upstream 2개·능동 상태 확인, 로컬 Prometheus `paldo-web` 대상 2개(`process` 라벨), Compose 선택 Redis(profile `redis`), proxy README 웹 프로세스 2개 절차·되돌림·비교 측정(`tools/perf/api_bench.py --web-processes`)·선택 Redis 절 |
| C-09-04 | 11/9 | 판 아웃박스 기반 진척 재처리 및 실제 Mongo 멱등 검증 |
| C-11-03 | 11/11 | 실제 unit 기동·재처리·재학습 준비·대사·종료 인계, 타 흐름 검수 표 |
