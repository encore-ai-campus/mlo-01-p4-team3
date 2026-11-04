# 트러블 기록 — C 인프라·데이터

<!-- 양식: docs/tasks/README.md 10절. 태스크마다 항목 하나. 발표의 담당자별 트러블 슬라이드 재료가 된다. -->

### C-05-03 개발용 Compose와 Python 환경 확인
- 문제: Spark 4.1.3 + Delta Spark 4.4.1(`io.delta:delta-spark_2.13:4.4.1`)로 Delta 테이블에 쓰면 `java.lang.NoSuchMethodError: DataSourceUtils$.mergeWriteOptionsIntoHadoopConf`로 실패했다.
- 원인: Delta 4.4.1의 기본 jar는 Spark 4.2.0을 대상으로 빌드됐다(pom의 `spark-sql_2.13` 4.2.0). Delta는 Spark 버전별 jar(`delta-spark_4.1_2.13`, `delta-spark_4.2_2.13`)를 따로 낸다.
- 대응: 같은 Delta 4.4.1의 Spark 4.1용 jar `io.delta:delta-spark_4.1_2.13:4.4.1`로 바꿔 다시 시험했다. 계약(`tech_stack.md`)에 jar 좌표를 적는 것은 PM 승인 뒤에 한다.
- 결과: 기본 jar는 쓰기 0회 성공(실패 1회), Spark 4.1용 jar는 쓰기 2회·읽기(현재 11행, 버전 0은 10행) 성공, 약 30초. 증빙 [`docs/evidence/C-05-03.md`](../evidence/C-05-03.md)
