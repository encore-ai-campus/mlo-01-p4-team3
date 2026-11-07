# 저장소·아웃박스 인프라

## 무엇을

`server/infra/`는 엔진 포트의 Mongo 저장·진척·콘텐츠 구현과 Kafka Outbox 발행을 담당한다. C-06-01·C-06-02 PR에 구현이 있고 선행 병합 뒤 사용한다. 엔진 내부에는 외부 서비스 의존성을 넣지 않는다. 이 문서는 해당 작업들의 당일 일괄 인계다.

## 왜

[ports.md](../contracts/ports.md) 원자 저장·CAS와 [events.md](../contracts/events.md) 공통 seq·이벤트 ID 계약을 따른다. 요청 경로는 Mongo만 기다리고 Kafka 전송은 별도 프로세스가 재시도한다. 소비자는 event_id와 내용 hash로 중복을 제거한다.

## 사용법

```sh
python -m tools.load_content content/bundle_v1.json
python -m server.infra.outbox_publisher --kafka-config <보호 TLS JSON>
python -m unittest tests.infra.test_mongo_repo -v
python -m unittest tests.infra.test_outbox_publisher -v
```

첫 명령·발행기는 선행 구현 병합과 외부 의존성이 필요하다. 라이브 테스트는 명시 서비스 환경에서만 실행한다. B 조립은 MongoGameRepository·MongoProgressRepository·MongoContentSource에 collection을 주입한다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| PALDO_MONGO_URI | 없음, 외부 보호 설정 | 게임 DB·TLS 인증 | 읽기/쓰기 권한과 Replica Set 연결 |
| PALDO_TEST_MONGO_URI | 없음 | 별도 검증 컬렉션 테스트 | 서비스 데이터와 격리 |
| --kafka-config | 없음, 보호 JSON | TLS·호스트 검증·브로커 | 배포 호스트 전용 ACL·인증 필요 |
| --metrics-port | 9108 | localhost 발행 지표 | 수집기 scrape 설정과 함께 변경 |
| lease | 45초 | 판별 발행자 임대 | 전송 timeout보다 길어야 함 |

## 한계

운영 전용 Mongo DB·계정 생성은 관리자 인증 위치 확인이 남았다. 기존 권한의 고유 검증 컬렉션 시험과 구분한다. 한 문서 Outbox는 Mongo 용량 한도를 가지며 보관 정책이 필요하다. 발행은 at-least-once이며 프로세스 정지·lease 경계의 중복은 소비자가 제거한다. 광고 DB 발행은 D 담당이다. LLM 운영 P-31 반영·RunPod 측정 전용는 [런북](../../ops/runbook/runpod.md)과 최종 자원 확정이 남았다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| C-06-01 | 11/6 | Mongo majority/CAS·진척·불변 콘텐츠, 실제 VM 계약 시험과 지연 측정 |
| C-06-02 | 11/6 | 별도 Outbox 발행·실패 재시도·lease·지표, Kafka 실제 소비 확인 |
| C-06-03 | 11/6 | 본 모듈 변경 없음, 광고·계정 MySQL 분리는 ops/mysql 인계 |
| C-06-04 | 11/6 | 당일 모듈 문서 일괄 갱신, P-31 반영·RunPod 측정 전용와 미확정 자원 구분 |
