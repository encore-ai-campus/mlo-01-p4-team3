# PJ4 MLflow 운영

NiFi VM의 MLflow 3.18.0은 loopback에서 추적과 레지스트리를 제공한다. 클라이언트는 인가된 SSH 포워딩으로 접속한다. 메타데이터는 systemd 상태 디렉터리의 SQLite에, 아티팩트는 TLS 검증을 켠 Garage 전용 버킷에 저장한다. 서비스는 전용 계정·버킷 쓰기/읽기 키를 쓰고, 키와 S3 주소는 보호 EnvironmentFile에 둔다. 설치 환경에는 `mlflow==3.18.0`과 `boto3`가 필요하다.

```sh
python -m tools.ml_gate --run-id "$RUN_ID" --model suspicion --artifact model --evaluator module:function
```

`MLFLOW_TRACKING_URI`를 추적 서버 접속값으로 설정한다. 평가 함수는 완료된 MLflow run 객체를 받아 `(bool, reason_code)`를 반환한다. 관문은 `data_version=delta:…`, `feature_version`, 전체 SHA의 `code_commit`을 먼저 검사한다. 의도 해석기 실행기(`intent.retrain`, `ops.mlflow.retrain`의 `intent`)는 사용자 테스트 동결 출처일 때 `check_run(..., allow_freeze=True)`로 `freeze:<manifest sha256>`도 받으며, 실제 `--freeze` 폴더 해시와 먼저 대조한다. 이 명령(CLI)은 `delta:`만 받는다(11/11 사용자 결정). 거절은 실행 태그에 기록한다. 통과 시 아티팩트를 등록하고 `candidate` 별칭을 부여한다. 같은 실행과 아티팩트를 다시 등록하면 기존 버전을 사용한다. 관문 실행은 모델별로 직렬화한다. `server.infra.model_selector.lookup_model`은 별칭에서 레지스트리 버전과 source를 반환한다. D 평가 모듈과 웹 런타임 연결은 인계 후 검증한다.
