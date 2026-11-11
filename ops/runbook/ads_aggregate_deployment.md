# 광고 하루 집계 운영 연결

PR #411의 하루 집계 코드를 실제 서버에 연결하는 절차다. 11/11 사전 점검에서 ad-01의 timer는 disabled/inactive였고 광고 서버 설정 두 개만 채워져 있었다. Spark 연결 설정 일곱 개는 비어 있었다. 기존 C-08 검증 설정의 `bronze_path`는 과거 validation-bronze이므로 그 설정 그대로 운영에 쓰지 않는다.

## 준비한 변경안

| 대상 | 현재 운영 상태 | 적용할 변경 |
|---|---|---|
| 집계 환경파일 | Spark 관련 설정 7개 비어 있음 | 별도 보호 경로의 제안 파일로 교체 |
| 광고 집계 소스 | 현재 앱 소스 참조 | `b1eb7f8` 별도 집계 소스 사용, 기존 광고 HTTP 프로세스 재시작 없음 |
| 파일 전달 | 전용 설정 없음 | ad-01 전용 키, Spark 전용 계정, 엄격한 호스트 키 검증 |
| Spark 입력 | 과거 검증용 Bronze | 공식 Bronze, validation_mode=false |
| Spark 출력 | C-08 검증 경로 | 공식 광고 전용 silver·gold·일별 보고서·학습 경로, 별도 writer lock |
| 예약 실행 | 설치만 됨, 비활성 | 수동 실행 성공을 확인한 뒤 매일 00:40 Asia/Seoul 활성화 |

전용 개인키는 광고 서버에만 보관했고 서버 간에는 공개키·호스트 키만 전달했다. 새 계정의 SSH 키에는 출발지 제한과 forwarding/PTY 제한을 적용했다. `true` 명령으로 인증 성공을 확인했다. 운영 환경파일·실행 스크립트 교체와 timer 활성화는 아직 적용하지 않았다.

제안 설정·원래 파일의 보호 백업 위치는 저장소 밖 운영 인계 자료에서 확인한다. 실제 주소·계정·키·설정 원문은 공개 문서에 넣지 않는다. 새 집계 코드는 별도 경로에 있으며 기존 광고 HTTP·Bronze 스트림 소스를 바꾸지 않았다.

## 첫 적용과 실행

운영 적용 승인이 난 뒤 아래 순서로 한다. 변수는 보호 인계 자료의 절대 경로로 지정한다.

```sh
# ad-01: 환경파일과 스크립트 원본을 보호 백업한 다음 제안 파일을 설치한다.
sudo install -m 0640 -o root -g "$ADS_GROUP" "$PROPOSED_ENV" "$AGG_ENV"
sudo install -m 0755 "$VERIFIED_SOURCE/ops/ads_aggregate.sh" "$AGG_SCRIPT"
sudo systemd-analyze verify /etc/systemd/system/paldo-ads-aggregate.service /etc/systemd/system/paldo-ads-aggregate.timer
sudo systemctl start --no-block paldo-ads-aggregate.service
sudo journalctl -u paldo-ads-aggregate.service --since '5 minutes ago' --no-pager
```

`prepare → silver → export_ledger → send → gold → fetch → publish → done`의 성공 로그와 실제 원장 대조를 확인한다. silver는 광고 전체 행 상한100,000을 유지한다. 넘으면 일부만 집계하지 않고 중단하며, 상한을 임의로 올리지 않는다. 재판정 발행 뒤120초 대기하고 gold 실패 시 원장을 재수출해 한 번만 재시도한다. 어느 단계든 실패하면 CTR을 갱신하지 않고 timer도 켜지 않는다.

실행은 공식 Delta 파생물을 쓰고 최종 성공 시 운영 광고 CTR을 갱신한다. 광고 선택 결과에 영향이 있으므로 연결 확인만 통과한 것을 운영 실행 완료로 발표하지 않는다. 마지막 성공 시각·관찰 종료 시각·CTR 칸 수 파일과 서비스 `Result=success`를 확인한다. 지표 파일의 생성과 Prometheus 수집은 별도로 확인한다.

```sh
# 수동 실행 성공 후에만 예약을 활성화한다.
sudo systemctl enable --now paldo-ads-aggregate.timer
sudo systemctl show paldo-ads-aggregate.timer -p ActiveState -p NextElapseUSecRealtime
```

## 실패와 되돌림

실패 로그는 보호 경로에 보존한다. timer를 비활성화하고 실행 중 작업을 종료한 다음 백업 환경파일·스크립트를 복원한다. 기존 광고 HTTP 프로세스와 Bronze는 계속 사용한다. 이미 성공한 CTR 갱신이나 Delta commit은 파일 복원만으로 되돌아가지 않으므로 해당 결과 버전·입력 snapshot·이전 CTR을 기록한 뒤 D·E와 복구 대상을 정한다. 승인 전에는 수동 집계도 실행하지 않는다.
