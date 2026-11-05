# 로컬 개발 환경 (Compose + Python)

각자 노트북에서 Mongo·Kafka·MySQL·S3 호환 저장소(Garage)·Prometheus·Grafana를 띄우고, Python 패키지가 설치되는지 확인하는 방법이다. 통합·사용자 테스트는 VM(기획서 6.3)에서 한다. 설계와 설정값은 모듈 문서 [`docs/modules/ops.md`](../../docs/modules/ops.md), 이 PC에서 확인한 결과는 증빙 [`docs/evidence/C-05-03.md`](../../docs/evidence/C-05-03.md)에 있다.

> 기준 사례: 아래 절차와 점검표의 "기준 PC" 값은 **2026-11-04 PM 노트북 한 대**에서 확인한 결과다(HP ProBook 450 G10, Windows 11 Pro 22631). 다른 노트북은 기종이 같아도 각자 점검표를 채워야 한다(C-05-03 조건 5).

## 1. 구성

| 서비스 | 이미지 | 호스트 주소(127.0.0.1만) | 메모리 상한 | 용도 |
|---|---|---|---|---|
| mongo | `mongo:8.0.34` | `localhost:27117` | 640MB | 판 문서·아웃박스·콘텐츠. 레플리카셋 `rs0` 1노드(인증 + 키 파일) |
| kafka | `apache/kafka:4.3.2` | `localhost:9092` | 1GB | 이벤트. KRaft 1노드, 보존 24시간 |
| mysql | `mysql:8.4.13` | `localhost:3316`(`.env`의 `PALDO_ADS_DB_PORT`) | 512MB | 광고 DB `paldo_ads`, 계정 저장소 `paldo_auth`(스키마·DB 계정을 나눠 서로 접근 거부) |
| garage | `dxflrs/garage:v2.5.0` | S3 `localhost:3900`, 관리 `localhost:3903` | 256MB | Delta·MLflow 버킷(`paldo-lake`, `paldo-mlflow`) |
| prometheus | `prom/prometheus:v3.15.0` | `localhost:9090` | 256MB | 지표 수집(지금은 자기 자신·Grafana·Garage) |
| grafana | `grafana/grafana:13.2.4` | `localhost:3000` | 384MB | 대시보드. 데이터 소스 Prometheus 자동 등록 |

- Mongo·MySQL 포트가 표준(27017·3306)이 아닌 이유: 노트북에 Windows용 MongoDB·MySQL이 이미 돌고 있을 수 있다(기준 PC가 그랬다). 겹치지 않게 비켜 두었다.
- 원자성: 상태·진실·아웃박스·명령 기록은 **판 문서 하나에 한 번에 쓰는 것**으로 보장한다(`docs/contracts/ports.md` 2절 ④). 레플리카셋은 변경 스트림과 VM과 같은 연결 방식을 위한 것이고, 다중 문서 트랜잭션은 쓰지 않는다.
- Compose 프로젝트 이름은 `paldo-dev`로 고정했다. 고정 `container_name`은 쓰지 않는다. 종료는 이 프로젝트만 내리고, `docker system prune` 같은 전역 정리는 하지 않는다.

## 2. 처음 한 번 (팀원용, PowerShell에 그대로 붙여 넣기)

준비물: Docker Desktop(WSL2), Python 3.14(`py -3.14`), Git. pyspark를 Windows에서 직접 돌릴 사람만 Java 17 이상이 필요하다.

```powershell
# 0) 저장소 루트로 이동 (경로는 자기 것으로)
cd C:\encore_project\4rd_project\paldo

# 1) 확인: Docker 엔진이 떠 있고 Python 3.14가 있다
docker version --format '{{.Server.Version}}'
py -3.14 --version

# 2) 주소·비밀값 파일 만들기: 루트 .env(이미 있으면 그대로)와 Compose 치환용 ops/compose/.env. 둘 다 Git 추적 안 함
#    3306을 이미 쓰는 프로그램(Windows MySQL 등)이 없고 3306을 쓰고 싶으면, .env의 PALDO_ADS_DB_PORT와 PALDO_AUTH_DB_PORT를 같은 값으로 고친 뒤 이 명령을 다시 실행
py -3.14 -m ops.compose.run make-env

# 3) 띄우기 → Garage 초기화(레이아웃·키·버킷, 여러 번 해도 같다) → 상태 확인
docker compose -p paldo-dev -f ops/compose/dev.yml up -d
py -3.14 -m ops.compose.run garage-init
py -3.14 -m ops.compose.run smoke
```

`smoke`의 마지막 줄이 `"msg": "결과 6/6 성공"`이고 종료 코드가 0이면 된다. 첫 실행은 이미지 다운로드(약 4.5GB 디스크, 기준 PC에서 약 3분)가 먼저 일어난다.

### Python 가상환경

`python` 대신 **`py -3.14`로 venv를 만든다.** `python`이 다른 배포판(기준 PC는 miniconda 3.14.6)을 가리킬 수 있는데, venv는 만든 Python을 바탕으로 만들어지기 때문이다.

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
# 자기 영역만 설치한다(여러 개 가능)
.venv\Scripts\python -m pip install -r requirements/web.txt        # B·C·D 웹·저장소·광고 서버
.venv\Scripts\python -m pip install -r requirements/ml.txt         # D ML (torch CPU 등, 약 1GB)
.venv\Scripts\python -m pip install --only-binary=:all: -r requirements/llm.txt   # E LLM
.venv\Scripts\python -m pip install -r requirements/pipeline.txt   # C 파이프라인
# 확인: 필수 5개(torch, transformers, lightgbm, pyspark, llama-cpp-python) import와 간단한 동작
.venv\Scripts\python -m ops.compose.pycheck
```

`pycheck`은 설치하지 않은 패키지를 FAIL로 표시한다. 자기 영역 것만 보면 된다. 네 파일을 모두 함께 설치해도 충돌이 없는 것을 기준 PC에서 확인했다(`pip install --dry-run`).

## 3. 매일 쓰는 명령

```powershell
docker compose -p paldo-dev -f ops/compose/dev.yml up -d       # 띄우기
docker compose -p paldo-dev -f ops/compose/dev.yml ps          # 상태(모두 healthy)
py -3.14 -m ops.compose.run smoke                              # 기능 확인
docker compose -p paldo-dev -f ops/compose/dev.yml stop        # 잠시 멈춤(메모리 반환, 데이터 유지)
docker compose -p paldo-dev -f ops/compose/dev.yml down        # 내리기(데이터 볼륨은 남는다)
```

데이터까지 지우는 `down -v`는 PM 허락 뒤에만 한다. `.env`를 다시 만들면 기존 볼륨의 Mongo·MySQL 계정과 달라지므로, 이때도 볼륨을 지워야 한다. MySQL의 계정 저장소 스키마·계정도 볼륨이 빈 첫 기동 때만 만들어진다(6절 "변수가 늘었을 때").

연결 정보는 앱이 `.env`의 `PALDO_*` 변수(이름은 `docs/contracts/integration.md` 4.1)로 읽는다. 코드·문서에 값을 쓰지 않는다. `make-env`가 로컬 값을 채운다:

| 대상 | 변수 | 로컬 값 |
|---|---|---|
| Mongo | `PALDO_MONGO_URI` | `mongodb://<계정>:<비밀번호>@localhost:27117/?replicaSet=rs0&authSource=admin` |
| Kafka | `PALDO_KAFKA_BOOTSTRAP` | `localhost:9092`(컨테이너끼리는 `kafka:19092`) |
| MySQL(광고) | `PALDO_ADS_DB_HOST`·`_PORT`·`_NAME`·`_USER`·`_PASSWORD` | `127.0.0.1`·`3316`·`paldo_ads`·컨테이너 앱 계정과 같은 값 |
| MySQL(계정 저장소) | `PALDO_AUTH_DB_HOST`·`_PORT`·`_NAME`·`_USER`·`_PASSWORD` | 같은 MySQL(`127.0.0.1`·`3316`), 스키마 `paldo_auth`, 계정 `paldo_auth_dev`(그 스키마 권한만), 비밀번호 무작위 |
| 게임 웹 출처 | `PALDO_PUBLIC_ORIGIN` | `http://localhost:8000`(Django 개발 서버 기본 포트). 브라우저도 **`http://localhost:8000`으로 접속**한다. `127.0.0.1:8000`으로 열면 출처가 달라 웹소켓이 거부된다 |
| 가입 초대 코드 | `PALDO_SIGNUP_INVITE_CODE` | 비움(공개 가입, 파일럿 설정 안 함) |
| S3 | `PALDO_S3_ENDPOINT`·`_ACCESS_KEY`·`_SECRET_KEY` | `http://localhost:3900`(리전 `garage`, 경로 방식), `garage-init`이 이 키를 등록 |
| Prometheus | `PALDO_PROMETHEUS_URL` | `http://localhost:9090` |
| MLflow·LLM·광고 서버 | `PALDO_MLFLOW_URI`, `PALDO_LLM_URL`, `PALDO_ADS_INTERNAL_URL` | 비움(로컬 Compose에 없음. 가짜 구현을 쓴다) |
| Grafana | (앱이 안 씀) `GF_SECURITY_ADMIN_USER`·`…PASSWORD` | `http://localhost:3000` |

## 4. Spark + Delta 확인 (C 담당, 선택)

Spark 4.1.3 컨테이너에서 Delta 테이블에 쓰고 다시 읽는다. **Delta jar는 Spark 4.1용 `io.delta:delta-spark_4.1_2.13:4.4.1`을 쓴다.** 기본 jar `delta-spark_2.13:4.4.1`은 Spark 4.2.0용으로 빌드돼 Spark 4.1.3에서 쓰기 때 `NoSuchMethodError`가 난다(증빙 C-05-03). 이 조합은 11/5 PM 승인으로 `tech_stack.md`에 반영됐다. 아래 명령은 **jar 좌표 재현용**이다. 공식 이미지의 Python이 3.10이라 프로젝트 Python(3.14)과 다르며, 실제 파이프라인의 드라이버·실행기 Python 3.14 일치는 C-07-01 6번에서 맞추고 확인한다.

```powershell
docker run --rm --memory 1g -v "${PWD}/ops/compose/spark:/work:ro" apache/spark:4.1.3-scala2.13-java17-python3-ubuntu `
  /opt/spark/bin/spark-submit --master "local[2]" --driver-memory 512m --conf spark.jars.ivy=/tmp/.ivy2 `
  --packages io.delta:delta-spark_4.1_2.13:4.4.1 `
  --conf spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension `
  --conf spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog `
  /work/delta_rw.py /tmp/delta_smoke
```

기대 출력: `"msg": "delta 쓰기·읽기 성공"`, `"rows_now": 11, "rows_v0": 10`. 이미지는 약 2.2GB다.

## 5. 팀원별 점검표

각자 자기 노트북에서 아래 명령을 돌려 칸을 채운다. 기준 PC 칸은 2026-11-04 결과다. 다른 노트북 칸을 채우는 시점과 담당은 PM이 정한다.

| # | 항목 | 확인 명령(PowerShell) | 기대 | 기준 PC(11/4) | 노트북 1 | 노트북 2 | 노트북 3 | 노트북 4 | 노트북 5 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | OS | `(Get-CimInstance Win32_OperatingSystem).Caption` | Windows 11 | Windows 11 Pro 22631 | | | | | |
| 2 | 가상화 | `(Get-Process vmware \| Select -First 1).Path \| % { (Get-Item $_).VersionInfo.ProductVersion }`, `wsl -l -v` | VMware Workstation, WSL2 | VMware 26.0.1, WSL 3.0.3, docker-desktop WSL2 | | | | | |
| 3 | Docker | `docker version --format '{{.Server.Version}}'`; `docker compose version --short` | 엔진 응답, Compose v2 이상 | 엔진 29.7.2, Compose 5.4.0(Desktop 4.88.1) | | | | | |
| 4 | Python | `py -3.14 -c "import sys;print(sys.version.split()[0], sys.executable)"`; `where.exe python` | 3.14.x | 3.14.6(`…\Python\pythoncore-3.14-64`). `python`은 miniconda 3.14.6을 가리킴 | | | | | |
| 5 | Java | `java -version`; `$env:JAVA_HOME` | 17 이상(pyspark 로컬 실행 때만) | PATH 17.0.20.1, JAVA_HOME은 21.0.12 | | | | | |
| 6 | 디스크 여유 | `"{0:N1}GB" -f ((Get-PSDrive C).Free/1GB)` | 이미지·venv 설치 뒤 20GB 이상 권장 | 설치 전 68.0GB → 설치 뒤 53.4GB | | | | | |
| 7 | VM 부하 | `@(Get-Process vmware-vmx).Count`; 여유 RAM `"{0:N1}GB" -f ((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB)` | 기동 뒤 smoke 성공 | VM 4대 실행 중, 여유 RAM 1.7GB(기동 전) → 1.5GB(기동 뒤) | | | | | |
| 8 | 포트 충돌 | `netstat -ano \| findstr "LISTENING" \| findstr ":27117 :9092 :3316 :3900 :3903 :9090 :3000"` | 기동 전 출력 없음 | 없음 | | | | | |
| 8-1 | MySQL 3306 사용 여부 | `netstat -ano \| findstr "LISTENING" \| findstr ":3306 "` | 3306을 이미 쓰는 프로그램(Windows MySQL 등)이 있으면 호스트 포트를 바꾸고 `.env`의 `PALDO_ADS_DB_PORT`와 `PALDO_AUTH_DB_PORT`(같은 MySQL)에 같이 적은 뒤 `make-env` 재실행(기본 3316) | 3306을 Windows MySQL(`mysqld`)이 사용 → 3316. 27017은 Windows MongoDB(`mongod`)가 사용하지만 Compose Mongo는 27117이라 겹치지 않음 | | | | | |
| 9 | 스모크 | `py -3.14 -m ops.compose.run smoke` | `결과 6/6 성공`, 종료 코드 0 | 6/6 성공 | | | | | |
| 10 | Python 패키지 | `.venv\Scripts\python -m ops.compose.pycheck` | `필수 실패 0건`(자기 영역만 설치했으면 그 패키지만 OK) | 필수 5개 OK | | | | | |

## 6. 막힐 때

| 증상 | 원인·대응 |
|---|---|
| `failed to connect to the docker API … dockerDesktopLinuxEngine` | Docker Desktop이 꺼져 있다. 켜고 1분쯤 기다린다 |
| `env file …\.env not found` | `py -3.14 -m ops.compose.run make-env`를 먼저 한다 |
| smoke가 `ops/compose/.env가 루트 .env와 다르다` | `.env`를 고친 뒤 `make-env`를 다시 실행하고 `up -d` |
| `ports are not available` | 그 포트를 다른 프로그램이 쓴다. MySQL은 `.env`의 `PALDO_ADS_DB_PORT`와 `PALDO_AUTH_DB_PORT`를 같은 값으로 바꾸고(다르면 smoke가 거부) `make-env` → `up -d`. 나머지는 셸 변수로 바꿔 띄운다(`$env:PALDO_KAFKA_PORT=19092` 등: `PALDO_KAFKA_PORT`, `PALDO_S3_PORT`, `PALDO_GARAGE_ADMIN_PORT`, `PALDO_PROMETHEUS_PORT`, `PALDO_GRAFANA_PORT`). 이때 `.env`의 해당 주소 변수(`PALDO_KAFKA_BOOTSTRAP` 등)도 같이 고친다. Mongo 27117은 레플리카셋 주소라 바꿀 수 없다. 겹친 포트는 PM에게 알린다 |
| 변수가 늘었을 때: smoke가 `…: 변수가 없다`, 또는 mysql FAIL `auth→paldo_auth 실패: ERROR 1045` | `.env.example`에 변수가 추가됐는데 `.env`·볼륨이 옛것이다. PM 허락 뒤 이 프로젝트만 지우고 다시 만든다: `docker compose -p paldo-dev -f ops/compose/dev.yml down -v` → 루트 `.env`를 다른 이름으로 옮김(`Rename-Item .env .env.old`) → `make-env` → `up -d` → `garage-init` → `smoke`. `down -v`는 `-p paldo-dev`로만 쓴다 |
| smoke에서 garage FAIL "레이아웃 없음" | `py -3.14 -m ops.compose.run garage-init` |
| mongo가 계속 `health: starting` | 첫 기동 때 레플리카셋 초기화에 수십 초가 걸린다. 2분이 넘으면 `docker compose -p paldo-dev -f ops/compose/dev.yml logs mongo` |
| 한글이 깨져 보인다 | 로그는 UTF-8 JSON이다. PowerShell에서는 `[Console]::OutputEncoding=[Text.Encoding]::UTF8` |
| 노트북이 느려진다 | `stop`으로 멈춘다. 서비스 메모리 상한 합계는 약 3GB다(기준 PC 실사용 약 1.0GB) |
