# user_test — 사용자 테스트 운영 절차

<!-- 양식: docs/tasks/README.md 10절. 아래 6개 제목을 이 순서 그대로 둔다(check_docs 검사). -->

## 무엇을

11/8 사전 테스트와 11/9 정식 테스트를 어떻게 운영하는지 정한 절차 모듈이다. 산출물은 계획서 [`docs/user_test/plan.md`](../user_test/plan.md)와 집계 도구 3개다(지표 집계·회차 등록용 가명 ID 추출 `tools/ut_metrics.py`, `test_human` 분할·교집합 검사·동결 `tools/ut_freeze.py`, 읽기 전용 내보내기 `server/infra/ut_export.py`). 계획서는 일정, 인원·판 수, 참가자당 진행 순서, 운영자 준비물, 동의 문구(원문 인용), 수집 항목, `test_human` 동결 규칙, 판 종료 설문 확정본, 고정 시드 판 목록, 뒤 태스크 연결을 담는다. 실제 기능은 게임 웹(B-06-03 세션·고정 시드 판, B-06-04 가입·로그인, B-07-02 판 종료 설문)과 프록시(C-07-04)가 만든다. 이 모듈은 그 기능을 운영에서 어떤 순서·값으로 쓰는지 정한다.

계층: 문서(운영). 도구는 인프라 `server/infra/ut_export.py`(Mongo `games`·계정 DB를 `find`·ORM 조회로만 읽어 비밀 없는 JSON으로) → 도메인 순수 함수와 CLI `tools/ut_metrics.py`·`tools/ut_freeze.py`(표준 라이브러리만, 그 JSON만 읽음). `tools/`는 pymongo·Django를 import하지 않는다(`tests/contract/test_layers.py`). 참가자 이름·연락처·명단, 강의실 호수, 접속 주소, 계정·비밀번호는 저장소에 쓰지 않는다.

## 왜

- 근거: 기획서 7.4(사람 데이터 활용 범위·고정 시드 판), 7.5(모집과 개인정보), 8.4(학습 루프), 태스크 E-06-04, 결정 README 7절 P-12(11/6)와 11/6 PM 결정(판 수, 설문 형식, `test_human` 14:30 동결, 가입 속도 제한 삭제, PM 문서 PR #195).
- 고정 시드 판: 같은 세션의 참가자가 같은 판 구성을 같은 순서로 받아야 사람 간 비교가 된다. 프리셋은 테스트 기본인 `base6` 하나로 두고 시드 4개를 쓴다.
- `test_human`은 시각이 아니라 사용자(가명 ID) 단위로 나눈다. 한 사람의 문장이 평가와 학습에 나뉘어 들어가면 평가 수치가 부풀기 때문이다.
- 설문은 짧게(점수 3문항 + 자유 의견 선택, 전체 건너뛰기 허용) 해서 판 사이 부담을 줄이고 응답률을 지킨다. 필드 이름은 계약 `SubmitSurveyInput`(`server/app/dto.py`)을 그대로 쓴다.
- 동의 문구와 수집 항목의 원본은 계약 [`docs/contracts/privacy.md`](../contracts/privacy.md)다. 계획서는 원문을 인용만 하고 바꾸지 않는다.

## 사용법

계획서 절별로 쓰는 곳이 다르다.

| 할 때 | 볼 곳 |
|---|---|
| 11/8 사전 테스트(E-08-10)·11/9 정식 테스트(E-09-01) 진행 | 계획서 1절 일정, 3절 참가자 순서, 4절 운영자 준비물 |
| 세션 만들기·고정 시드 판 지정(B-06-03 세션 관리 화면 `/admin/sessions`) | 계획서 8절(순서, 프리셋 ID, 시드) |
| 판 종료 설문 화면 구현(B-07-02) | 계획서 7절 |
| `test_human` 동결·분할(E-09-02, D-09-01) | 계획서 6절 |

운영자 명령(계획서 4절):

```
python manage.py changepassword <로그인 ID>     # 비밀번호 초기화. 본인을 현장에서 확인한 뒤에만
```

잠금 해제 관리 명령은 B-06-04가 만들고 사용법은 모듈 문서 [`web`](web.md)에 둔다.

집계 도구(지표 정의는 계획서 10절). 내보내기는 운영 DB 읽기 권한이 있는 실행 환경에서 돌리고, 계산은 받은 JSON으로 어디서나 한다. 시간대 없는 명령줄 시각은 KST로 본다.

```
# 1) 내보내기(접속 정보는 환경 변수로만. 저장소·대화에 쓰지 않는다)
PALDO_MONGO_URI=<읽기 전용 URI> python -m server.infra.ut_export games --out games.json [--session-id <회차 ID>]
DJANGO_SETTINGS_MODULE=<운영 설정> python -m server.infra.ut_export accounts --out accounts.json
# 2) 회차 등록용 가명 ID: 가입 시각 [시작, 끝] 경계 포함, 동의·활성·비운영자만. 표준 출력 = 공백 구분 목록, 건수는 표준 오류
python -m tools.ut_metrics participants --accounts accounts.json --from 2026-11-09T12:00 --to 2026-11-09T12:50
# 3) 지표: 마감 뒤 끝난 판은 미완주, 마감 뒤 시작한 판·입력·명령은 세지 않음(설문은 둠). PM 가명은 빼서 한 번, 넣어서 한 번
python -m tools.ut_metrics metrics --games games.json --cutoff 2026-11-09T14:00 --session-id <회차 ID> \
    --exclude-pseudonym <PM 가명> --out metrics.json
# 4) test_human 동결(14:30): 검수 결과·학습·개발 세트는 필수(없으면 {"exclude": []}·빈 파일). 실패면 종료 코드 1, 묶음은 쓰지 않고 report_failed.json만
python -m tools.ut_freeze --games games.json --pretest-games pretest.json --pm-pseudonym <PM 가명> --seed <분할 시드> \
    --exclude-set <학습 세트.jsonl> --exclude-set <개발 세트.jsonl> --privacy-review review.json --out-dir <동결 폴더>
```

동결 폴더: `test_human.jsonl`(시도 ID·가명·`pm` 표시·마스킹 문장), `train_candidates.jsonl`(학습 쪽 사용자 문장), `assignment.json`(시드·정렬된 참가자·평가/학습 사용자), `report.json`(수·제외 사유·충돌), `manifest.json`(앞 네 파일의 바이트 수·sha256, Delta 버전 대신 쓰는 동결 기록, 11/8 사용자 결정). 개인정보 검수 제외는 학습 쪽 문장에도 적용한다. 검수 결과 형식은 `{"exclude": [{"attempt_id": ..., "reason": ...}]}`.

## 설정값

| 이름 | 값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| 정식 테스트 시간 | 11/9 13:00~14:00, 강의실 | 참가자 동시 진행 | 14:00에 진행 중인 판은 미완주로 기록 |
| 목표 인원 | 30명 | 기획서 7.5 | - |
| 1인 판 수 | 기본 3판, 시간이 남으면 4판 | 11/8 사전 테스트의 한 판 시간 실측으로 확정 | 4판째는 비교 표본이 적다 |
| 고정 시드 판 | `base6` 910001·910002·910003·910004 | 판 순서대로 배정 | 시드를 0부터 차례로 쓰는 시뮬레이션이 910001 이상까지 가면 겹친다. 그런 작업은 이 값을 뺀다 |
| `test_human` 동결 | 11/9 14:30 | 사전 테스트 문장 + 참가자 절반(사용자 단위 무작위, 분할 시드 기록) | D-09-01 BERT 평가는 14:30 이후 |
| 설문 | `fun`·`unfair`·`npc_plausible` 각 1~5, `comment` 선택. 전체 건너뛰기는 `skipped=true`·점수 `null`(P-29) | `unfair`는 높을수록 더 억울함. 건너뜀은 평균에서 빼고 응답률 분모에만 넣음 | 보상·평균에서 방향 처리는 E-10-03·D-09-02가 정한다 |
| 지표 정의 버전 | `ut-metrics-1.0`(`tools/ut_metrics.py` `DEFINITIONS`, 11/8 사용자 확정) | 결과 JSON에 버전·정의 문장을 함께 쓴다 | 정의를 바꾸면 버전을 올린다 |
| 동결 규칙 버전 | `ut-freeze-1.0`, 정규화 `norm-1`(NFC·앞뒤 공백 제거·연속 공백 1칸) | 홀수 인원이면 평가 = floor(n/2)(11/8 확정) | 바꾸면 같은 시드로도 분할이 달라진다 |
| 내보내기 형식 | `ut-export-1` | 진실(`truth`)·`observation`·`decision`·`truth_revealed`·화면 본문은 내보내지 않는다 | - |
| 보존 기한 | 2026-12-12 | 가명 ID가 붙은 모든 사용자 테스트 데이터 삭제(C-09-02) | privacy.md 2절 |

## 한계

- 30명 동시 진행의 서버 여력은 이 계획에서 확인하지 않았다. 게임 API는 B-06-02·INT-09, LLM 추궁은 E-07-07·INT-09 측정을 따른다.
- 1인 판 수와 1인 총시간은 11/8 사전 테스트에서 실측해 확정한다. 사전 테스트는 PM 혼자 하므로 그 문장은 사용자 1명으로 보고, PM 문장을 넣은 값과 뺀 값을 함께 보고한다(E-09-02).
- 11/10 회차는 두지 않는다. 11/9에 모인 판 수가 모자라면 그때 PM이 정한다.
- 참가자 접속 안내(지원 브라우저, 인증서 경고 넘기기)는 C-07-04가 만들고, 사전 테스트 전에 받는다.
- 집계 도구 검증은 **로컬만** 했다(11/8, 로컬 Mongo 레플리카셋 + SQLite 계정 DB + `tools/perf/int07_web_run.py` 판과 자유 입력·설문 요청). 운영 DB 접속·실데이터 검증은 하지 않았다(읽기 전용 계정 미확보).
- 원하는 행동 완료율·추가 선택 횟수·취소·버튼 전환 비율·분당 추궁 수·설문 응답률 분모는 문서에 정의가 없어 도구가 낸 안을 11/8 사용자가 확정했다(설문 분모는 끝난 판 전부, 취소·전환에 `none_of_these`를 합친 `give_up_rate` 추가). 분당 추궁 수의 체류 시간은 명령 사이 간격으로 잡아, 마지막 명령 뒤 화면을 보는 시간은 판 종료·마감까지로 본다.
- Delta 경로로 집계 수치를 대조하지 않는다(11/8 사용자 결정). 원본은 게임 웹 Mongo·계정 DB이고, 동결 기록은 `manifest.json`이다.
- 문장 교집합은 정규화 후 완전 일치만 본다. 비슷한 문장(띄어쓰기 외 차이)은 잡지 않는다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| E-06-04 | 11/6 | 처음 작성: 사용자 테스트 계획 `docs/user_test/plan.md`(일정·인원·판 수, 참가자 순서, 운영자 준비물, 동의 문구 인용, 수집 항목, `test_human` 14:30 동결·사용자 단위 분할, 판 종료 설문 확정본, 고정 시드 판 `base6` 910001~910004) |
| P-29 | 11/6 | 설문 전체 건너뛰기 형식(`skipped=true`·점수 `null`)과 응답률·평균 집계 규칙 추가 |
| E-08-10 | 11/8 | 집계 도구 추가: 읽기 전용 내보내기 `server/infra/ut_export.py`, 지표·회차 등록 가명 ID `tools/ut_metrics.py`, `test_human` 분할·동결 `tools/ut_freeze.py`, 계획서 10절 지표 산출 방법. 같은 날 사용자 확정: 정의 버전 `ut-metrics-1.0`(설문 분모 끝난 판 전부, `give_up_rate`), 동결 `ut-freeze-1.0`(Delta 버전 대신 `manifest.json`) |
