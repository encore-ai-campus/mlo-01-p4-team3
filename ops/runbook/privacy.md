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

## 재유입 차단

계정 비활성화와 대상 키 확보 후, 저장 위치의 행을 삭제하기 전에 tombstone을 기록한다. Spark 드라이버에서 읽는 외부 설정의 `privacy_tombstone_dir`를 보호 디렉터리로 지정한다. 파일은 등록·읽기 권한을 가진 운영 서비스 계정으로 게시한다.

```sh
python -m tools.privacy_delete --register --directory "$PROTECTED_REGISTRY" \
  --player "$PLAYER" --ad-session "$AD_SESSION" --ad-event-id "$AD_EVENT" \
  --ad-charge-id "$CHARGE" --model-version "$CURRENT_MODEL_VERSION"
```

광고 키 옵션은 복수일 때 반복한다. 위 명령은 제외 목록을 기록한다. 저장소 삭제는 각 어댑터의 실행과 검증이 필요하다. Bronze는 배치마다 새 목록을 읽고 `privacy_filtered` 건수를 남긴다. 데이터를 재처리한 뒤 삭제 가명·연관 키가 0건인지 확인한다. 재학습 입력에도 같은 제외 목록을 전달한다.

## 계정·게임 정리

승인된 가명 한 건을 웹 운영 설정으로 아래 순서에 따라 처리한다. 계획 파일은 보호 디렉터리의 새 경로를 사용한다. 먼저 계정과 세션을 비활성화한 뒤 진행 중이던 게임 쓰기가 모두 끝났는지 확인한다. 광고 데이터의 후속 삭제를 위해 대응표는 유지한다.

```sh
python server/manage.py privacy_game_cleanup freeze --player "$PLAYER" --apply
python server/manage.py privacy_game_cleanup plan --player "$PLAYER" --plan "$PRIVATE_PLAN"
# 재유입 제외 목록 등록과 게임 쓰기 종료 확인 후
python server/manage.py privacy_game_cleanup purge-games --player "$PLAYER" --plan "$PRIVATE_PLAN" --apply --game-writes-drained --tombstone-registered
```

광고·Delta·아티팩트 등 외부 삭제와 광고 가명 대응표 정리가 모두 확인된 뒤에만 계정 행을 정리한다.

```sh
python server/manage.py privacy_game_cleanup finalize --player "$PLAYER" --plan "$PRIVATE_PLAN" --apply --game-writes-drained --tombstone-registered --external-deletions-verified
```

11/9 Kafka 10개 토픽 실측에서 `cleanup.policy=delete`, `retention.ms=86400000`, `segment.ms=604800000`, broker 정리 주기 `300000`이었다. 설정 합계로 추정한 삭제 대기 시간은 **8일 5분**이다. 실제 세그먼트가 닫히는 시점과 브로커 상태를 확인하고, 이 추정값을 물리적 소멸 검증 결과로 쓰지 않는다.

## 보존 기한

2026-12-12 KST에 보호 accounts·games 스냅숏에서 참가자 목록을 확정한다. 11/10 13:00–18:00에 가입했거나 해당 구간에 판을 시작한 비운영자 계정을 선택한다. 시험용 보호 디렉터리 안의 새 파일 경로를 출력으로 사용한다.

```sh
python -m tools.privacy_retention --accounts "$PRIVATE_ACCOUNTS" --games "$PRIVATE_GAMES" --out "$PRIVATE_SUBJECTS"
```

목록을 확인하고 동일한 삭제 절차를 가명마다 적용하는 작업을 운영 일정에 예약한다. 마스킹된 원문·설문·라벨·특징·계정·스냅숏 사본을 모두 포함한다. 운영 삭제와 예약 활성화는 각 저장소의 삭제 검증이 끝난 뒤 수행한다.
