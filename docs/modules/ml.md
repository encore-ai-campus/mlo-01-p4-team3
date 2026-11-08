# ML 모델

## 무엇을
`ml/suspicion/`의 선행 학습 코드는 아직 dev에 통합되지 않았다. `ml/adapters/`에는 D-08-01 실행 경계가 있다. 현재 이 패키지에 학습·추론 모델이 구현됐다는 뜻은 아니다. 의도 기준선은 `intent/`, 광고 메모리 서버는 `ads/`이며 각각의 모듈 문서를 따른다.

## 왜
기획서 v1.3 8장(v1.1 9장), `docs/contracts/ml_lifecycle.md`에 따라 모델과 게임 엔진을 포트로 분리한다. 실제 엔진 관측·종료 후 라벨·판 단위 분할·누수 검사와 평가 관문을 구현한 뒤 연결한다.

## 사용법
D-08-01의 `ml/adapters/suspicion_model.py`는 지속 worker로 추론 마감을 지키고, 실패를 엔진의 규칙 v0 폴백 예외로 바꾼다. `ml/adapters/run_model_games.py`는 기존 판 실행기를 재사용해 모델 버전·합법성·폴백 수·p95를 기록한다. `tools/`에서 `ml/`을 import하지 않는다.

```sh
python -m ml.adapters.run_model_games --preset base6 --games 50 --seed 1 --model out/suspicion/run1/models.pkl --out out/model_games
```

`--preset/--games/--seed/--out/--policy/--record`는 기존 도구와 같다. 기본 정책은 모델을 호출하는 `rule-v0`다. `--model`을 생략하면 `PALDO_SUSPICION_MODEL_PATH`, 그 값도 없으면 위 로컬 경로를 쓴다. D-07-02의 features/evaluate 코드와 실제 models.pkl 및 그 의존 패키지가 있어야 한다. 없으면 종료 코드 1이며 모델 실행 성공 manifest를 만들지 않는다. `--policy min-legal`은 모델을 사용하지 않아 성공으로 판정하지 않는다.
- 의도 기준선·후보 비교: [intent.md](intent.md).
- 광고 API·예산·경매 검증: [ads.md](ads.md).
- 씐 자 특징·학습·평가의 착수 기준: D-07-02.
- 모델 v0 어댑터: D-08-01. 관문·밴딧: D-09-02. 광고 CTR LightGBM: D-10-02.

- D-07-03 의도 미세조정은 `python -m tools.perf.intent_finetune --epochs 5 --include-generated`로 실행한다. 증빙은 `docs/evidence/D-07-03.md`, 모델·실행 계보는 `mlruns/intent_bert/`에 보관한다.

## 설정값
어댑터의 추론 응답 대기 마감은 90ms, native 초기화·워밍업을 포함한 로드 마감은 30초다. 고정 주민 수의 로컬 순차 IPC에서 검증했으며 웹 동시성·큰 메시지의 절대 wall-clock 상한은 별도 검증이 필요하다. 특징 버전·열 순서·보정 온도는 D-07-02 산출물과 기존 특징 모듈로 검증한다. 공유 데이터 계약은 `contracts/datasets.py`, 버전·승격 계약은 `docs/contracts/ml_lifecycle.md`, 패키지 목록은 `requirements/ml.txt`다.

## 한계
D-05-01의 의도 파서 및 D-05-03의 광고 API가 구현되어도 씐 자 모델·광고 CTR 모델 학습·승격이 완료되는 것은 아니다. 현재 그 성능이나 운영 준비를 주장하지 않는다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 하루 일괄 문서화로 ML 패키지의 미구현 범위와 후속 작업 위치를 명시 |
| D-07-03 | 11/7 | intent/의 CPU 미세조정·동일 기준선 평가와 모델·계보 저장 경로를 연결 |
| D-08-01 | 11/8 | 중단 가능한 모델 포트·판 실행 진입점, 실제 버전 고정과 폴백 검증 추가 |

### D-08-01 제약

- 현재 logistic/창귀 1명만 연결한다. 특징 추출을 복제하지 않으며 D-07-02 통합 전에는 모델을 실행할 수 없다.
- 학습 산출물에 근거별 기여값이 없어 빈 기여 매핑을 반환한다. 성향별 근거 가중 효과와 확장 2명 모델은 후속 인계가 필요하다.
- pickle은 신뢰된 로컬 학습 파일만 사용한다. HTTP/사용자 업로드 파일을 입력으로 받지 않는다.
- 같은 프로세스 호출 지연과 worker IPC 지연은 별도로 측정한다. worker는 운영용 서비스 배포를 뜻하지 않는다. Selector의 판별 버전 선택·인스턴스 보존·close 수명 관리는 D-08-04 경계다.
- 실제 웹·k3s·Kafka→bronze는 별도 검증이 필요하다. 자세한 대기 입력과 실측은 `docs/evidence/D-08-01.md`.
