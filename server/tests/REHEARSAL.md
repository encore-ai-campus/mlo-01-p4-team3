# B-09-03 리허설·결함 재현 준비

11/10 테스트 서버의 6인 판 리허설과 테스트 중 결함 대응을 준비한다. 일정·권한은 [B 태스크](../../docs/tasks/B_web.md#b-09-03-정식-사용자-테스트-지원수정), [사용자 테스트 계획](../../docs/user_test/plan.md), [장애 대처](../../ops/runbook/user_test_incident.md)가 기준이다. 이 문서는 실제 리허설 수행·배포 승인을 대신하지 않는다.

## 1. 실행 전 확인

| 항목 | 확인할 내용 | 확인 주체 |
|---|---|---|
| 후보 코드 | E가 고른 후보 SHA, 서버 실제 SHA, 리허설 후 태그 SHA를 구분해 기록 | E 선정·C 배포·B 대조 |
| 버전 | 후보 코드의 rule/engine/content 버전과 운영 설정·판 메타의 버전이 일치 | C 적재·대조, E 기록, B 화면 확인 |
| 접속·계정 | 접속 방식·인증서 안내·지원 브라우저·리허설 계정은 보호 경로로 인수. 비밀값은 기록하지 않음 | C·E |
| 게임 조립 | 실제 엔진·계정 MySQL·Mongo Replica Set·일반 판 시작 설정 확인. 녹화 미리보기와 구분 | C·B |
| 시작 조건 | 공개 프리셋 base6, 일반 판의 서버 랜덤 시드, 동의한 계정의 홈 새 게임·이어 하기 | B·E |
| 상태 확인 | `/healthz`, `/readyz`, `/metrics`와 로그인·실제 판 조회를 함께 확인. 두 probe의 성공은 계정 DB 확인이며 Mongo/Kafka 전체 준비를 보장하지 않음 | C·B |
| 소켓 | 로그인 뒤 브라우저 Network의 `/ws/game/` 101 및 게임 웹 출처만 사용하는지 확인 | B |
| 모델·광고 | 승인된 배포 설정 유지. 새 BERT 코드가 dev에 있어도 성능 검증·승인 없이 켜지 않음. 광고가 비어도 게임은 계속 진행 | C·E 판단, B 확인 |

버전 읽기(후보 커밋에서 실행):

```powershell
git rev-parse HEAD
python -c "from engine.domain import params; print(params.RULE_PARAMS_VERSION)"
python -c "from engine.domain.model import ENGINE_VERSION; print(ENGINE_VERSION)"
python -c "import json; print(json.load(open('content/bundle_v1.json', encoding='utf-8'))['content_version'])"
```

새 워크트리에는 접속용 `.env`가 없다. 기존 디자인·다른 태스크의 `.env`를 임의로 복사하지 않는다. C가 보호 경로로 전달한 계정 DB·Mongo·웹 출처·콘텐츠 버전·비밀키와 승인된 조립 설정을 운영 절차로 환경 변수에 주입한다. `server/manage.py`는 `.env`를 자동으로 읽지 않으므로 파일 작성만으로 연결되지 않는다. 주입·기동은 [프록시 운영 절차](../../ops/proxy/README.md)를 따른다. 접속 확인을 위해 운영 DB에 migrate·flush·테스트 데이터 적재를 실행하지 않는다.

## 2. 10:30~12:00 리허설 점검

E가 테스트 서버에서 6인 판을 진행하고 B가 아래 순서의 화면·API를 확인한다. 각 행에 확인 시각·결과·문제 이슈 번호를 기록한다. 자동 시험 통과를 사람 완주 결과로 바꾸지 않는다.

테스트 서버 SHA는 **리허설 시작 10:30부터 18:00 클로즈까지** 유지한다. 리허설 중에도 재배포 예외는 **E가 승인한 blocker 수정**뿐이며 C가 배포한다. 일반 결함이나 dev 병합만으로 재배포하지 않는다. 예외 배포의 버전 재기록·6인 1판 재확인은 [사용자 테스트 계획 4절](../../docs/user_test/plan.md#sha-유지)을 따른다.

| 순서 | 점검 | 기대 동작 | 시각·결과·이슈 |
|---|---|---|---|
| 1 | 로그인·동의·홈 | 인증·동의한 계정이 일반 판을 시작. 이미 진행 중인 판은 이어 하기 | 미수행 |
| 2 | 새 게임·주인공 선택 | 공개 프리셋만 선택되고 선택한 초상 표시. 중복 시작으로 판이 더 생기지 않음 | 미수행 |
| 3 | 낮·진술·수첩 | 공개 정보만 표시. 확신도 5단계 입력·메모 저장·재조회 일치 | 미수행 |
| 4 | 자유 입력 | 해석만으로 소비 없음. 후보 하나 확인·수정·재입력·취소 후 예상 상태. 오래된 카드는 실행 거부 | 미수행 |
| 5 | 심문·답변 | 지난 밤·관련 진술 등 합법 조합 안내. 폴백 먼저 표시, 완료·단계 전환 시 폴링 종료 | 미수행 |
| 6 | 투표·결선·밤 | 단계에 맞는 선택과 결과 표시. 비공개 정보 노출 없음 | 미수행 |
| 7 | 재조회·재전송·재접속 | 새로고침·이어 하기 후 상태 유지. 같은 명령 중복 소비 없음. 버전 충돌은 현재 화면으로 갱신 | 미수행 |
| 8 | 종료·복기·단서 | 종료 후 판에 고정된 버전의 결과·복기·수집 단서 표시 | 미수행 |
| 9 | 설문·건너뛰기 | 점수 제출 또는 전체 건너뛰기, 같은 제출 재시도 중복 없음. 다른 끝난 판에서도 제출 가능 | 미수행 |
| 10 | 광고·웹소켓 | 비콘/클릭이 게임 웹을 경유. 광고만 비어 있어도 게임 진행. 운영 서버에 장애를 임의 주입하지 않음 | 미수행 |
| 11 | 문제 재확인 | B의 blocker 수정·회귀검사 후 E가 반영을 승인하면 C 재배포. 버전 재기록과 E의 6인 1판 재확인 | 미수행 |

12:00 진행 판단·태그는 E, 배포는 C가 담당한다. 실패하면 12:30까지 수정·재배포·재확인한다. 해결하지 못하면 E가 A-09-05 인물 전신이 들어간 마지막 리허설·검사 통과 커밋으로 13:00 오픈을 판단한다. B가 임의로 태그·배포·순연을 결정하지 않는다.

## 3. 버그 재현 기록 양식

아래 내용을 결함 하나당 복사한다. blocker는 저장소의 [blocker 양식](../../.github/ISSUE_TEMPLATE/blocker.yml)의 항목을 채우고 영역 라벨도 붙인다. 일반 웹 결함은 `bug`·`web`으로 기록한다. 자동으로 원격 이슈를 만들거나 담당자에게 전송하지 않는다.

```markdown
제목: [증상 한 줄]
발견 시각(KST):
이슈 번호 / 기록자 흐름:
분류: blocker 또는 일반 bug / 영역(engine·web·infra·ml·ads·content·llm)
막히는 시연·흐름·INT 항목 또는 태스크:
환경: 테스트 서버 또는 로컬 대역 / 브라우저 버전·화면 폭
배포 SHA / 태그 / rule_version / engine_version / content_version:
판 정보: game_id / preset / seed / 단계 / state_version
요청 정보: request_id / API 경로 / 상태 코드 / reject 코드(있을 때)
선행 조건:
재현 순서: 1. 2. 3.
입력·선택: 개인정보를 제거한 문장 또는 합성 입력, 고른 대상·진술·카드
기대 결과와 근거: 계약 파일·절 또는 태스크 완료 조건
실제 결과:
재현 시도 수 / 성공 수 / 영향 범위:
증거: 마스킹한 화면·request_id/game_id로 찾은 최소 로그 발췌
담당 흐름 / 인계 상태 / 원인 확인 여부:
수정 SHA / 회귀검사 / E 재확인 시각·결과:
처리: 수정 완료 / 일반 bug 보류 사유·후속 B-11-01 / 조사 중
```

seed·판 버전은 권한 있는 서버 기록에서 확인하며 참가자 화면에 비공개 상태를 노출하지 않는다. 로그인 ID·이름·연락처·쿠키·CSRF·광고 토큰·접속 URL·전체 요청 본문·미마스킹 채팅·DB 연결값은 증거에서 제거한다. 전체 HAR는 그대로 첨부하지 않는다. 가림으로 재현이 어려우면 합성 입력으로 다시 재현한다.

재현 실패도 기록한다. 중복 신고는 원래 이슈에 연결해 분류 수를 부풀리지 않고, 분류한 결함 수·재현 시도 결함 수·재현 성공 결함 수·보류 수·blocker 미해결 수를 구분한다. 아직 신고를 받지 않은 준비 단계는 수치 0으로 완료를 주장하지 않고 **미집계**로 둔다.

## 4. 13:00~18:00 테스트 중 대응

1. B는 증상·막히는 흐름으로 분류하고 재현 기록을 해당 흐름에 인계한다. 시연·끝에서 끝까지 흐름·INT를 막으면 blocker이며 애매해도 blocker로 둔다. blocker는 보류로 닫지 않는다.
2. 10:30 리허설부터 적용 중인 테스트 서버 SHA 유지를 18:00까지 계속한다. blocker만 즉시 수정하며 서버 반영은 E 승인 후 C가 한다. dev 병합만으로 테스트 서버를 갱신하지 않는다.
3. 예외 배포는 계획 4절의 `v-usertest-2` 절차대로 버전 재기록·6인 1판 재확인을 포함한다. 변경 시각은 E-09-01 증빙에 남긴다.
4. 일반 `bug`·`web`의 수정·종료/보류 판정은 11/11 B-11-01에서 12:00까지 처리한다. 13:00~18:00 대량 부하·학습·장애 주입을 하지 않는다.
5. 여러 사람의 접속 불가·5xx·무한 로딩은 E가 장애 판정, C가 상태·재시작 확인한다. 판정 후 5분 내 재시작, 합계 20분 초과 시 대응 종료는 E 판단이다. 재시작 실패 후 다음 단계는 기준 런북에서 미정이므로 E·C에게 판단을 요청한다.
6. 18:00 클로즈·스냅숏은 유지한다. `backfill_surveys` 등 마감 처리는 E·C의 운영 절차로 진행하며 B가 준비 검사 중 실행하지 않는다.

## 5. 로컬 실행 준비와 확인 범위

의존성은 `requirements/web.txt`다. 기존 로컬 Python 환경을 읽기 전용으로 재사용하며 이 작업을 위해 다른 작업의 패키지를 바꾸지 않았다. Django의 MySQL 드라이버는 settings.py의 PyMySQL 조립이며 mysqlclient 별도 설치가 필수는 아니다.

```powershell
python -B server/manage.py check --settings=server.paldo_web.test_settings
python -B server/manage.py test server.tests.test_accounts server.tests.test_open_start server.tests.test_interactions server.tests.test_utterance_ui server.tests.test_participant_forms server.tests.test_phase_filter server.tests.test_preset_screens server.tests.test_meeting server.tests.test_game_end server.tests.test_review server.tests.test_story_ui server.tests.test_survey_skip server.tests.test_sessions server.tests.test_probes server.tests.test_ws server.tests.test_game_factory --settings=server.paldo_web.test_settings --noinput
node server/tests/test_game_js.cjs
node server/tests/test_ad_slots_js.cjs
node server/tests/ws_client_test.cjs
```

위 회귀검사는 메모리 SQLite·시험 대역을 사용하며 일부 실제 엔진 단위 경로를 포함한다. 실제 DB 보존·배포 환경·사람 브라우저 완주·시각 디자인 승인·성능 검증을 대신하지 않는다. MySQL 잠금/정렬과 실제 Mongo 환경이 필요한 건너뜀은 증빙에 별도로 기록한다.

기존 로컬 미리보기 도구 `python -B -m server.tests.ui_theme_preview --port <빈 포트>`는 임시 SQLite·녹화 엔진으로 조작을 연습할 때만 쓴다. 운영 `.env`·DB에 접속하지 않으며 테스트 서버 리허설이 아니다. 이번 준비에서는 디자인 미리보기 서버를 추가로 실행하지 않았다.

## 6. E·C에게 확인받을 항목

- E: 후보 SHA·리허설 계정·확인 일정, 진행 판단과 blocker 예외 배포 승인 경로. B-08-02의 현재 dev 공동 완주 기록은 별도 조건으로 유지한다.
- C: 보호 설정 주입과 실제 배포/콘텐츠 대조 결과, 브라우저 접속 안내, 실제 게임·DB·소켓 준비 확인. `/healthz`만으로 전체 스택 정상이라고 판단하지 않는다.
- E·C: 장애 런북 3절은 웹 1프로세스 유지라고 적혀 있지만 최신 E-09-10·C-08-04는 기본 2프로세스 배포를 다룬다. 실제 배포 unit·프로세스 수와 재시작·되돌림 절차를 리허설 전에 확정해 런북을 맞춰야 한다. B는 이 차이 때문에 프로세스 수를 임의 변경하지 않는다.
- E·C: 재시작 실패 후 조치는 장애 런북에서 미정이다. 현장 판단·연락 경로를 확인한다.

[사전 검증 결과·미수행 조건](../../docs/evidence/B-09-03.md)
