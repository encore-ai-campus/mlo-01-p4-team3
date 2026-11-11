# 개인정보 삭제·보존

현장에서 본인 가명을 확인한 요청은 받은 날 처리하며 늦어도 24시간 안에 Kafka 밖 삭제와 재유입 차단을 확인한다. 삭제 실행 전 영향 범위를 가명으로 대조한다. 완료 건수와 실패 단계만 운영 기록에 남긴다.

| 위치 | 연결 키 | 작업 |
|---|---|---|
| 계정 MySQL | `accounts_accountprofile.player_pseudonym` → 사용자 PK | 계정을 비활성화하고 인증 세션을 삭제한다. 다른 위치의 삭제 검증 후 프로필과 사용자 행을 삭제한다. |
| 게임 Mongo | `games.state.meta.player_pseudonym`, `progress._id`, `b_test_sessions.participants`, `b_ad_identities.player_pseudonym`, `b_interactions`의 판 ID | 판 ID 및 광고 세션 가명을 먼저 확보한다. 판과 아웃박스·진척·상호작용을 제거하고 회차 참가자 배열에서 가명을 뺀다. 광고 키 확보 후 대응 행을 지운다. |
| 광고 MySQL | 세션 가명 → 저장 요청·serve·원본 이벤트·과금 ID | 광고 session·request·outbox와 연결된 receipt/과금·환입을 찾아 정리한다. 원장 정리는 광고 책임자와 잔액·집계를 다시 대사한 뒤 적용한다. |
| Kafka | 게임 가명, 광고 세션 가명 및 연결 이벤트·과금 키 | 삭제 목록으로 재적재를 막고 보존 설정으로 원본이 소멸하도록 한다. |
| Delta 및 Garage | Bronze 원본 가명, Silver/Gold의 가명·판·serve·원본 키와 모든 파생 경로 | 삭제 후 영향받은 파생 집계를 재작성한다. 이전 버전의 실제 제거는 쓰기 중지·보존 정책을 확인한 뒤 VACUUM으로 수행하고 읽기 가능 여부를 재검증한다. |
| MLflow·추출 데이터·`out/` | 가명 분할 키, 스냅숏·실행 manifest의 입력 경로 | 포함된 아티팩트·추출·사본을 찾고 삭제·재생성한다. 이미 운영 중인 모델은 다음 재학습부터 제외하고 모델 버전을 기록한다. |
| AWS 복사본 | 배포 인벤토리의 해당 가명 포함 객체·DB 백업 | AWS 태스크는 보류 상태다. 복사본이 있는 경우에만 삭제 작업에 포함한다. |

## 대상 확정과 재유입 차단

웹 운영 설정에서 승인된 가명 하나의 계정과 인증 세션을 비활성화한다. 진행 중인 게임 쓰기를 마치고 게임 계획을 만든다. 광고 서버의 관련 쓰기와 아웃박스 처리를 마치고 발급한 광고 토큰의 만료 및 유예를 확인한 뒤, 광고 운영 설정으로 같은 대상의 계획을 만든다. 계획 파일은 보호 디렉터리의 새 경로를 쓴다.

```sh
python server/manage.py privacy_game_cleanup freeze --player "$PLAYER" --apply
python server/manage.py privacy_game_cleanup plan --player "$PLAYER" --plan "$PRIVATE_PLAN"
python ads/manage.py privacy_ads_cleanup plan --game-plan "$PRIVATE_PLAN" --scope "$PRIVATE_AD_PLAN"
python -m tools.privacy_scope --game "$PRIVATE_PLAN" --ads "$PRIVATE_AD_PLAN" --out "$PRIVATE_DELTA_SCOPE"
python -m tools.privacy_delete --register --directory "$PROTECTED_REGISTRY" \
  --scope "$PRIVATE_DELTA_SCOPE" --model-version "$CURRENT_MODEL_VERSION"
```

Spark 드라이버 외부 설정의 `privacy_tombstone_dir`를 보호 디렉터리로 지정한다. 파일은 등록·읽기 권한을 가진 운영 계정으로 게시한다. Bronze는 배치마다 새 목록을 읽고 `privacy_filtered` 건수를 남긴다. 삭제 후 데이터를 한 번 재처리해 가명과 연관 키가 0건인지 확인한다. 재학습 입력에도 같은 제외 목록을 전달한다.

## 저장소 삭제와 계정 마무리

재유입 차단 목록을 게시한 뒤 게임과 광고를 삭제한다. 광고 책임자와 캠페인 잔액을 대사한다. 광고 원장과 일 예산 잔액이 정리 전 일치하지 않으면 광고 정리는 중단된다.

```sh
python server/manage.py privacy_game_cleanup purge-games --player "$PLAYER" --plan "$PRIVATE_PLAN" --apply --game-writes-drained --tombstone-registered
python ads/manage.py privacy_ads_cleanup purge --scope "$PRIVATE_AD_PLAN" --ad-writes-drained --tombstone-registered
```

Delta 보호 설정에 실제 `spark` 설정, Bronze와 동일한 `lock_path`, `privacy_delta_targets`를 넣는다. 대상은 `kind`와 `path`를 가진 항목으로 `bronze`, `game_silver`, `ad_silver`를 사용한다. 운영 Spark 환경에서 먼저 읽기 전용으로 대상 건수를 확인한다. Bronze 및 Silver 쓰기를 중지한 뒤 `--apply --writers-drained --tombstone-registered`를 붙여 0건을 확인한다.

```sh
spark-submit pipeline/spark/privacy_purge.py --config "$PRIVATE_SPARK_CONFIG" --scope "$PRIVATE_DELTA_SCOPE"
```

영향받은 Gold 집계와 특징·라벨·훈련 데이터는 삭제된 원본에서 다시 만든다. 이전 버전을 참조하는 작업의 종료와 보존 정책을 확인한 후 동일한 Delta 명령에 `--vacuum-hours HOURS`를 추가해 실제 이전 파일 제거를 확인한다. 168시간 미만은 승인 후 `--short-retention-approved`가 필요하다.

로컬 추출·`out/`·스냅숏과 Garage MLflow 사본은 영향받은 파일이나 객체를 승인 목록에 정확한 경로 및 SHA-256으로 적고 먼저 검증한다. 쓰기를 마친 뒤 `--apply --writers-drained`를 추가하고 재실행해 삭제가 0건인지 확인한다. 여러 사람이 포함된 추출물은 정제 원본에서 다시 생성한다. 로컬 목록은 `files` 안에 `path`와 `sha256`, Garage 목록은 `objects` 안에 `key`와 `sha256`을 넣는다.

```sh
python -m tools.privacy_artifacts --root "$APPROVED_ROOT" --manifest "$LOCAL_MANIFEST"
python -m ops.mlflow.privacy_artifacts --manifest "$GARAGE_MANIFEST"
```

외부 위치의 검증을 마치고 광고 가명 대응표를 지운 뒤 계정 행을 정리한다.

```sh
python server/manage.py privacy_game_cleanup purge-ad-mapping --player "$PLAYER" --plan "$PRIVATE_PLAN" --apply --game-writes-drained --tombstone-registered --external-deletions-verified
python server/manage.py privacy_game_cleanup finalize --player "$PLAYER" --plan "$PRIVATE_PLAN" --apply --game-writes-drained --tombstone-registered --external-deletions-verified
```

11/9 Kafka 10개 토픽 실측에서 `cleanup.policy=delete`, `retention.ms=86400000`, `segment.ms=604800000`, broker 정리 주기 `300000`이었다. 설정 합계로 추정한 삭제 대기 시간은 **8일 5분**이다. 실제 세그먼트가 닫히는 시점과 브로커 상태를 확인하고, 이 추정값을 물리적 소멸 검증 결과로 쓰지 않는다.

## 보존 기한

11/10 테스트 종료 후 승인된 계정·게임 스냅숏을 웹 VM의 `/var/lib/paldo-privacy/accounts.json`과 `/var/lib/paldo-privacy/games.json`에 배치한다. `paldo-privacy` 소유, 권한 `0600`으로 둔다. 11/10 13:00–18:00에 가입했거나 해당 구간에 판을 시작한 비운영자 계정이 대상이다.

```sh
systemctl list-timers --all paldo-privacy-retention.timer
systemctl status paldo-privacy-retention.service
```

웹 VM의 타이머는 2026-12-12 00:00 KST에 두 스냅숏이 있으면 `retention-subjects.json`을 만든다. 파일이 늦게 배치되면 보존 기한이 지난 뒤 `systemctl start paldo-privacy-retention.service`로 목록 생성을 실행한다. 운영자는 목록을 확인하고 가명마다 위 삭제 절차를 적용한다. 마스킹된 원문·설문·라벨·특징·계정·스냅숏 사본을 확인한다.
