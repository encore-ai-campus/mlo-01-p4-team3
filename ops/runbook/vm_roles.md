# VM 역할과 디스크 예산 (C-05-02 초안)

3차 프로젝트 VM 20대를 4차 역할로 다시 나눈다. 배치 원본은 기획서 6.3 "노트북별 배치"이고, 이 문서는 운영에 필요한 담당·상태·디스크 예산을 더한다. 논리 서버명만 쓴다. 접속 정보는 저장소 밖 비공개 문서에 있다. 실측값은 `ops/inventory_check.md`.

## 노트북별 VM과 4차 역할

| 노트북 | 노트북 관리 | VM | 4차 역할 | 4차 담당 흐름 | 상태(계획) |
|---|---|---|---|---|---|
| 1 | C | bastion-01 | Bastion, Ansible 관리 | C | 사용 |
| 1 | C | kafka-01 | Kafka 브로커 1 | C | 사용 |
| 1 | C | spark-master-01 | Spark Master | C | 사용 |
| 1 | C | mysql-01 | 광고 DB Primary | C(D 사용) | 사용 |
| 2 | PM(담당 흐름 없음) | app-01 | 게임 웹(Django·웹소켓) | B | 사용 |
| 2 | PM | kafka-02 | Kafka 브로커 2 | C | 사용 |
| 2 | PM | spark-worker-01 | Spark Worker | C | 사용 |
| 2 | PM | mongo-01 | Mongo 레플리카 셋 1 | C | 사용 |
| 3 | PM | ad-01 | 광고 서버 | D | 사용 |
| 3 | PM | lake-storage-01 | Delta Lake 저장소 | C | 사용 |
| 3 | PM | mysql-03 | (3차 MySQL 3) | - | **끄기**(LLM 자원 확보, 기획서 6.3) |
| 3 | PM | mongo-03 | Mongo 레플리카 셋 3 | C | 사용 |
| 3 | PM | llm-01(신규) | 로컬 LLM 서버 | E | **잠정**: 11/6 C-06-04가 VM / Windows 직접 실행과 RAM을 정한다 |
| 4 | B | metrics-01 | Prometheus·Grafana | C | 사용 |
| 4 | B | nifi-01 | ML 작업 서버(학습) | D | 사용(밤) |
| 4 | B | media-storage-01 | (3차 이미지 저장소) | - | **끄기**(기획서 6.3) |
| 4 | B | spark-worker-03 | k3s 시뮬레이션 | C(D 사용) | 사용(밤) |
| 5 | D | kafka-03 | Kafka 브로커 3 | C | 사용 |
| 5 | D | spark-worker-02 | Spark Worker / k3s | C | 사용 |
| 5 | D | mysql-02 | 광고 DB Replica | C | 사용 |
| 5 | D | mongo-02 | Mongo 레플리카 셋 2 | C | 사용 |

## 3차 VM 이름과 4차 논리 서버명

VMware 폴더와 3차 접속 별칭은 3차 이름(`pj3-…`)을 그대로 쓴다. 4차 문서·증빙에는 논리 서버명만 쓴다. 출처: 3차 VM 설치 패키지(공유 드라이브)와 3차 인계 자료.

| 3차 이름 | 4차 논리 서버명 | 3차 이름 | 4차 논리 서버명 |
|---|---|---|---|
| bastion | bastion-01 | pj3-mysql01 | mysql-01 |
| pj3-webapp | app-01 | pj3-mysql02 | mysql-02 |
| pj3-ad-server | ad-01 | pj3-mysql03 | mysql-03 |
| pj3-monitor | metrics-01 | pj3-mongo01 | mongo-01 |
| pj3-spark-master | spark-master-01 | pj3-mongo02 | mongo-02 |
| pj3-spark-worker01 | spark-worker-01 | pj3-mongo03 | mongo-03 |
| pj3-spark-worker02 | spark-worker-02 | pj3-object01 | media-storage-01 |
| pj3-spark-worker03 | spark-worker-03 | pj3-object02 | lake-storage-01 |
| pj3-nifi | nifi-01 | pj3-kafka01 | kafka-01 |
| pj3-kafka02 | kafka-02 | pj3-kafka03 | kafka-03 |

- 설치 기준(같은 출처): 모든 VM 2 vCPU(1소켓 × 2코어), RAM 4096MB, 40GB 확장형 디스크, Ubuntu 26.04.1 live server(자동 설치). bastion과 pj3-webapp은 설치 패키지에 없다(webapp은 나중에 추가). VMware 폴더 이름은 노트북마다 다를 수 있어 `ops/inventory_host.ps1`의 대응표에 없으면 직접 고쳐 적는다.

- "노트북 관리"는 VM 켜기·끄기·설정 변경을 하는 사람이다(`docs/tasks/README.md` 1절). "4차 담당 흐름"은 그 VM의 서비스를 맡는 흐름이다.
- 끄기 대상(mysql-03, media-storage-01)과 llm-01 생성은 C-05-02 조건 2다. **11/5 밤 기준 아직 하지 않았다**(PM 결정 대기, C 확인 필요).

## 3차 정리 (조건 1)

3차 데이터·서비스·계정·systemd timer 정리는 **C와 범위를 합의한 뒤** 한다. 서버의 데이터를 지우는 작업이라 대행하지 않았다. 정리하면 지운 것 목록을 이 절에 적는다.

| VM | 지운 것 | 남긴 것 | 날짜·담당 |
|---|---|---|---|
| (대기) | | | |

## 디스크 예산 (조건 4, 초안)

노트북마다 호스트 여유 공간 안에서 아래 항목이 들어가야 한다. 노트북 3만 실측했다(11/5, `ops/inventory_check.md`). 나머지는 각 노트북 점검 뒤 채운다.

| 노트북 | 호스트 여유(GB) | 모델 파일 | Docker 이미지 | 학습 체크포인트 | MLflow | Kafka 로그(보존 1일) | Delta | 합계 예산 | 판정 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 측정 대기 | - | | - | - | | - | | |
| 2 | 측정 대기 | - | | - | - | | - | | |
| 3 | 76.1 | LLM GGUF(E-06-07 후보, 수 GB) | | - | - | - | lake-storage-01 안 | | VM 디스크 할당 합계 360GB > 여유 76.1GB. VM 안 데이터가 늘면 호스트가 먼저 찬다. mysql-03을 끄는 것만으로는 공간이 돌아오지 않는다(기획서 6.3 디스크) |
| 4 | 측정 대기 | - | | (nifi-01) | (nifi-01) | | - | | |
| 5 | 측정 대기 | - | | - | - | | - | | |

**스냅샷 병합 일정**: 노트북 3의 VM 4대는 스냅샷이 1개씩 있다. 스냅샷을 지우면(병합) 호스트 공간을 돌려받을 수 있지만 시간과 추가 여유가 필요하다. 각 노트북 점검 결과를 본 뒤 C가 정한다. 제안: 사용자 테스트가 없는 11/10 오전.
