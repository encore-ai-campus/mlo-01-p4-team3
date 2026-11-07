# 의도 해석기

## 무엇을
`IntentBaseline`은 문자 TF-IDF와 로지스틱 회귀 기준선이다. `BaselineIntentParser`가 분류 → 인물 → 공개 근거 → 문맥 → 슬롯 검증을 수행하고 `engine.ports.IntentParser`의 ParseResult를 반환한다. 결과는 확인용 후보이며 명령 실행·행동 소비는 하지 않는다.
`matcher.py`는 별칭·조사·최장 일치·시간 표현을 해소한다. 수신자는 addressee, 의심 이유 질문의 언급 인물은 기존 Command.target으로 구분한다.
`bert_bench.py`는 D-05-02 CPU 후보 비교용이며 BERT 운영 해석기는 아직 아니다.

## 왜
기획서 v1.3 8.1절 의도 해석(v1.1 9.1), `docs/contracts/intent.md`·`ports.md`를 따른다. 엔진에 비공개 정보가 들어가지 않도록 공개 문맥만 사용하고, 낮은 신뢰도·미지원·누락 슬롯을 확인 결과로 반환한다.

## 사용법
저장소 루트 Python 3.14, `requirements/ml.txt`의 scikit-learn·NumPy 등이 필요하다.

```python
from intent.parser import BaselineIntentParser
parser = BaselineIntentParser()
# ctx는 engine.ports.ParseContext. 공개 별칭·근거·선택 인물 등만 넣는다.
# result = parser.parse("훈장은 수상하지만 머슴은 결백해", ctx)
```

```powershell
python -m unittest discover -s tests/intent -t . -v
python -m tools.perf.intent_bench --n 1000 --seed 7
python -m tools.perf.bert_bench --help
```

기준선 벤치마크는 계보·대조쌍 단위 분할 후 평가하며 로컬 mlruns에 데이터·특징·코드 버전과 모델을 저장한다. 후보 비교 결과·라이선스·외삽 방법은 `docs/evidence/D-05-02.md`에 있다. 선택은 라이선스가 명시된 KoELECTRA-base-v3이며 이 문서를 쓰면서 재측정하거나 운영 모델로 전환하지 않았다.

## 설정값
| 이름 | 기본값·의미 |
|---|---|
| 시드 데이터 | fixtures/intent/seed_examples.jsonl |
| IntentBaseline.threshold | 0.20. OOV·낮은 신뢰도는 미지원 |
| 특징 | char-2-5-nfc-v1 |
| 학습 | LogisticRegression C=4, max_iter=1000, random_state=7 |
| 매칭 | public-longest-v0.1 |
| versions | model·tokenizer·alias_dict·matcher·threshold |
| 로컬 실행 기록 | mlruns/ (Git 제외), 벤치마크 결과 docs/evidence/perf/ |
| HF_HOME 등 후보 측정 설정 | bert_bench --help와 D-05-02 증빙. 키·가중치를 저장소에 넣지 않음 |

## 한계
- 시드 기준선이며 일반 입력 합격 성능을 증명하지 않는다. D-05-01의 F1·명령 전체 일치율·미지원 검출률은 작은 평가 분할 기준이다.
- 기본 생성자는 시드로 학습한다. 준비·학습 시간은 해석 1회 지연과 다르다.
- 의도 계약 5절의 LLM 보조는 확장 E-08-12·E-10-11 범위이며 아직 구현하지 않았다.
- BERT 후보 비교는 학습 성능 검증이 아니다. 미세조정·실제 해석기 연결은 후속 태스크다.
- 실물 BERT 가중치·모델 파일·mlruns는 로컬 산출물이며 별도 배포가 필요하다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-01 | 11/5 | TF-IDF 기준선·인물/근거 매칭·확인 전용 파서·계보 분할 평가·지연·VM 점검 |
| D-05-02 | 11/5 | CPU BERT 후보 라이선스·추론·메모리·표본 학습/외삽 비교와 KoELECTRA 선택 |
