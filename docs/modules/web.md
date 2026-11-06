# 게임 웹

## 무엇을

Django가 게임 JSON API와 회의 화면을 제공한다. 응용 서비스의 공개 상태를 화면 모델로 바꾸고, htmx 행동 요청의 결과를 회의 조각으로 돌려준다.
화면 이미지는 manifest의 콘텐츠 ID로 찾고, Django 상태 확인 경로와 요청 지표로 웹 서비스 상태를 확인한다.

## 왜

[API 계약 1·3·6절](../contracts/api.md)에 따라 JSON 응답과 htmx 조각에 같은 게임 상태를 쓰며, 진행 화면에는 플레이어에게 공개된 데이터만 전달한다.
[에셋 규격 4절](../assets/spec.md)에 맞춰 파일 경로를 manifest에서 찾아 이미지 교체 시 화면 경로를 그대로 쓴다.

## 사용법

저장소 루트에서 Django 6.1.2가 설치된 Python으로 실행한다.

```powershell
python -B server/manage.py test server.tests --settings=server.paldo_web.test_settings --noinput
python -B server/preview.py --port 8769
```

미리보기는 `http://127.0.0.1:8769/accounts/login`에서 연다. 실행 콘솔에 표시되는 미리보기 계정으로 로그인하면 녹화 판으로 이동한다. 녹화 판은 기록된 입력 순서에 맞춰 행동을 선택한다.

[화면 라우팅](../../web/ROUTING.md)과 [화면 설계](../../web/DESIGN.md)에 요청별 응답과 갱신 규칙을 정리했다.

## 설정값

| 이름 | 기본값 | 뜻 | 바꿀 때 영향 |
|---|---|---|---|
| `PALDO_PUBLIC_ORIGIN` | 필수 | 브라우저가 접속하는 게임 웹 출처 | 허용 호스트·CSRF 출처·보안 쿠키가 바뀜 |
| `PALDO_GAME_SERVICE_FACTORY` | 비어 있음 | 운영 게임 서비스를 조립하는 함수 경로 | 운영 게임 API가 사용하는 서비스가 바뀜 |
| `ASSET_MANIFEST` | `content/images/manifest.json` | 콘텐츠 ID, URL, 이미지 경로 목록 | 화면 조회와 정적 이미지 응답에 함께 적용 |
| 미리보기 `--port` | 8766 | 로컬 HTTP 포트 | 루프백 미리보기 접속 주소가 바뀜 |

## 한계

미리보기는 임시 SQLite와 녹화 엔진, 메모리 게임 저장소를 사용한다. 실제 계정·회차와 배포 프록시는 각 태스크에서 연결한다. 투표·밤 전용 화면, 복기 UI와 광고 지면 호출은 후속 작업이다.

## 변경 이력

| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| B-05-01 | 11/5 | Django 설정, 상태 확인, 요청 로그와 계측 |
| B-05-02 | 11/5 | 게임 JSON API 열 개, 입력 DTO 검증, 가짜 엔진 회귀 시험 |
| B-05-03 | 11/5 | 회의 전체·조각 화면, htmx 행동 폼, manifest 이미지 연결 |
