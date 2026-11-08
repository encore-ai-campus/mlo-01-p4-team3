# 사용자 테스트 배포 준비

E가 만든 `v-usertest-1` 또는 `v-usertest-2`가 선행이다. `ops/user_test_ops.py release --repo "$REPO" --tag "$E_TAG" --out "$PRIVATE_RELEASE_ZIP"`는 존재하는 태그의 commit만 archive하고 manifest에 tag/SHA를 고정한다. dev/main/SHA 직접 지정과 기존 파일 덮어쓰기는 거절한다. 이 도구 자체가 배포 완료를 의미하지 않는다.

배포 전 후보의 `content/bundle_v1.json`과 운영 Mongo `content-1.0`을 DTO로 읽고 정규 직렬화 SHA-256을 비교한다. 불일치하면 E가 content_version을 올린 변경을 병합한 뒤 그 태그를 다시 확인하고 적재한다. 현재 후보 대조 결과와 E 확정 태그 상태는 C-08-04 증빙을 본다.

배포는 태그 SHA별 디렉토리에서 venv 의존성·정적 파일·콘텐츠를 검사하고 보호 런타임 source를 그 절대 경로에 고정한 뒤 웹·outbox를 시작한다. Caddy 정적 경로도 같은 릴리스를 가리켜야 한다. 리허설 이후 git pull/dev 추종을 하지 않는다. 이전 source 설정·정적 경로를 보존해 E 승인 rollback을 할 수 있게 한다. 실제 서버 SHA와 태그 SHA를 대조하고 증빙에 기록하기 전에는 배포 완료로 처리하지 않는다.

집계 계정은 정상 Mongo 관리자 인증으로 read 역할만 만든다. `ops/user_test_ops.py reader --admin-profile "$PRIVATE_ADMIN_FILE" --ca "$PRIVATE_CA" --seed "$PRIVATE_MONGO_SEED" --out "$CONFIRMED_4TH_PRIVATE/reader.json"`을 해당 보호 폴더에 접근 가능한 운영 호스트에서 실행한다. 출력 경로는 기존 4th_private 아래의 새 보호 파일이어야 한다. 읽기 성공과 실제 insert의 code 13을 확인하며 비밀번호·URI·계정 원문은 stdout에 내지 않는다. 폴더 위치가 확인되지 않으면 계정을 만들지 않는다.

D 인계에는 보호된 광고 DB 프로필·TLS 파일 위치, Garage 공용 클라이언트 설정 위치, 읽기 허용 prefix를 확인한 기록을 저장소 밖에 둔다. PJ4 쓰기는 C 파이프라인만 하며 D는 공용 키로 쓰지 않는다. E/D가 실제로 읽을 수 있는 전달 위치를 확인하기 전에는 전달 완료라고 기록하지 않는다.
