# 계약 문서 색인

무엇을 찾을 때 어느 계약 문서를 보는지 안내한다. 내용은 각 문서가 원본이다. 계약을 바꾸는 규칙은 [태스크 README](../tasks/README.md) 3절을 따른다.

## 1. 문서별 범위

| 문서 | 다루는 것 | 예시 픽스처 |
|---|---|---|
| [domain.md](domain.md) | 게임 규칙, 상태, 주장, 관측 | `fixtures/scenarios/`, `fixtures/assignment/` |
| [ports.md](ports.md) | 엔진과 바깥의 인터페이스(저장소·발행·의도 해석 등 포트) | — |
| [api.md](api.md) | 화면 API, 플레이어 뷰 | `fixtures/api/` |
| [vn.md](vn.md) | 회의·밤 VN 표시 규칙과 UI 적용 범위(B-09-06 초안, E 검토 대기) | B-10-07에서 후속 검증 |
| [events.md](events.md) | 이벤트 봉투, Kafka 토픽, 학습 행 | `fixtures/events/` |
| [content.md](content.md) | 콘텐츠 묶음(인물, 비밀, 대사) | `fixtures/content_min/` |
| [intent.md](intent.md) | 의도 해석 입출력 | `fixtures/intent/` |
| [ads.md](ads.md) | 광고 서버(경매, 과금 원장, 지면 규격) | `fixtures/ads/` |
| [ml_lifecycle.md](ml_lifecycle.md) | 모델 학습·등록·승격·롤백 | `fixtures/ml/` |
| [erd.md](erd.md) | 데이터 모델 개요: 저장소별 엔터티와 관계(개념 ERD, 안내 문서) | — |
| [privacy.md](privacy.md) | 개인정보 수집·저장·삭제, 계정 | — |
| [integration.md](integration.md) | 영역 간 호출 지도, 환경 변수, 서버 배치 | — |
| [structure.md](structure.md) | 디렉터리 트리(새 파일 위치), 허용 폴더 | — |
| [tech_stack.md](tech_stack.md) | 기술 스택과 버전 | — |
| [parallel_dev.md](parallel_dev.md) | 병렬 개발 규칙(가짜 구현, 인계) | — |
| [crosscheck.md](crosscheck.md) | 계약 교차 검토 기록 | — |

이미지 에셋의 manifest 형식은 계약 폴더가 아니라 [`docs/assets/spec.md`](../assets/spec.md) 4절이다.

## 2. DB 스키마

컬럼 단위 DB 스키마를 한곳에 모은 문서는 없다. 저장소별 엔터티와 관계의 개요는 [erd.md](erd.md)(개념 ERD, 안내 문서)에서 본다. 흐름 사이에 걸리는 제약(원자성, 유일 키, 멱등 키)은 아래 계약에 고정되어 있다. 컬럼 상세와 실제 정의는 각 저장소를 만드는 태스크의 산출물이다.

| 저장소 | 계약에 고정된 제약 | 실제 스키마를 만드는 곳 |
|---|---|---|
| MongoDB(게임 판) | 판 하나 = 문서 하나. 상태·진실·아웃박스를 같은 문서에 원자적으로 쓴다. `seq`·`event_id`는 저장소가 `next_seq`로 매긴다([ports.md](ports.md) 1절, [integration.md](integration.md)) | C-06-01 Mongo 저장소(`GameRepository`·`ProgressRepository`, `server/infra/`) |
| 광고 MySQL | 예약·확정·해제·환입과 아웃박스를 같은 트랜잭션으로 쓴다. `charge_id` 유일, 환입은 음수 행 추가, 버전 행 `ctr_version`·`policy_version`·`bid_version`([ads.md](ads.md) 5절) | D-06-02 `ads/infra/schema.sql`(DB 준비는 C-06-03) |
| 계정 MySQL | 광고와 분리된 전용 DB·계정([privacy.md](privacy.md) 5절) | B-06-04(Django 인증 모델, DB 준비는 C-06-03) |
| Delta(분석) | `event_id` 기준 MERGE로 멱등. bronze → silver → gold, silver는 학습 행([events.md](events.md) 3절, [ads.md](ads.md) 6·7절) | C의 Spark 적재 태스크(C-07-01 등) |

스키마를 바꾸면 위 계약의 제약을 먼저 확인한다. 제약을 바꿔야 하면 계약 변경 절차를 거친다.
