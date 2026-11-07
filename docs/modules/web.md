# 게임 웹

## 무엇을

Django가 게임 JSON API와 회의 화면을 제공한다. 응용 서비스의 공개 상태를 화면 모델로 바꾸고, htmx 행동 요청의 결과를 회의 조각으로 돌려준다.
화면 이미지는 manifest의 콘텐츠 ID로 찾고, Django 상태 확인 경로와 요청 지표로 웹 서비스 상태를 확인한다.
계정 화면은 가입·로그인·로그아웃을 처리한다. 게임 API는 계정에서 발급한 가명 ID로 판과 하위 자원의 소유권을 확인한다.
운영자는 테스트 회차에 참가자와 고정 시드 목록을 등록한다. 참가자는 자신의 회차에서 등록 순서대로 판을 진행한다.

## 왜

[API 계약 1·3·6절](../contracts/api.md)에 따라 JSON 응답과 htmx 조각에 같은 게임 상태를 쓰며, 진행 화면에는 플레이어에게 공개된 데이터만 전달한다.
[에셋 규격 4절](../assets/spec.md)에 맞춰 파일 경로를 manifest에서 찾아 이미지 교체 시 화면 경로를 그대로 쓴다.
[개인정보 계약](../contracts/privacy.md)에 따라 로그인 정보와 플레이 기록은 분리해 보관한다. 계정 화면은 Django 세션 인증과 CSRF 검사를 사용한다.

## 사용법

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests --settings=server.paldo_web.test_settings --noinput
python -B server/preview.py --port 8769
```

미리보기는 `http://127.0.0.1:8769/accounts/login`에서 연다. 실행 콘솔에 표시되는 미리보기 계정으로 로그인하면 녹화 판으로 이동한다. 녹화 판은 기록된 입력 순서에 맞춰 행동을 선택한다.

[화면 라우팅](../../web/ROUTING.md)과 [화면 설계](../../web/DESIGN.md)에 요청별 응답과 갱신 규칙을 정리했다.

계정 화면은 `/accounts/signup`, `/accounts/login`, `/accounts/logout`을 사용한다. 새 계정 스키마는 환경 변수를 설정하고 저장소 루트에서 `python server/manage.py migrate`로 준비한다.
로그인 성공 시 기존 로그인 여부와 관계없이 세션 ID를 교체한다.

1. 현장에서 본인임을 확인한 뒤 `python server/manage.py changepassword <로그인 ID>`를 실행한다. 새 비밀번호는 가입과 같은 규칙으로 검사되며 변경 전 세션은 무효가 된다.
2. 로그인 잠금을 해제하려면 `python server/manage.py unlock_account`를 실행하고 프롬프트에 로그인 ID를 입력한다.

고정 회차는 운영자 로그인 뒤 `/admin/sessions`에서 등록한다. 회차 ID, 참가자 화면에 표시된 가명 ID, 실행에 쓸 규칙·엔진·콘텐츠 버전을 입력한다. 고정 판 목록은 [사용자 테스트 계획의 순서](../user_test/plan.md#8-고정-시드-판-목록)에 따라 `preset_id`와 `seed`를 가진 JSON 배열로 입력한다. 참가자는 로그인 뒤 홈에서 다음 판을 시작하거나 진행 중인 판을 이어 간다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `PALDO_PUBLIC_ORIGIN` | 필수 | 브라우저가 접속하는 게임 웹 출처 | 허용 호스트·CSRF 출처·보안 쿠키가 바뀜 |
| `PALDO_AUTH_DB_*` | [통합 계약 4.1](../contracts/integration.md) | Django 계정 MySQL 접속값 | 인증·세션·관리자 테이블의 연결 대상이 바뀜 |
| `PALDO_SIGNUP_INVITE_CODE` | 비어 있음 | 비어 있으면 공개 가입 | 값이 있으면 일치하는 초대 코드를 제출해야 가입 가능 |
| `SESSION_COOKIE_AGE` | 1,209,600초 | 세션 유효 기간 | 새 세션의 기본 유효 기간이 바뀜 |
| `PALDO_GAME_SERVICE_FACTORY` | 비어 있음 | 운영 게임 서비스를 조립하는 함수 경로 | 운영 게임 API가 사용하는 서비스가 바뀜 |
| `PALDO_MONGO_URI` | 비어 있음 | 회차·광고 가명 저장소 접속값 | DB 경로 생략 시 `paldo_web`을 사용한다. 미설정 시 운영 회차 저장소를 사용할 수 없다 |
| `ASSET_MANIFEST` | `content/images/manifest.json` | 콘텐츠 ID, URL, 이미지 경로 목록 | 화면 조회와 정적 이미지 응답에 함께 적용 |
| 미리보기 `--port` | 8766 | 로컬 HTTP 포트 | 루프백 미리보기 접속 주소가 바뀜 |

## 한계

미리보기는 임시 SQLite와 녹화 엔진, 메모리 게임 저장소를 사용한다. 운영 게임 서비스는 C의 게임·진척 저장소를 조립하고, B의 기본 회차 정책을 사용할 때 `prepare_start`를 비워 둔다. 배포 계정 DB 권한, 운영 게임 저장소, 실제 광고 요청의 식별자는 통합 환경에서 확인한다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-05-01 | 11/5 | Django 설정, 상태 확인, 요청 로그와 계측 |
| B-05-02 | 11/5 | 게임 JSON API 열 개, 입력 DTO 검증, 가짜 엔진 회귀 시험 |
| B-05-03 | 11/5 | 회의 전체·조각 화면, htmx 행동 폼, manifest 이미지 연결 |
| B-06-01 | 11/6 | 진술표, 투표·결선·감금 공개, 밤 점 대상 화면 |
| B-06-02 | 11/6 | 같은 ID 재생, 버전 충돌, 동시 요청 시험. 성능 시험은 `perf.md` 참고 |
| B-06-03 | 11/6 | 운영자 회차 관리, 참가자 고정 시드 배정, 광고 가명 대응표 |
| B-06-04 | 11/6 | 가입·로그인·잠금·계정 저장소 검증. VM DB와 광고 요청 확인 대기 |
