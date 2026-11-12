# VM 점검표 (C-05-01)

2026-11-11 16:38~16:45 KST 재측정. 11/5 초안의 가정값을 실제 호스트 5대·게스트 20대 값으로 교체했다. 논리 서버명만 공개한다.

## 점검 방법

1. **호스트(노트북, Windows)**: VMware VM 폴더를 읽어 기동 여부·vCPU·RAM·디스크(디스크별 OS·데이터)·스냅샷 수와 호스트 디스크 여유를 한 번에 출력한다. VM이 꺼져 있어도 된다.
   ```
   powershell -ExecutionPolicy Bypass -File ops\inventory_host.ps1
   ```
   VM 폴더가 기본 위치(`문서\Virtual Machines`)가 아니면 `-VmRoot "<폴더>"`를 붙인다. 폴더 이름이 스크립트의 대응표(`pj3-…` → 논리 서버명)에 없으면 "(프로젝트 밖)"으로 나오니 논리 서버명을 직접 고쳐 적는다.
2. **게스트(VM 안, Linux)**: VM을 켜고 SSH로 들어가 OS·Python·vCPU·RAM 사용·디스크별 크기와 마운트별 사용량(`disks`·`mounts`)·서비스 상태를 JSON 한 줄로 받는다. 스크립트를 VM에 복사할 필요 없이 표준 입력으로 넘긴다.
   ```
   ssh <접속 별칭> 'bash -s <논리 서버명>' < tools/perf/vm_probe.sh
   ```
   출력에는 IP·hostname이 없다. JSON의 값을 아래 표에 옮긴다.
3. 기동이 안 되거나 SSH가 안 되면 "기동 안 되는 VM" 표에 증상·원인·조치 계획을 적는다.

## 현재 VM 20대

호스트 할당값과 게스트 사용값은 별도 측정이다. VMware가 예약한 메모리 때문에 게스트 MemTotal은 할당보다 작다. `df -BG`는 올림한 GiB이며 마운트별 사용량은 게스트 기준이다. VMDK 파일 길이 합은 NTFS 실제 할당 클러스터나 VM 폴더 전체 크기와 다르다. 실행 중 디스크는 공유 읽기로 앞64KiB 설명 영역만 읽었고 변경하지 않았다.

| 노트북 | 논리 서버명 | 기동 | vCPU | RAM 할당/게스트 사용(GiB) | 디스크 할당 / 마운트 사용·크기(GiB) | VMDK 파일 크기 합(GiB) | 스냅샷 수 | OS | Python | 설치 버전 | 점검일·담당 |
|---|---|---|---:|---|---|---:|---:|---|---|---|---|
| 1 | bastion-01 | 켜짐·SSH 성공 | 2 | 4 / 0.80 | 40 / / 14/38 | 14.4 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | 앱/노드 exporter active, 버전 미수집 | 11/11 C |
| 1 | kafka-01 | 켜짐·SSH 성공 | 2 | 4 / 1.33 | 40 / / 14/39 | 19.3 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; kafka: 4.1.2 | 11/11 C |
| 1 | spark-master-01 | 켜짐·SSH 성공 | 2 | 4 / 1.96 | 90 (40 + 50) / / 25/39; /srv/pj3-spark-data 2/49 | 42.5 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; spark: 4.1.3, Spark 4.1.3 (git revision 77bbf77e86a) built for Hadoop 3.4.2 | 11/11 C |
| 1 | mysql-01 | 켜짐·SSH 성공 | 2 | 4 / 1.27 | 90 (40 + 50) / / 12/39; /var/lib/mysql 7/49 | 52.9 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mysql: /usr/sbin/mysqld  Ver 8.4.11-0ubuntu0.26.04.1 for Linux on x86_64 ((Ubuntu)) | 11/11 C |
| 2 | app-01 | 켜짐·SSH 성공 | 2 | 4 / 1.50 | 40 / / 17/39 | 18.8 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | 앱/노드 exporter active, 버전 미수집 | 11/11 C |
| 2 | kafka-02 | 켜짐·SSH 성공 | 2 | 4 / 1.32 | 40 / / 12/39 | 14.9 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; kafka: 4.1.2 | 11/11 C |
| 2 | spark-worker-01 | 켜짐·SSH 성공 | 2 | 4 / 0.83 | 90 (40 + 50) / / 13/39; /srv/pj3-spark-data 1/49 | 58.4 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; spark: 4.1.3, Spark 4.1.3 (git revision 77bbf77e86a) built for Hadoop 3.4.2 | 11/11 C |
| 2 | mongo-01 | 켜짐·SSH 성공 | 2 | 4 / 1.31 | 90 (40 + 50) / / 13/39; /var/lib/mongodb 5/49 | 37.6 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mongod: db version v9.0.4 | 11/11 C |
| 3 | ad-01 | 켜짐·SSH 성공 | 2 | 4 / 0.83 | 40 / / 10/39 | 11.2 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | 앱/노드 exporter active, 버전 미수집 | 11/11 C |
| 3 | lake-storage-01 | 켜짐·SSH 성공 | 2 | 4 / 0.58 | 140 (40 + 100) / / 10/39; /srv/pj3-object-data 8/98 | 33.3 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | garage: garage v2.4.1 [features: bundled-libs, consul-discovery, fjall, journald, k2v, kubernetes-discovery, lmdb, metrics, sqlite, syslog, telemetry-otlp] | 11/11 C |
| 3 | mysql-03 | 켜짐·SSH 성공 | 2 | 4 / 1.21 | 90 (40 + 50) / / 10/39; /var/lib/mysql 7/49 | 21.1 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mysql: /usr/sbin/mysqld  Ver 8.4.11-0ubuntu0.26.04.1 for Linux on x86_64 ((Ubuntu)) | 11/11 C |
| 3 | mongo-03 | 켜짐·SSH 성공 | 2 | 4 / 1.23 | 90 (40 + 50) / / 12/39; /var/lib/mongodb 6/49 | 28.9 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mongod: db version v9.0.4 | 11/11 C |
| 4 | metrics-01 | 켜짐·SSH 성공 | 2 | 4 / 0.87 | 40 / / 15/39 | 20.1 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | prometheus: prometheus, version 3.15.0 (branch: HEAD, revision: 5241a27fe3c6983549fccc32f6e65917408c63cd); grafana: grafana version 13.2.4 | 11/11 C |
| 4 | spark-worker-03 | 켜짐·SSH 성공 | 2 | 4 / 1.89 | 90 (40 + 50) / / 17/39; /srv/pj3-spark-data 1/49 | 72.4 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; spark: 4.1.3, Spark 4.1.3 (git revision 77bbf77e86a) built for Hadoop 3.4.2 | 11/11 C |
| 4 | nifi-01 | 켜짐·SSH 성공 | 2 | 4 / 1.57 | 40 / / 32/39 | 39.8 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; nifi: 2.12.0 | 11/11 C |
| 4 | media-storage-01 | 켜짐·SSH 성공 | 2 | 4 / 0.63 | 140 (40 + 100) / / 12/37; /srv/pj3-object-data 8/98 | 38.7 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | garage: garage v2.4.1 [features: bundled-libs, consul-discovery, fjall, journald, k2v, kubernetes-discovery, lmdb, metrics, sqlite, syslog, telemetry-otlp] | 11/11 C |
| 5 | kafka-03 | 켜짐·SSH 성공 | 2 | 4 / 1.28 | 40 / / 13/39 | 15.1 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; kafka: 4.1.2 | 11/11 C |
| 5 | spark-worker-02 | 켜짐·SSH 성공 | 2 | 4 / 2.13 | 90 (40 + 50) / / 16/39; /srv/pj3-spark-data 1/49 | 42.4 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | java: openjdk version "21.0.13.0.3" 2026-10-24; spark: 4.1.3, Spark 4.1.3 (git revision 77bbf77e86a) built for Hadoop 3.4.2 | 11/11 C |
| 5 | mysql-02 | 켜짐·SSH 성공 | 2 | 4 / 1.22 | 90 (40 + 50) / / 11/39; /var/lib/mysql 7/49 | 39.8 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mysql: /usr/sbin/mysqld  Ver 8.4.11-0ubuntu0.26.04.1 for Linux on x86_64 ((Ubuntu)) | 11/11 C |
| 5 | mongo-02 | 켜짐·SSH 성공 | 2 | 4 / 1.30 | 90 (40 + 50) / / 13/39; /var/lib/mongodb 6/49 | 32 | 1 | Ubuntu 26.04.1 LTS | Python 3.14.4 | mongod: db version v9.0.4 | 11/11 C |

## 호스트 5대

| 노트북 | 호스트 C 여유/전체(GiB) | 프로젝트 가상 디스크 할당 합(GiB) | VMDK 파일 크기 합(GiB) | 호스트 RAM 전체/여유(GiB) |
|---|---|---:|---:|---|
| 1 | 23.3/475.9 | 260 | 129.1 | 31.6/4.7 |
| 2 | 15.6/475.9 | 260 | 129.7 | 31.6/8.2 |
| 3 | 29/475.9 | 360 | 94.5 | 31.6/4.3 |
| 4 | 35.2/476 | 310 | 171 | 31.6/2.2 |
| 5 | 13.5/475.8 | 310 | 129.3 | 31.6/5.8 |

프로젝트 외 VM은 합계에서 제외했다. 자동 스냅샷 병합·VM 종료·기존 데이터 삭제는 하지 않았다. 호스트 여유가 적은 노트북2·5는 이미지/체크포인트를 추가하기 전 용량을 다시 확인한다.

## 기동 안 되는 VM

현재 프로젝트 VM 20대 모두 기동 및 SSH 성공이다. 11/7의 비실행·Kafka 실패 기록을 현재 장애로 재사용하지 않는다. `active`는 기능 검증을 뜻하지 않으며 복제·대사 결과는 각 C 증빙을 따른다.

## 서비스 버전 보완

표준 PATH 외 Java 패키지와 서비스 실행 파일의 버전은 `python3 tools/perf/vm_service_versions.py`로 읽었다. 결과에는 설치 패키지 버전과 현재 active unit만 포함한다. 게임 웹·광고 앱의 배포 커밋은 C-11-01 등 배포별 증빙을 따르며 현재 표에서 추정하지 않는다.
