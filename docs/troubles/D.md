# 트러블 기록 — D ML·광고

### D-05-02 BERT 후보 확인
- 문제: 최초 Hugging Face Xet 방식 가중치 전송이 진행되지 않았고, KLUE-RoBERTa-small 개별 모델 카드에는 라이선스가 없었다.
- 원인: 다운로드 중 가중치 임시 파일과 로드 시작 로그만 남고 추론으로 진행하지 않는 것을 확인했다. KLUE API의 라이선스도 null이며, 공식 프로젝트의 CC-BY-SA-4.0과 개별 가중치 조건을 같다고 확정할 수 없었다.
- 대응: 이번 측정용 자식 프로세스만 중지하고 일반 HTTPS로 재시도했다. KoELECTRA는 공식 PyTorch 가중치를 weights_only로, KLUE는 safetensors로 로드했다. 양쪽을 동일 CPU 조건으로 실측하고, 라이선스를 명시한 KoELECTRA-base-v3를 선택했다.
- 제출 검사: `HF_HUB_DISABLE_IMPLICIT_TOKEN` 설정이 키 이름 때문에 비밀정보로 탐지됐다. 설치된 `huggingface_hub`의 `constants.py`·`utils/_headers.py`에서 인증 토큰 자동 전송을 끄는 불리언 설정임을 확인하고, 저장소 규칙대로 `tools/secrets_allowlist.txt`에 해당 파일·키만 사유와 함께 등록했다. 비밀정보 검사 전체와 저장소 검사 테스트를 다시 실행한다.
- 결과: 두 후보 각각 추론 200회와 40문장 표본 전체 미세조정 10 step 완료. p95는 KoELECTRA 154.527ms, KLUE-small 48.012ms, 1,000문장 1 epoch 예상은 각각 341.669s/177.298s였다. 표본·외삽 방법·RSS 정의·라이선스 근거는 [D-05-02 증빙](../evidence/D-05-02.md)에 기록했다.
