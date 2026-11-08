# 시뮬레이션 Job 운영

C가 빌드한 이미지는 `engine.sim.runner.run_game` → `RecordingEngine` → `KafkaEventSink`를 실행한다. D는 `--preset`, `--games`, `--seed`, `--bot logic|random`, `--policy min-legal|rule-v0`만 조절한다. 기준선 모델은 `fake-suspicion-1`이며 학습 모델 성능을 뜻하지 않는다.

`install.py --profile "$PRIVATE_PROFILE"`은 `artifacts.json`의 공식 바이너리·airgap SHA를 확인한다. 2노드 설치는 기존 Spark를 유지하고 전용 systemd unit을 사용한다. server 1600M, agent 1000M 상한이며 kubelet은 프로필의 사설 주소에만 bind한다. admin kubeconfig·join token은 root 전용, etcd secrets encryption은 켠다. 설치 아티팩트는 [K3s 공식 릴리스](https://github.com/k3s-io/k3s/releases/tag/v1.36.5%2Bk3s1)에서 받는다.

이미지는 `images/sim/Dockerfile`로 빌드하고 OCI digest를 확인해 두 노드에 같은 파일을 import한다. `jobs/sim-job.yaml`의 Kafka CIDR 변수 3개는 저장소 밖 인벤토리 값으로 치환한 뒤 적용한다. Kafka TLS 파일은 그 노드에서 만든 전용 생산자 키를 Secret으로 주입하며 repo에 넣지 않는다. Secret volume 0440·fsGroup 10001, 읽기 전용 root filesystem, 비특권, service-account token 없음, Kafka 고정 egress만 허용한다.

```sh
python -m pipeline.simulation --kafka-config "$PRIVATE_KAFKA_CONFIG" --run-id example --preset base6 --games 50 --seed 801 --policy rule-v0
# 위 명령이 이미지의 entrypoint다. 실제 운용은 Job args로 동일 인자를 넘긴다.
python -m pipeline.spark.verify_simulation --config "$PRIVATE_SPARK_CONFIG" --manifest "$PRIVATE_ACK_MANIFEST" --out "$PRIVATE_RESULT"
```

검증은 Job의 종료 코드만 보지 않고 ACK된 판별 이벤트 수·연속 seq·전체 본문 해시와 공식 bronze를 대조한다. 노드 로그의 manifest는 ID·해시만 포함한다. kubelet tunnel은 사설 bind와 맞지 않아 `kubectl logs`가 EOF를 반환하는 제한을 발견했다. 보안 설정 완화는 적용하지 않았으며, 완료 Pod의 노드 로컬 CRI 로그를 읽어 manifest를 회수했다. 노드 로그 조회 복구는 별도 승인이 필요하다. Job 재실행 시 같은 seed/ID를 유지해야 bronze 멱등성을 검증할 수 있다.

노트북 5대의 자원 표와 C-05-01 비교는 C-08-03 증빙을 본다. MySQL 세 번째 VM은 호스트 여유 RAM 부족으로 꺼져 있으며 20대 전체 가동을 주장하지 않는다.

시뮬레이션의 11인 프리셋은 `--bundle fixtures/content_min/bundle.json`(content-min-0.1)을 명시한다. 운영 content-1.0은 6인이어서 duo11은 오류로 멈춘다. 운영 콘텐츠를 늘리거나 사람 판 공개 프리셋을 바꾸지 않는다. 최종 로컬 OCI import 후에는 같은 manifest의 digest 이름도 `ctr images tag`로 등록해야 NeverPull의 digest 참조가 동작한다.
