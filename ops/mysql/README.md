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

## PJ4 전용 복제 복구

11/8 실제 복구는 기존 mysql-02의 3306 인스턴스에 **named channel `pj4`**를 추가했다. 기본 PJ3 GTID 채널과 그 데이터는 보존했으며 해당 채널의 과거 binlog 만료는 해결한 것으로 기록하지 않는다. RESET·GTID 주입·PJ3 데이터 덮어쓰기는 하지 않았다.

Primary 관리자 소켓에서 `python -m ops.mysql.pj4_channel snapshot --directory "$PRIVATE_SNAPSHOT"`으로 두 스키마만 일관 dump한다. `--source-data=2 --set-gtid-purged=OFF`의 파일/위치와 SHA-256을 manifest에 기록한다. 디렉터리 0700·파일 0600으로 보호하며 복사도 보호된 관리 경로로 한다. Replica의 **비어 있는 PJ4 스키마**에서만 `configure`를 실행한다. 기존 PJ4 테이블·기존 채널이 있으면 거절한다. 새 스냅샷을 이미 운영 중인 PJ4 데이터 위에 다시 복원하면 안 된다.

복원 완료 표시·스키마·덤프 해시·좌표를 재검증하고 `SOURCE_AUTO_POSITION=0`인 TLS 채널을 만든다. `start-channel`은 복원 완료 표시가 있어야 하며 이미 있는 채널을 다시 설정하지 않는다. 기존 복제 자격정보는 로컬 소켓에서 메모리로만 읽는다. 기존 GTID·스키마와 이전 채널 설정은 관리자 전용 백업에 보관한다. 중간 실패 시 백업·완료 표시를 확인하고 새 디렉터리에서 수동 검토하며 자동 재복원을 하지 않는다.

`/etc/mysql/mysql.conf.d/pj4-channel.cnf`는 비밀 없는 필터만 담아 0644로 유지한다. `mysqld --validate-config`와 Replica 재시작 후 IO/SQL 및 새 marker를 다시 확인했다. 검사에는 반드시 `--channel pj4`를 준다. Primary/Replica가 함께 동작해야 하며 초기 스냅샷은 재부팅용 복원 파일이 아니다.

## 앱 호스트 설정과 기동

11/11 C-11-01 부하 검증에서 Primary 접속 한도40·최대 사용41·접속 초과를 확인했다. 4GB VM에서 메모리 여유를 확인하고 원값을 보호 백업한 뒤 한도64를 시험했다. 새 접속 초과0과 여유 메모리를 재확인해 `SET PERSIST max_connections=64`로 보존했다. 재시작 없이 적용한다. [결과와 미완료 성능](../../docs/evidence/C-11-01.md)

```sh
# 해당 MySQL 호스트의 관리자 소켓에서만 실행한다.
sudo mysql --no-defaults --protocol=socket -uroot -e "SHOW GLOBAL VARIABLES LIKE 'max_connections'; SHOW GLOBAL STATUS LIKE 'Connection_errors_max_connections';"
free -m
# 롤백이 필요하면 활성 연결 수와 보호 기록의 원값을 확인한다(이번 원값40).
# Replica 승격 전에는 승격 대상의 접속 용량도 확인한다.
```

`service_runtime.py --profile "$PRIVATE_RUNTIME" <role>`은 보호 JSON의 `source`, `environment`, 선택 `kafka_config`를 읽어 같은 venv 프로세스로 교체한다. 역할은 web, ads, migrate-web, migrate-ads, collectstatic, load-content, outbox, publish, expire, refund다. 주소·계정·암호·키를 인자로 전달하지 않는다. 웹은 loopback:8000·8002(`web --port`, 프로세스 2개, E-09-10·[proxy README](../proxy/README.md)), 광고는 보호 설정의 내부 bind:8001이다. 광고는 gunicorn gthread(프로세스 1개·스레드 16, `ads.project.wsgi:application`)로 띄워 스레드별 DB 연결을 재사용한다(11/11, `CONN_MAX_AGE` 기본 60초, `PALDO_ADS_DB_CONN_MAX_AGE`). daphne(ASGI)는 요청마다 스레드를 새로 만들어 연결을 재사용하지 못한다. 되돌릴 때는 보호 JSON에 `"ads_server": "daphne"`(또는 환경 변수 `PALDO_ADS_SERVER=daphne`)를 넣고 재시작하면 `ads.project.asgi:application`으로 뜬다.

웹 `PALDO_AUTH_DB_SSL_CA`, 광고 `PALDO_ADS_DB_SSL_CA`는 각각 인증서·호스트 검증을 강제한다. CA는 공개 파일이지만 DB 계정 프로필은 자기 서비스만 읽는다. 양쪽 실제 호스트의 migration·자기 스키마 TLS·상대 스키마 1044와 실제 브라우저 가입/로그인을 확인했다. 광고 직접 HTTP 제공·노출·클릭도 MySQL Outbox·Kafka·bronze까지 확인했다. 실제 파일 경로·서비스명·기동/종료 명령은 비공개 운영 인계 기록을 따른다.

MySQL 실측 당시 Mongo는 허용 DB의 pilot이었으며, 이후 C-06-01 후속 작업에서 정상 관리자 인증으로 전용 DB·계정 분리를 완료했다(ops/mongo.md). DB 승격 자동화·PJ3 기본 채널 복구·B의 실제 광고 HTTP 어댑터는 이 작업에서 완료로 주장하지 않는다.
