# PJ4 광고·계정 MySQL

Primary의 `paldo_ads`·`paldo_auth`는 각각 전용 계정으로 분리한다. D 광고 서버와 B 게임 웹의 정확한 출발 호스트만 허용한다. 검증을 위해 Primary 자기 주소도 허용한다. 두 계정 모두 TLS 필수이며 다른 스키마·글로벌 권한·GRANT OPTION을 부여하지 않는다. 마이그레이션을 위해 자기 스키마 안의 DDL 권한은 포함한다.

## 생성·검증

Primary의 로컬 관리자 소켓에서 실행한다. 주소·비밀은 저장소 밖 인벤토리/보호 파일에서 주입한다. 기본은 계획만 출력하며 `--apply`를 명시해야 생성한다. 기존 DB·계정·복제 설정을 지우거나 비밀번호를 바꾸지 않는다. 같은 이름의 비관리 계정이 있으면 중단한다.

```sh
python -m ops.mysql.provision --secrets-dir "$PRIVATE_MYSQL_DIR" --ads-host "$ADS_HOST" --auth-host "$AUTH_HOST" --db-host "$DB_HOST" --ca "$MYSQL_CA" --apply
python -m ops.mysql.verify --secrets-dir "$PRIVATE_MYSQL_DIR"
# 같은 marker로 각 호스트에서 순서대로 실행
python -m ops.mysql.replication_check --role primary --marker c06_handoff
python -m ops.mysql.replication_check --role replica --marker c06_handoff
```

관리 디렉터리는 0700, 역할별 JSON·로컬 검증 option 파일은 0600이다. 운영 주입은 역할별 JSON에서 `PALDO_ADS_DB_HOST/PORT/NAME/USER/PASSWORD` 또는 `PALDO_AUTH_DB_*`로 매핑한다. CA를 앱에 배치하고 Django 연결에서 TLS `VERIFY_IDENTITY`를 강제한다. JSON의 CA 경로는 DB 호스트 기준이므로 앱 호스트 경로로 조정한다. 광고 자격증명을 계정 웹에 전달하거나 그 반대의 전달을 하지 않는다. 보호 파일의 실제 위치는 비공개 인계 기록을 따른다. 앱 호스트별 연결·마이그레이션 확인은 추가로 필요하다.

## 현재 복제 장애와 재구성 계획

11/7 실제 점검: Replica IO 중단·SQL 동작. 기존 설정을 유지한 IO 재개 후 Primary 연결은 됐지만 오류 1236으로 실패했다. Primary에서 필요한 과거 binlog가 이미 purged되어 자동 GTID 재개가 불가능하다. Replica가 적용한 마지막 Primary 거래보다 현재 Primary 거래가 뒤에 있고, Replica에는 자기 UUID 거래도 있어 기존 데이터를 무조건 덮어쓰면 안 된다. 지연은 NULL이며 정상 0으로 기록하지 않는다. 새 probe의 복제까지 성공하기 전 완료로 닫지 않는다.

복구 시 실행 범위는 mysql-02 하나다. 먼저 Primary와 Replica 각각의 논리 백업·GTID·설정·사용자/권한을 암호화된 저장소 밖 경로에 보존하고 복원 시험 및 Replica 자체 거래 비교를 한다. Primary 쓰기 일관성을 확보한 새 스냅샷을 만들고, Replica 데이터 대체 범위와 롤백 지점을 검토한다. 검토한 범위만 새 스냅샷으로 복원하고 기존 TLS·복제 계정으로 GTID 재개한다. 마지막으로 IO/SQL 모두 Yes, 지연 유효값, 위 probe와 실제 앱 쓰기의 조회 일치를 확인한다. 이 PR은 데이터 대체·GTID skip·RESET REPLICA·인증 우회를 실행하지 않는다.

재발 방지를 위해 사용자 테스트 중 Primary·Replica를 함께 기동하고, IO 중단 경보와 binlog 보존 기간/디스크 예산을 운영자가 함께 정한다. 보존 기간만 늘려도 이미 만료된 거래는 돌아오지 않는다.
