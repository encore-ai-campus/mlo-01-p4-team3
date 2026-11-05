# 트러블 기록 — C 인프라·데이터

<!-- 양식: docs/tasks/README.md 10절. 태스크마다 항목 하나. 발표의 담당자별 트러블 슬라이드 재료가 된다. -->

### C-05-01 VM 20대 실측과 점검표 양식
- 문제: 11/5 밤 점검 때 VM 20대 모두 SSH(22번 포트)가 시간 초과로 닿지 않았다. 노트북 3의 VM 4대는 `vmrun list` 기준 0대 실행 중이었다. 호스트 점검 스크립트를 Windows PowerShell 5.1로 처음 돌리자 한글 출력이 모두 깨졌다.
- 원인: 노트북 3의 VM은 LLM 벤치 전 RAM 확보를 위해 꺼 둔 상태였고, 나머지 노트북은 밤이라 켜져 있지 않았다. PowerShell 5.1은 BOM 없는 `.ps1`을 시스템 기본 코드 페이지로 읽는다.
- 대응: 꺼진 VM에서도 읽을 수 있는 호스트 값(`.vmx`·`.vmsd`·`.vmdk`)만 먼저 기록하고, 게스트 값은 VM을 켠 뒤 `vm_probe.sh`로 채우게 표를 나눴다. `.ps1`은 UTF-8 BOM으로 저장했다.
- 결과: 20대 중 4대 호스트 값 기록(게스트 0대), 노트북 5대 중 1대 호스트 디스크 기록. 노트북 3은 VM 디스크 할당 합계 360GB가 호스트 여유 76.1GB보다 크다. 증빙 [`docs/evidence/C-05-01.md`](../evidence/C-05-01.md)

### C-05-02 4차 역할 재배정·LLM 잠정 자원·실행 시간표
- 문제: 담당 C와 11/5 밤 연락이 닿지 않았고, 시간표의 시각과 디스크 예산을 정할 근거(각 노트북 실측, 사용자 테스트 운영 시각)가 없었다.
- 원인: C-05-01 실측이 노트북 3 호스트 값만 있고, 사용자 테스트 시각은 E-08-10·E-09-01 운영 계획에서 정해진다.
- 대응: PM이 문서 초안만 대신 썼다. 날짜는 태스크 문서에서 가져오고 시각은 모두 "제안"으로 표시했다. 서버를 바꾸는 정리·정지·생성(조건 1·2)은 C 확인 뒤로 미뤘다.
- 결과: 시간표 8일분, VM 21행(llm-01 포함) 역할표, 디스크 예산 5행 중 1행 실측. 증빙 [`docs/evidence/C-05-02.md`](../evidence/C-05-02.md)

### C-05-03 개발용 Compose와 Python 환경 확인
- 문제: Spark 4.1.3 + Delta Spark 4.4.1(`io.delta:delta-spark_2.13:4.4.1`)로 Delta 테이블에 쓰면 `java.lang.NoSuchMethodError: DataSourceUtils$.mergeWriteOptionsIntoHadoopConf`로 실패했다.
- 원인: Delta 4.4.1의 기본 jar는 Spark 4.2.0을 대상으로 빌드됐다(pom의 `spark-sql_2.13` 4.2.0). Delta는 Spark 버전별 jar(`delta-spark_4.1_2.13`, `delta-spark_4.2_2.13`)를 따로 낸다.
- 대응: 같은 Delta 4.4.1의 Spark 4.1용 jar `io.delta:delta-spark_4.1_2.13:4.4.1`로 바꿔 다시 시험했다. 계약(`tech_stack.md`)에 jar 좌표를 적는 것은 PM 승인 뒤에 한다.
- 결과: 기본 jar는 쓰기 0회 성공(실패 1회), Spark 4.1용 jar는 쓰기 2회·읽기(현재 11행, 버전 0은 10행) 성공, 약 30초. 증빙 [`docs/evidence/C-05-03.md`](../evidence/C-05-03.md)
