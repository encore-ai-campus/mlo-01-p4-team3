# 기술 스택 계약

아래 버전은 모두 **기록값이며 11/5에 실측해야 한다.** 3차 프로젝트 인수 자료에서 옮겼다. 접속 정보(IP, 계정, 비밀번호)는 이 저장소에 쓰지 않는다. 서버는 논리 서버명으로만 부른다.

## 1. 실행 환경

| 구분 | 값 | 비고 |
|---|---|---|
| 서버 | 3차 VMware VM 20대(노트북 5대에 분산) | VM 사양 후보 2 vCPU / 4GB(재확인 전). 배치는 `ops/runbook/vm_roles.md` |
| OS | Ubuntu Server 26.04 | 기록값, 11/5 실측 필요 |
| 시스템 Python | 3.14.6 | 11/4 이 PC 실측(`python --version`). 다른 노트북은 C-05-03 점검표로 확인 |
| ML·LLM Python | **3.14 단일**(11/5 PM 확정, 3.12 가상환경 분리 안 함) | 근거: C-05-03 조건 2 실측(11/4, 기준 PC, Windows 네이티브 3.14.6 venv와 Linux 컨테이너 `python:3.14`): torch 2.15.0+cpu, transformers 5.22.1, lightgbm 4.7.0, pyspark 4.1.3, llama-cpp-python 0.3.36 설치·import·기본 동작 성공(llama는 시스템 정보 호출까지, 모델 추론은 E-06-07), 실패 0건(`docs/evidence/C-05-03.md`). 한계: Ubuntu VM에서는 아직 확인하지 않았다(VM 확인은 11/5 PM 결정으로 완료 조건에 넣었다: 학습·시뮬레이션 VM의 torch·transformers·lightgbm·pyspark 4개는 D-07-02 6번, llm-01의 llama-cpp-python은 E-06-07 3번 ②) |
| 엔진·계약 코드 | 표준 라이브러리만 사용, Python 3.14(11/5 PM 결정: 프로젝트 전체 3.14 단일, CI도 3.14. VM에서는 OS 기본 Python(Ubuntu는 3.12일 수 있음)을 쓰지 않고 `python:3.14` 계열 컨테이너 이미지나 별도 설치한 3.14로 실행한다. PySpark는 드라이버와 실행기의 Python 부 버전이 같아야 하므로 Spark 실행기 이미지도 3.14로 맞추고 C-07-01에서 확인한다) | `engine/`, `contracts/`, `tools/`는 외부 패키지를 import하지 않는다 |

## 2. 구성 요소

| 역할 | 기술 | 버전(기록값, 11/5 실측 필요) | 담당 |
|---|---|---|---|
| 웹·API | Django + Django 템플릿 + htmx + Alpine.js | Django 6.1.2(app 서버에 설치 기록) | B |
| 웹소켓(광고 신호, 11/4 PM 결정) | Django Channels + Daphne(ASGI 서버), 채널 계층 `InMemoryChannelLayer`, 게임 웹 프로세스 1개 | **잠정 범위** `channels>=4.3.2,<5`, `daphne>=4.2.3,<5`(하한은 로컬에서 설치를 확인한 버전. 이전 Daphne의 보안 수정이 빠지지 않게 하한을 올림). Python 3.14·Django 6.1 조합은 미확인이고 설치 성공만으로 호환을 확정하지 않는다. B-07-05에서 대상 VM에 설치해 기동·로그인·웹소켓·기존 HTTP 테스트를 통과시킨 뒤 정확한 버전을 고정·기록 | B |
| HTTPS 프록시(11/4 PM 결정) | Caddy 리버스 프록시, `tls internal` 자체 서명, wss 통과, HSTS 끔 | 버전 미정(C-07-04에서 설치 버전 기록) | C |
| 판 저장 | MongoDB Replica Set 3노드, `w=majority`. 이미지 `mongo:8.0.34`(개발 Compose·CI 서비스 컨테이너도 같은 태그, 11/4 PM 결정) | - | C |
| 이벤트 | Kafka 3노드(브로커·KRaft 컨트롤러 겸용), 보존 1일 | - | C |
| 배치 | Spark(Master 1 + Worker), Hadoop, OpenJDK | Spark 4.1.3, Hadoop 3.4.2, OpenJDK 21 | C |
| 학습 데이터 | Delta Lake on Garage(S3 호환) | Delta Spark 4.4.1, Maven 좌표 **`io.delta:delta-spark_4.1_2.13:4.4.1`**(11/5 PM 확정). 근거: C-05-03 조건 3 실측(Spark 4.1.3 공식 컨테이너, Java 17, Python 3.10, 로컬 파일 저장)에서 이 좌표로 쓰기·추가·읽기·버전 0 읽기 성공, 기본 좌표 `io.delta:delta-spark_2.13:4.4.1`은 NoSuchMethodError(Spark 4.2 빌드)로 실패. OpenJDK 21·Garage(S3) 저장 조합은 실제 스택에 처음 쓰는 C-07-01(Spark → Delta)에서 처음 드러난다 | C |
| 모델 관리 | MLflow 추적·레지스트리(아티팩트는 Garage) | 미설치, 11/9까지 설치 | C·D |
| 관측 | Prometheus, Grafana, node_exporter(전 VM) | 3.15.0 / 13.2.4 / 1.12.1 | C |
| 광고 서버·DB | Django 별도 서비스(ad-01), MySQL Primary + Replica(mysql-01·02). 이미지 `mysql:8.4.11`(개발 Compose·CI 서비스 컨테이너도 같은 태그, 11/4 PM 결정. 11/5 VM 설치 버전에 맞춰 8.4.13 → 8.4.11) | 기록값, 11/5 실측 필요 | D(서버), C(DB·배포) |
| 배포 자동화 | Ansible(Bastion) | - | C |
| 배치 실행 | k3s 2노드(CronJob, Job) | 미설치 | C |
| 의도 해석 | BERT 계열(한국어) + 규칙 매칭 | 11/5 모델 후보 확정 | D |
| 씐 자 확률 | LightGBM(+ 보정) | - | D |
| LLM | llama.cpp(CPU 양자화) + LangGraph | 11/5 벤치마크로 모델 확정 | E |

## 3. 테스트
- 계약 테스트는 표준 `unittest`로 작성한다. pytest가 설치되지 않은 환경에서도 돌아가야 하기 때문이다. 실행: `python -m unittest discover -s tests -t .`
- 각 흐름은 자기 폴더에서 pytest를 써도 된다. 다만 `tests/contract/`는 표준 라이브러리만 쓴다.

## 4. 11/5 실측 목록
1. VM 20대 기동 여부와 실제 vCPU·RAM·디스크
2. OS와 Python 버전
3. 시스템 Python 3.14에서 PyTorch, transformers, LightGBM, llama-cpp-python, pyspark 설치·import(C-05-03 조건 2. 11/4 기준 PC 실측 성공 → 11/5 ML·LLM 3.14 단일 확정)
4. Spark 4.1.3 + Delta Spark 4.4.1 조합으로 쓰기·읽기 시험(C-05-03 조건 3. 11/4 실측 → 11/5 좌표 `io.delta:delta-spark_4.1_2.13:4.4.1` 확정)
5. Kafka와 Mongo 클러스터 상태
6. LLM 노트북에서 VM 실행과 Windows 직접 실행의 토큰 속도 비교
