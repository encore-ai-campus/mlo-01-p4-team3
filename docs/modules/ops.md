# ops (운영 구성)

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

인프라 계층의 운영 구성이다. 지금 들어 있는 것은 **로컬 개발용 Compose**(`ops/compose/`)뿐이다.

- `dev.yml`: Mongo(레플리카셋 1노드), Kafka(KRaft 1노드), MySQL, Garage(S3 호환), Prometheus, Grafana를 한 번에 띄운다. 입력은 저장소 루트의 `.env`(비밀값), 출력은 `127.0.0.1`의 서비스 포트다.
- `run.py`(CLI·흐름·docker/HTTP 호출)와 `devenv.py`(규칙: `.env` 생성 규칙, 점검 판정, S3 서명, 로그 레코드): `.env` 만들기, Garage 초기화, 6개 서비스 기능 확인(스모크).
- `pycheck.py`: 필수 Python 패키지 5개의 import·간단한 동작 확인. `spark/delta_rw.py`: Spark 컨테이너 안에서 Delta 쓰기·읽기 확인.
- 영역별 Python 의존성은 저장소 루트 `requirements/`(web·ml·llm·pipeline)에 둔다.

VM 배포(Ansible), Prometheus 수집 대상·경보·대시보드(C-08-01), k3s(C-08-03)는 아직 없다.

## 왜

- 기획서 6.1 "배포": 개발은 각자 PC의 Docker Compose 축소 구성, 통합은 VM. 다섯 흐름이 VM 없이도 실제 저장소·브로커로 개발할 수 있게 한다(`docs/contracts/parallel_dev.md`).
- Mongo를 레플리카셋으로 띄우는 이유: VM(3노드)과 같은 연결 방식(`replicaSet=rs0`, `w=majority`)과 변경 스트림을 로컬에서도 쓰기 위해서다. 원자성은 판 문서 하나에 한 번에 쓰는 것으로 보장하며 다중 문서 트랜잭션은 쓰지 않는다(`docs/contracts/ports.md` 2절 ④).
- S3 호환 저장소로 Garage를 고른 이유: VM `lake-storage-01`과 같은 제품이라 Delta·MLflow 설정을 그대로 옮길 수 있다(기획서 6.3, 11/4 PM 결정).
- 포트를 `127.0.0.1`에만 열고 Mongo·MySQL을 비표준 포트(27117·3316)로 둔 이유: 노트북에 Windows용 Mongo·MySQL이 이미 돌고 있을 수 있다(기준 PC가 그랬다).
- 메모리 상한을 서비스마다 건 이유: 노트북마다 VM 4대가 RAM 약 16GB를 쓴다(기획서 6.3). 기준 PC는 기동 전 여유 RAM이 1.7GB였다.
- 점검 스크립트를 표준 라이브러리만으로 쓴 이유: 패키지를 설치하기 전에도 돌아가야 한다.

## 사용법

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

## 한계

- 이 결과는 **기준 PC 한 대(11/4)**의 것이다. 다른 노트북은 `ops/compose/README.md` 5절 점검표로 각자 확인해야 한다.
- 이미지 `mongo:8.0.34`·`mysql:8.4.13`은 11/4 PM 결정(CI 서비스 컨테이너와 같은 태그)이다. `apache/kafka:4.3.2`는 VM 실측(C-05-01) 전의 잠정값이다.
- Spark 4.1.3에는 Delta 기본 jar(`delta-spark_2.13:4.4.1`, Spark 4.2.0용)가 맞지 않는다. Spark 4.1용 jar(`delta-spark_4.1_2.13:4.4.1`)로만 확인했다. 계약(`tech_stack.md`) 반영은 PM 승인 대기다.
- Python은 3.14를 먼저 실측했다(C-05-03 조건 2, 11/4 PM 결정). 필수 5개가 모두 성공해 3.12 재시도는 하지 않았다. ML·LLM Python 버전 확정과 `tech_stack.md`·CI `core` 반영은 PM 승인 대기다.
- Python 3.14 필수 5개는 Windows 네이티브와 Linux 컨테이너(`python:3.14`, Debian 13, glibc 2.41)에서 확인했다. Ubuntu VM(nifi-01, llm-01) 자체에서는 확인하지 않았다(manylinux 휠이라 glibc 2.28 이상이면 같을 것으로 보지만 실측 전이다).
- `requirements/`의 조사 항목(scikit-learn, shap, mlflow, langgraph, Django, pymongo, PyMySQL, confluent-kafka, prometheus-client, delta-spark)은 3.14 휠이 있는지만 확인했고(`pip --dry-run`), import는 하지 않았다.
- Prometheus는 Mongo·Kafka·MySQL 지표를 아직 수집하지 않는다(익스포터 없음, C-08-01).
- 루트 `.env`와 Compose 치환용 `ops/compose/.env` 두 파일이 있다(명령을 바꾸지 않으려고). `make-env`가 맞추고 smoke가 어긋남을 잡는다.
- 계정 저장소 스키마·계정은 MySQL 볼륨이 빈 첫 기동 때만 만들어진다. `.env`의 `PALDO_AUTH_DB_*`를 나중에 바꾸면 `down -v`로 볼륨을 다시 만들어야 한다.
- Garage 키를 등록할 때 비밀 키가 `docker compose exec` 인자로 넘어간다. 로컬 프로세스 목록에서 보일 수 있다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| C-05-03 | 11/5 | 로컬 개발용 Compose(`ops/compose/dev.yml`), `.env` 생성·Garage 초기화·스모크(`run.py`·`devenv.py`), 패키지 확인(`pycheck.py`), Delta 확인(`spark/delta_rw.py`), 팀원 절차·점검표(`README.md`), 계정 저장소 스키마·계정 분리(MySQL initdb)와 `PALDO_AUTH_DB_*`·`PALDO_PUBLIC_ORIGIN`·`PALDO_SIGNUP_INVITE_CODE` 반영. 사전 작업(실제 작업일 11/4) |
