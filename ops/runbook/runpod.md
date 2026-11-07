# RunPod LLM 운영 후보 인계 (C-06-04, 미확정)

11/7 사용자 의견은 “런팟 써야 될 것 같은데”이다. GPU 기종·시간당 예산·사용 시간·계정·최종 모델 승인이 없으므로 아직 배포 결정이나 Pod 생성 완료가 아니다. 기존 E-06-07의 Kanana-2-3B GGUF·Windows 직접·8스레드·RAM 6GB는 CPU 측정의 잠정 값이며 GPU 자원 값으로 재사용하지 않는다. E-07-06의 Colab T4는 MVP 노트북 실험이며 게임 운영 엔드포인트와 구분한다.

## 후보 실행 구조

`app-01의 LineRenderer → HTTPS → RunPod의 E LLM 서비스 → llama.cpp CUDA 추론`을 후보로 둔다. 서비스 외부 계약은 integration.md G7의 `POST /v1/renders`, `GET /v1/renders/{job_id}`, `POST /v1/renders/{job_id}/cancel`을 유지한다. 일반 OpenAI/vLLM completion 주소를 PALDO_LLM_URL에 바로 넣으면 이 계약이 맞지 않는다. B의 어댑터·E 서비스가 같은 계약으로 실제 연결되어야 한다.

RunPod Pod의 HTTP proxy로 서비스 포트를 노출할 수 있다. 외부 URL과 인증은 보호 설정에서만 주입한다. HTTPS 접속과 인증 실패 거부를 모두 확인한다. RunPod 관리 API 키는 운영 제어용이고 공개 LLM 서비스의 인증으로 자동 적용되지 않는다. 서비스/보호 프록시에 별도 인증을 구현하고 B 어댑터와 맞춘다. URL·키·Pod ID·계정은 저장소에 적지 않는다.

## 확정할 값과 검증 순서

| 항목 | 현재 근거 | 필요한 확정/측정 |
|---|---|---|
| 모델 | E CPU 잠정 Kanana-2-3B GGUF Q4_K_M | E가 모델·revision·파일 hash·GPU 빌드 지원 확인 |
| GPU | 후보 미선정 | VRAM·CUDA 호환성, 단일 Pod로 동시 요청 벤치 |
| CPU·RAM·스레드 | GPU 측정 없음 | 컨테이너 실배정과 메모리 최고점·스레드 측정 |
| 디스크 | 모델·런타임 용량 미산정 | 영속 경로·다운로드 캐시·이미지 예산과 백업 |
| 비용 | 예산 미정 | 현재 콘솔 단가와 사용 시간·저장소 비용 승인 |
| 앱 연결 | 계약상 PALDO_LLM_URL | 실제 app-01에서 렌더·poll·cancel·deadline 시험 |

후보 자원과 비용 범위를 확정한 뒤 E 서비스 이미지를 고정 digest로 준비한다. 모델·라이선스·hash를 확인하고 GPU 지원 런타임을 기동한다. 준비 상태 확인 후 E와 같은 평가 세트로 단일·동시 2·4 요청의 전체 지연 p95·큐 대기·실패·VRAM/RAM 최고점을 측정한다. deadline 기본 15초와 실제 생성 취소를 확인한다. Pod 중단/통신 실패 때 B의 폴백·timeout이 동작하고 게임 상태 저장이 계속되는지 확인한다. 통과한 값으로 vm_roles.md·schedule.md와 증빙을 갱신한 뒤 C-06-04를 닫는다.

## 시간표·정지·복구

사용자 테스트 전에 모델 적재·예열·HTTPS와 실제 app-01 연결을 확인한다. 테스트 중 모델 학습·대량 시뮬레이션은 별도 자원에서 실행한다. 테스트 종료 후 필요한 로그·모델 경로를 영속 저장소로 보존하고 Pod 정지 상태와 남은 저장소 과금을 콘솔에서 확인한다. terminate는 container disk 삭제를 수반하므로 백업 여부를 확인한 뒤 별도 범위로 실행한다. Pod 중단 중에는 운영 endpoint가 응답하지 않으므로 사용자 테스트 시간과 맞춰 기동한다.

노트북의 신규 llm-01을 생성·기동했다는 확인 자료가 없어 정지했다고 기록하지 않는다. 외부 GPU 선택에 따른 실제 RAM 반환량도 미측정이다. 기존 Garage는 두 스토리지 노드를 함께 사용해야 하므로 LLM 자원 확보를 이유로 media-storage-01을 끄지 않는다.

공식 자료 확인일 2026-11-07: [HTTP 포트 노출](https://docs.runpod.io/pods/configuration/expose-ports), [Pod 생성·정지·삭제](https://docs.runpod.io/pods/manage-pods). 구체적인 GPU 가격·가용성은 선택 시점 콘솔에서 다시 확인한다.
