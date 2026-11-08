# PJ4 전용 Mongo DB 전환

`team3ops`는 서버 OS의 SSH 계정이다. MongoDB 관리자는 별도 DB 계정이며 서버 sudo 권한과 구분한다. 이번 전환은 최초 설치 폴더의 기존 관리자 보호 JSON(0600)을 찾아 **정상 관리자 인증**을 검증한 뒤 수행했다. 실제 경로·계정·암호는 비공개 인계 기록에만 둔다. 인증 비활성화, cluster key를 이용한 관리자 로그인, 기존 계정 비밀번호 변경은 하지 않았다.

## 계획과 적용

`python -m ops.provision_mongo`는 Linux 관리 VM에서 mongosh와 공개 TLS CA를 사용한다. 기본은 읽기 전용 계획이다. 실제 대상 DB에 컬렉션이나 사용자가 있으면 중단한다. source/target이 같거나 시스템 DB이면 거절한다.

```sh
python -m ops.provision_mongo --admin-profile "$PRIVATE_ADMIN_PROFILE" --ca "$PUBLIC_MONGO_CA" --seed "$MONGO_SEED_HOST" --replica-set "$MONGO_RS" --source "$OLD_APP_DB" --target "$DEDICATED_APP_DB" --app-user "$NEW_APP_ROLE" --profile-out "$PRIVATE_NEW_APP_PROFILE" --backup-dir "$PRIVATE_NEW_BACKUP_DIR"
# 웹 입력·발행기를 아래 절차로 멈춘 뒤 같은 명령에 --apply를 추가한다.
```

관리자 JSON은 username/password를 갖고 서버 안에서만 읽힌다. stdout·명령 인자에는 값을 넣지 않는다. 새 앱 비밀번호는 crypto 난수로 만들며 대상 DB의 readWrite 역할만 부여한다. 앱 보호 프로필은 root 전용 디렉터리의 0600 파일이다. 기존 앱 계정·원본 DB는 수정하거나 지우지 않는다.

복사 대상은 games, progress, content, b_test_sessions, b_ad_identities, b_interactions 여섯 개다. 기존 PJ3의 다른 컬렉션·사용자는 복사하지 않는다. 원본 BSON은 확장 JSON으로 별도 0700/0600 백업에 저장하고 컬렉션 옵션·인덱스·행 수·canonical SHA-256을 manifest로 남긴다. 새 DB의 각 복사 결과를 다시 읽어 전체 hash와 수를 대조한다. 준비 중 프로필의 ready는 false이며 복사·사용자 생성이 끝나야 true다. 중간 실패 시 기존 DB를 유지하고 이전 앱 설정으로 재개한다. 부분 대상이나 프로필을 자동 삭제·덮어쓰지 않는다.

## 운영 전환과 롤백

1. 실제 참가자가 없음을 확인하고 원래 웹 runtime/Mongo 프로필을 root 전용 파일로 보존한다. 이번에는 합성 계정만 존재했다.
2. 웹 입력을 중지하고 Outbox pending=0까지 기다린 후 발행기를 중지한다. 원본 쓰기가 멈춘 상태에서만 `--apply`한다. 도구의 pending 검사만으로 동시 쓰기 중 스냅샷 일관성이 보장되는 것은 아니다.
3. 검증된 **새 앱 프로필만** 보호 관리 경로로 웹에 전달한다. 관리자 프로필은 웹에 전달하지 않는다. 웹의 PALDO_MONGO_URI에 대상 DB/authSource·기존 RS·TLS CA를 설정하고 파일 owner/group/mode를 보존해 원자 교체한다.
4. 웹·Outbox를 재개한다. 실패 시 원래 runtime을 복구하고 두 서비스를 재개한다. 기존 DB는 롤백용으로 보존한다. 전환에는 180초 자동 재개 타이머를 병행했고 성공 후 해제했다.
5. 전용 앱 계정으로 콘텐츠 읽기·자기 DB 쓰기·다른 DB 읽기 거부(ERROR 13), 기존 회차·게임 재개·브라우저 wss 101을 확인한다. 새 합성 HTTPS 게임을 끝내고 Mongo/Kafka/Delta ID·seq·hash를 대조한다. 임시 검증 리스너는 종료한다.

새 DB가 쓰기를 받은 뒤에는 원래 URI로 단순 복귀하면 안 된다. 새로 쓴 게임·진척·Outbox를 별도 백업·대조하고 동기화 범위를 검토해야 한다. 이전 검증 단계의 runtime 백업과 현재 전환 백업도 구분한다.

11/8 실제로 6개 컬렉션·24문서를 복사했고 hash 불일치는 0이었다. 전환 후 기존 브라우저 세션의 wss 재연결과 새 합성 게임 완료도 확인했다. 이전 API/브라우저 검증 상태는 같은 호스트에 유지하며 쿠키는 전송하지 않았다. 자세한 증빙은 [C-06-01](../docs/evidence/C-06-01.md)을 따른다.

백업은 진실·판 상태를 포함하므로 root만 읽고 저장소에 넣지 않는다. 앱 계정은 다른 DB를 읽지 못한다. 기존 관리자 파일도 제한된 관리 경로에 유지한다. 복구·새 대상 재시도 전에 현재 데이터·manifest·프로필 ready 상태를 확인하며 기존 DB나 운영 중인 새 DB 위에 다시 복사하지 않는다.
