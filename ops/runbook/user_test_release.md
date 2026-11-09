# 사용자 테스트 배포 준비

11/10 09:00–10:00에 E가 선택한 후보의 전체 SHA를 받아 `python -m ops.user_test_ops release --repo "$REPO" --candidate "$E_SHA" --out "$PRIVATE_RELEASE_ZIP"`로 묶는다. 먼저 `origin/dev`를 갱신한다. 도구는 `origin/dev`에 포함된 전체 커밋 SHA를 확인해 manifest에 기록한다. 10:30–12:00 리허설을 통과하면 E가 같은 SHA에 `v-usertest-1`을 달고, 운영 SHA와 태그 SHA가 일치하는지 확인한다. E가 예외 배포를 승인한 경우의 `v-usertest-2` 묶음은 기존 `--tag "$E_TAG"` 경로로 준비할 수 있다.

배포 전 후보의 `content/bundle_v1.json`과 운영 Mongo에서 같은 버전의 DTO를 읽어 정규 직렬화 SHA-256을 비교한다. `content-1.0.1`이 없다면 후보의 `python -m tools.load_content content/bundle_v1.json`으로 적재하고 다시 대조한다. 같은 버전의 내용이 다르면 E가 버전을 올린 후보를 확정할 때까지 기다린다. 배포 후 `PALDO_CONTENT_VERSION`이 후보와 일치하는지, 로그인한 일반 판 시작이 성공하는지 확인한다.

배포는 후보 SHA별 디렉토리에서 venv 의존성·정적 파일·콘텐츠를 검사하고 보호 런타임 source를 그 절대 경로에 고정한 뒤 웹·outbox를 시작한다. Caddy 정적 경로도 같은 릴리스를 가리켜야 한다. 리허설부터 18:00까지 그 SHA를 유지한다. 이전 source 설정·정적 경로를 보존해 E가 승인한 경우 복구할 수 있게 한다. E-09-10의 프로세스 구성과 측정 옵션이 들어오면 배포 직후 실제 Daphne에서 30명·3분 조건으로 웹 프로세스 2개와 1개를 각각 측정한다.

집계 계정은 정상 Mongo 관리자 인증으로 read 역할만 만든다. `ops/user_test_ops.py reader --admin-profile "$PRIVATE_ADMIN_FILE" --ca "$PRIVATE_CA" --seed "$PRIVATE_MONGO_SEED" --out "$CONFIRMED_4TH_PRIVATE/reader.json"`을 해당 보호 폴더에 접근 가능한 운영 호스트에서 실행한다. 출력 경로는 기존 4th_private 아래의 새 보호 파일이어야 한다. 읽기 성공과 실제 insert의 code 13을 확인하며 비밀번호·URI·계정 원문은 stdout에 내지 않는다. 폴더 위치가 확인되지 않으면 계정을 만들지 않는다.

D 인계에는 보호된 광고 DB 프로필·TLS 파일 위치, Garage 공용 클라이언트 설정 위치, 읽기 허용 prefix를 확인한 기록을 저장소 밖에 둔다. PJ4 쓰기는 C 파이프라인만 하며 D는 공용 키로 쓰지 않는다. E/D가 실제로 읽을 수 있는 전달 위치를 확인하기 전에는 전달 완료라고 기록하지 않는다.
