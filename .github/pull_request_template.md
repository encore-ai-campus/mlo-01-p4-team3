<!-- docs/tasks/README.md 11절. 아래 Task 줄은 CI evidence 체크가 읽는다. 지우지 않는다. PR 하나 = 태스크 ID 하나(Task 줄 1개). HTML 주석 안의 글은 검사하지 않는다. -->
Task: A-05-01
<!-- 필수. 이 PR의 태스크에 쓴 시간(문서화 포함, 공통 시간 제외). 숫자만. 증빙 문서의 같은 줄과 같은 값 -->
실제 소요 시간(h): 

<!-- 조건을 모두 만족했으면 Closes, 일부·실패 증빙이면 Refs. 둘 중 정확히 한 줄만. Fixes·Resolves 등 다른 닫기 키워드는 쓰면 실패한다. Closes면 CI가 check_evidence --complete를 돌린다. -->
Closes #

## 바뀐 점

-

## 증빙

- `docs/evidence/<ID>.md`
- 측정 결과: `docs/evidence/perf/<ID>/<이름>.json` (성능 조건이 있으면. 형식은 README 4절 "결과 JSON 형식")

## 문서

- 모듈 문서: (그날 흐름의 마지막 태스크만) `docs/modules/<모듈>.md` — 일괄 갱신 줄의 대상 태스크마다 변경 이력 행
- 트러블 기록: `docs/troubles/<흐름>.md#<ID>`
- 계약 문서: (계약 파일을 바꿨으면) `docs/contracts/…`
- 라우팅: (새 폴더를 만들었으면) `paldo/README.md`

## 확인

- [ ] 공통 완료 정의 ①~⑧(README 3절). ⑧ 작성자가 아닌 1명의 승인은 병합 전에 받는다
- [ ] 계약 변경이면 `contract-change` 라벨과 영향받는 흐름 담당자 승인
