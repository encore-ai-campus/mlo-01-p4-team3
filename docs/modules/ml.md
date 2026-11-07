# ML 모델

## 무엇을
`ml/`, `ml/suspicion/`, `ml/adapters/`는 후속 구현 위치만 준비되어 있다. 현재 이 패키지에 학습·추론 모델이 구현됐다는 뜻은 아니다. 의도 기준선은 `intent/`, 광고 메모리 서버는 `ads/`이며 각각의 모듈 문서를 따른다.

## 왜
기획서 v1.3 8장(v1.1 9장), `docs/contracts/ml_lifecycle.md`에 따라 모델과 게임 엔진을 포트로 분리한다. 실제 엔진 관측·종료 후 라벨·판 단위 분할·누수 검사와 평가 관문을 구현한 뒤 연결한다.

## 사용법
현재 `ml/` 자체의 실행 명령은 없다.
- 의도 기준선·후보 비교: [intent.md](intent.md).
- 광고 API·예산·경매 검증: [ads.md](ads.md).
- 씐 자 특징·학습·평가의 착수 기준: D-07-02.
- 모델 v0 어댑터: D-08-01. 관문·밴딧: D-09-02. 광고 CTR LightGBM: D-10-02.

- D-07-03 의도 미세조정은 `python -m tools.perf.intent_finetune --epochs 5 --include-generated`로 실행한다. 증빙은 `docs/evidence/D-07-03.md`, 모델·실행 계보는 `mlruns/intent_bert/`에 보관한다.

## 설정값
현재 이 패키지에 구현된 모델 설정값은 없다. 공유 데이터 계약은 `contracts/datasets.py`, 버전·승격 계약은 `docs/contracts/ml_lifecycle.md`, 패키지 목록은 `requirements/ml.txt`다.

## 한계
D-05-01의 의도 파서 및 D-05-03의 광고 API가 구현되어도 씐 자 모델·광고 CTR 모델 학습·승격이 완료되는 것은 아니다. 현재 그 성능이나 운영 준비를 주장하지 않는다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 하루 일괄 문서화로 ML 패키지의 미구현 범위와 후속 작업 위치를 명시 |
| D-07-03 | 11/7 | intent/의 CPU 미세조정·동일 기준선 평가와 모델·계보 저장 경로를 연결 |
