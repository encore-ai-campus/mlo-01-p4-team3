# 재학습·카나리아·롤백

학습은 승인된 입력과 삭제 목록을 준비한 뒤 수동 Job으로 먼저 실행한다. C는 경보를 확인하고 D가 관측과 모델을 대조해 전환한다. 정식 사용자 테스트 시간에는 E 승인과 카나리아 비율 0을 적용한다.

## 실행

`ops/k3s/cronjobs/retrain.yaml`은 02:00 KST 야간 일정과 `suspend: true`를 갖는다. `ops/k3s/images/retrain/Dockerfile`로 ML 의존성 이미지를 빌드하고 검증한 digest를 `RETRAIN_IMAGE`에 넣는다. `paldo-ml` namespace에 승인 입력 PVC, 보호된 `paldo-retrain-config` Secret, MLflow 인증용 `paldo-mlflow-client` Secret을 준비한다. PVC의 `/inputs/repository`에는 `.git`을 포함한 검증된 checkout을 둔다. runner는 실제 HEAD와 설정 SHA를 대조한다. 설정은 세 모델 `intent`·`suspicion`·`ad_ctr`를 모두 포함한다.

설정의 `code_commit`은 이미지 소스 전체 SHA, `tombstones`는 읽기 전용 입력 PVC의 최신 삭제 목록이다. 각 모델 설정에 `model`, `data_version`(`delta:<table>@<version>`. 사용자 테스트 동결로 학습하는 `intent`만 `freeze:<manifest.json sha256>`이고 학습 명령의 `--freeze` 폴더와 해시가 같아야 한다), `feature_version`, `train`, `report`, `artifact`를 둔다. 광고 CTR은 보고서 버전 `artifact_version`도 지정한다. 주소·계정·입력 경로는 보호 설정에만 기록한다.

`train`은 기존 D 진입점의 인자 배열이다. 의도 해석기는 `["-m","intent.retrain",...,"--report-only","--out","{output}"]`, 씐 자는 `["-m","ml.suspicion.retrain",...,"--out","{output}"]`, CTR은 `["-m","ml.ad_ctr",...,"--out","{output}/decision.json"]`을 쓴다. 검수된 동결·라벨·평가 맥락·보정 자료와 정확한 Delta 버전을 넘긴다. 의도와 씐 자의 report는 `decision.json`, artifact는 각각 `checkpoint`와 `trained`다. CTR은 report와 artifact 모두 `decision.json`이다.

명령은 모델별로 학습을 실행하고 D의 지표 관문을 다시 적용한다. 출처 버전과 삭제 목록이 유지되는지 확인한 뒤 MLflow에 기록한다. 거부된 실행은 거부 사유를 남기며 통과 실행만 `candidate`로 등록한다. 운영·카나리아 별칭 전환은 운영자가 수행한다.

```sh
python -m ops.mlflow.retrain --config "$PRIVATE_RETRAIN_CONFIG" --check
kubectl -n paldo-ml create job --from=cronjob/paldo-retrain "paldo-retrain-manual-$(date +%s)"
kubectl -n paldo-ml get jobs,pods
```

CronJob의 동시 실행 정책은 Forbid다. 수동 Job을 만들기 전 같은 모델의 실행 중 Job이 없는지 확인한다. 실패 Job은 출력과 run ID를 확인한다. 이미 생성된 run의 후보 등록 재시도는 C-09-01 `register_candidate`의 같은 run ID를 사용한다. 결과·모델·재실행 로그를 확인한 뒤 일정을 활성화한다.

## 카나리아

등록 버전의 `paldo.artifact_version`은 실제 모델 파일의 버전 이름이다. B 조립에는 `server.infra.mlflow_registry.MlflowModelRegistry`를 `attach_model_versions`의 registry로 주입한다. 기존 `VersionPinning`이 판 시작 때 고정하고 해시로 20%를 배정한다. 비율 설정 `PALDO_MODEL_CANARY_RATIO=0.2`를 사용한다. 각 모델의 실제 로더와 평가를 통과한 산출물이 준비된 뒤 운영 조립에 적용한다.

```sh
python -m ops.mlflow.lifecycle canary --model "$MODEL" --expected-production "$CURRENT" --reason approved_candidate
python -m ops.mlflow.canary --report "$PRIVATE_OBSERVATIONS" --model "$MODEL" --expected-production "$CURRENT" --expected-canary "$CANARY"
```

관측 보고서는 `canary_games`, `control_games`, `canary_win`, `control_win`, `fallback`, `p95_ms`, `intent_edit_cancel`을 포함한다. 양쪽 완료 30판과 악화 신호 없음을 확인한 뒤 같은 명령에 `--apply`를 붙인다. 시간 상한은 없다. 봇 시뮬레이션에서 수정·취소율은 `intent_edit_cancel="unmeasurable"`, `source="bot"`으로 기록한다. 사람 관측에서는 검토 신호 여부를 bool로 제공한다.

## 경보와 복구

승격 명령은 `--expected-canary`를 전환 함수에도 전달한다. 처음 관측 이후나 관문 확인 중 후보 별칭이 바뀌면 `canary_version_changed`로 중단한다. 첫 쓰기 직전에 운영 버전도 다시 확인한다. 실패하면 현재 별칭·관측 보고서가 같은 후보인지 확인하고 다시 평가한다. 자동 재시도하지 않는다. MLflow 별칭 API에 원자적 비교·교환이 있는 것으로 가정하지 않으며, 실제 쓰는 대상은 평가한 명시적 버전 번호로 고정한다.

폴백률 5% 초과 또는 p95 100ms 초과는 즉시 롤백 대상이다. 마을 승률 차이 10%p 초과 또는 수정·취소율 1.5배 초과는 D 검토 대상이다.

```sh
promtool test rules ops/prometheus/canary-test.yml
python -m ops.mlflow.lifecycle rollback --model "$MODEL" --expected-production "$CURRENT" --reason fallback_above_limit
```

현재 운영 별칭이 확인한 번호와 다르면 명령이 중단된다. 복구는 `previous`를 `production`에 설정하고 `canary`를 지운다. 이유와 시각은 모델 버전 태그에 남는다. 새 판은 복구된 실제 모델 버전으로 시작한다. 이미 진행 중인 판은 메타의 버전으로 계속 처리한다. 이전 파일과 worker는 그 판이 끝날 때까지 유지한다. 광고 CTR의 MySQL 활성 버전은 D-10-02의 광고 운영 명령과 불변 롤백 기록을 별도로 확인한다.
