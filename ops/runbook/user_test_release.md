# 사용자 테스트 배포 준비

11/10 09:00–10:00에 E가 선택한 후보의 전체 SHA를 받아 `python -m ops.user_test_ops release --repo "$REPO" --candidate "$E_SHA" --out "$PRIVATE_RELEASE_ZIP"`로 묶는다. 먼저 `origin/dev`를 갱신한다. 도구는 `origin/dev`에 포함된 전체 커밋 SHA를 확인해 manifest에 기록한다. 10:30–12:00 리허설을 통과하면 E가 같은 SHA에 `v-usertest-1`을 달고, 운영 SHA와 태그 SHA가 일치하는지 확인한다. E가 예외 배포를 승인한 경우의 `v-usertest-2` 묶음은 기존 `--tag "$E_TAG"` 경로로 준비할 수 있다.

배포 전 후보의 `content/bundle_v1.json`과 운영 Mongo에서 같은 버전의 DTO를 읽어 정규 직렬화 SHA-256을 비교한다. `content-1.0.1`이 없다면 후보의 `python -m tools.load_content content/bundle_v1.json`으로 적재하고 다시 대조한다. 같은 버전의 내용이 다르면 E가 버전을 올린 후보를 확정할 때까지 기다린다. 배포 후 `PALDO_CONTENT_VERSION`이 후보와 일치하는지, 로그인한 일반 판 시작이 성공하는지 확인한다.

배포는 후보 SHA별 디렉토리에서 venv 의존성·정적 파일·콘텐츠를 검사하고 보호 런타임 source를 그 절대 경로에 고정한 뒤 웹·outbox를 시작한다. Caddy 정적 경로도 같은 릴리스를 가리켜야 한다. 리허설부터 18:00까지 그 SHA를 유지한다. 이전 source 설정·정적 경로를 보존해 E가 승인한 경우 복구할 수 있게 한다.

## 웹 프로세스 전환과 부하 측정

[프록시 필수 절차](../proxy/README.md#웹-프로세스-2개-e-09-10)로 Daphne 8000·8002와 InMemory 채널 레이어를 준비한다. 전환 전에 현재 Caddy 바이너리에서 실제 운영 환경값으로 새 Caddyfile을 검증한다. `health_fails`와 `health_passes`를 읽지 못하는 버전에서는 새 설정 적용을 중단하고 호환 바이너리로 다시 검증한다. 두 웹 프로세스의 `/healthz`와 C-08-01에서 정한 지표 대상을 확인한 뒤 Caddy의 HTTPS·웹소켓을 확인한다.

후보 배포 직후 해당 서버에서 프록시 문서의 비교 명령을 `--web-processes 2,1 --channel-layer memory --front binary`로 실행한다. 두 경우 각각 30명·3분을 측정하고 2프로세스의 커밋이 두 포트에 분산됐는지 확인한다. p95 기준은 1초이며 초과하면 E에게 수치를 전달한다. 시험용 Daphne·Caddy와 시험용 게임 엔진이 내는 비교 결과를 운영 후보의 실제 엔진 처리량과 구분해 기록한다.

## 11/10 18:00 마감

1. E의 마감 시각인 **2026-11-10 18:00 KST(09:00 UTC)** 이후에 실행한다. 보호된 웹 런타임 프로필을 사용하는 후보 SHA의 소스 및 venv에서 `python server/manage.py backfill_surveys`를 한 번 실행한다. 마감 전에 실행하면 작성 중인 설문을 닫을 수 있다.
2. 명령이 성공하면 운영의 읽기 전용 내보내기로 games 및 accounts 스냅숏을 확보한다. 집계와 분할의 `--cutoff`는 실제 명령 실행 시각이 아니라 18:00 KST로 고정한다. 완료 건수와 실행 시각을 남긴다. 마감 전에 끝난 판의 일괄 건너뜀은 ut-metrics-1.2 규칙에 따라 집계한다.

## 게임 화면 되돌림

E가 요청하면 B-10-07이 확정한 화면 설정 값을 보호 프로필에 적용하고 `sudo systemctl restart paldo-web@8000 paldo-web@8002`로 두 인스턴스를 다시 시작한다. 두 인스턴스의 `/healthz`와 HTTPS 게임 화면을 확인하고 시각·설정·E 요청을 기록한다. 화면 설정 이름은 B-10-07 문서를 기준으로 한다. 웹을 1프로세스로 줄이는 경우는 프록시 문서의 별도 절차를 따른다.

집계 계정은 정상 Mongo 관리자 인증으로 read 역할만 만든다. `ops/user_test_ops.py reader --admin-profile "$PRIVATE_ADMIN_FILE" --ca "$PRIVATE_CA" --seed "$PRIVATE_MONGO_SEED" --out "$CONFIRMED_4TH_PRIVATE/reader.json"`을 해당 보호 폴더에 접근 가능한 운영 호스트에서 실행한다. 출력 경로는 기존 4th_private 아래의 새 보호 파일이어야 한다. 읽기 성공과 실제 insert의 code 13을 확인하며 비밀번호·URI·계정 원문은 stdout에 내지 않는다. 폴더 위치가 확인되지 않으면 계정을 만들지 않는다.

D 인계에는 보호된 광고 DB 프로필·TLS 파일 위치, Garage 공용 클라이언트 설정 위치, 읽기 허용 prefix를 확인한 기록을 저장소 밖에 둔다. PJ4 쓰기는 C 파이프라인만 하며 D는 공용 키로 쓰지 않는다. E/D가 실제로 읽을 수 있는 전달 위치를 확인하기 전에는 전달 완료라고 기록하지 않는다.
