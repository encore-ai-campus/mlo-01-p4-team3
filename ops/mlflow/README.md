# PJ4 MLflow 운영

NiFi VM의 MLflow 3.18.0은 loopback에서 추적과 레지스트리를 제공한다. 클라이언트는 인가된 SSH 포워딩으로 접속한다. 메타데이터는 systemd 상태 디렉터리의 SQLite에, 아티팩트는 TLS 검증을 켠 Garage 전용 버킷에 저장한다. 서비스는 전용 계정·버킷 쓰기/읽기 키를 쓰고, 키와 S3 주소는 보호 EnvironmentFile에 둔다. 설치 환경에는 `mlflow==3.18.0`과 `boto3`가 필요하다.

```sh
python -m tools.ml_gate --run-id "$RUN_ID" --model suspicion --artifact model --evaluator module:function
```

`MLFLOW_TRACKING_URI`를 추적 서버 접속값으로 설정한다. 평가 함수는 완료된 MLflow run 객체를 받아 `(bool, reason_code)`를 반환한다. 관문은 `data_version=delta:…`, `feature_version`, 전체 SHA의 `code_commit`을 먼저 검사한다. 의도 해석기 실행기(`intent.retrain`, `ops.mlflow.retrain`의 `intent`)는 사용자 테스트 동결 출처일 때 `check_run(..., allow_freeze=True)`로 `freeze:<manifest sha256>`도 받으며, 실제 `--freeze` 폴더 해시와 먼저 대조한다. 이 명령(CLI)은 `delta:`만 받는다(11/11 사용자 결정). 거절은 실행 태그에 기록한다. 통과 시 아티팩트를 등록하고 `candidate` 별칭을 부여한다. 같은 실행과 아티팩트를 다시 등록하면 기존 버전을 사용한다. 관문 실행은 모델별로 직렬화한다. `server.infra.model_selector.lookup_model`은 별칭에서 레지스트리 버전과 source를 반환한다. D 평가 모듈과 웹 런타임 연결은 인계 후 검증한다.


## 운영 Job과 가중치 연결

이미지의 고정 ML 의존성을 실제 k3s 노드의 비루트 CPU 컨테이너에서 검사한 결과는 C-10-01 증빙을 따른다. OCI 이미지는 digest로 노드에 import하며 레지스트리로 push했다고 가정하지 않는다. `cronjobs/retrain-volumes.yaml`을 보호 인벤토리 값으로 렌더해 입력4GiB·출력8GiB의 두 PVC를 설치한다. Retain·노드 affinity를 사용하고 입력은 Pod에 읽기 전용으로 제공한다.

`ops.mlflow.job`은 매 실행마다 영속 결과 볼륨에 고유 폴더를 만든다. 출력 여유가 부족하면 시작하지 않는다. 보호 설정의 선택 `mlflow_tunnel`은 identity·known_hosts·target·local_port·remote_port이며 엄격한 호스트 검증과 loopback 포워딩만 쓴다. 작업이 끝나거나 터널 연결이 실패하면 전용 SSH 프로세스와 임시 키를 회수한다. 개인키·설정 원문은 저장소에 두지 않는다. 입력·등록 설정 검사는 `python -m ops.mlflow.job --config "$PRIVATE_CONFIG" --out "$PRIVATE_RUN_ROOT" --check`다.

웹은 선택 `PALDO_MLFLOW_MODELS`로 intent·suspicion의 레지스트리 모델명을 지정한다. 가중치를 영속 원자 캐시에 준비한 뒤 `paldo.artifact_version`을 판에 고정한다. TF-IDF와 BERT는 해당 판의 버전을 읽고, 의심 모델은 기존 프로세스 보관소를 사용한다. 판 종료 시에는 마지막 확인한 버전 목록을 써 레지스트리 장애가 종료 요청을 막지 않게 한다. 설정을 비우면 기존 정적 조립을 유지한다. 시작 시 운영 가중치 두 종류를 실제로 읽어야 하므로 보호 cache·SSH 추적 연결·MLflow 클라이언트 의존성을 먼저 준비한다.

CronJob은 실제 수동 Job 세 모델 실행과 관문·등록·운영 카나리아 검증이 끝날 때까지 suspend=true다. 현재 구현·이미지 import·PVC 설치와 실제 개인 입력 전송/학습/운영 웹 배포는 구분한다. `engine-jsonl:` 보고서의 출처를 임의 `delta:`로 바꾸지 않으며 기존 출처 관문을 유지한다. D가 α 유효성 등 추가 조건으로 accepted=false를 기록하면 기본 지표 관문만 통과해도 C가 등록하지 않는다.
