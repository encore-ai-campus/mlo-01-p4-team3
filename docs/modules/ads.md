# 광고 서버

## 무엇을
- `ads/domain/`: 표준 라이브러리 경매·예산·서명·유효 판정 기준 구현.
- `ads/app/serve.py`: 캠페인·타기팅·빈도·예산 필터, 소재별 사전 CTR 톰슨 추출, 2등 가격, 메모리 예약. 동일 request_id·동일 입력은 응답을 재사용하고 다른 입력이면 409.
- `ads/app/beacon.py`·`click.py`: 토큰·최초 수신 시각으로 판정, CPC 클릭 또는 CPM 노출 확정. 클릭이 먼저 오면 노출도 인정한다.
- `ads/project/`·`ads/web/`: 게임 웹과 독립된 Django 서비스. MySQL에는 캠페인·소재와 admin 계정만 저장한다. 일 예산은 캠페인의 `daily_budget_mw`로 관리한다.

## 왜
기획서 v1.3 9장(v1.1 14장), `docs/contracts/ads.md` 3~6절을 따른다. 게임 서버는 AdProvider 포트로 호출하고 브라우저에는 게임 웹 출처만 노출한다. D-05-03은 `BudgetBook`으로 HTTP 흐름을 검증하는 단계다. 원장·아웃박스·Kafka는 D-06-02 범위다.

## 사용법
저장소 루트 PowerShell, 문서 지정 Python 3.14와 `requirements/web.txt` 패키지를 사용한다. 관리 명령과 벤치마크는 기존 `ops.compose.devenv.parse_env` 형식의 루트 .env를 읽으며 셸 환경 변수가 우선한다.

```powershell
python -m ops.compose.run make-env
docker compose -p paldo-dev -f ops/compose/dev.yml up -d mysql
python ads/manage.py migrate --noinput
python ads/manage.py load_ad_fixtures
python ads/manage.py createsuperuser
python ads/manage.py runserver 127.0.0.1:8001 --noreload
```

- `load_ad_fixtures`는 없는 가상 캠페인·소재만 최초 적재하며 기존 admin 수정은 유지한다.
- 등록·수정은 `validate_campaign_budget`로 일 예산이 최대 입찰가 기준 1건 상한(CPC 입찰가, CPM 입찰가/1000 내림, 11/7 PM 결정) 이상인지 검사하고 admin `daily_budget_mw` 오류로 표시한다.
- 후보 예산 필터도 같은 상한을 쓰며 제외 사유는 메모리 `ServeCandidate.filtered`와 구조화 로그 `candidates`에 `budget`으로 남긴다. 예약은 실제 낙찰 단가 기준이다.
- admin `/admin/`은 로그인·관리 권한이 필요하다. 캠페인 생성·중지·일 예산 변경은 **새 request_id의 다음 serve**부터 반영한다.
- API: POST `/ads/v1/serve`, POST `/ads/v1/impression`, GET `/ads/v1/click?t=`, GET `/ads/landing/<creative_id>`. 경로·JSON은 광고 계약 3절을 따른다.
- 내부 POST API는 토큰 중계와 서버 간 호출용으로 CSRF에서 제외한다. admin CSRF는 유지한다. 비콘은 Content-Type과 무관하게 JSON을 읽는다. 클라이언트 발생 시각은 수신하지만 판정에는 쓰지 않는다.
- 클릭은 항상 상대 랜딩 경로로 302를 반환한다. 읽을 수 없는 토큰은 `/ads/landing/unavailable`로 이동한다. 외부 랜딩 URL을 저장하거나 사용하지 않는다.
- 이미지 기본 경로는 MySQL 소재 데이터에서 읽고 `slot_image_path`로 규격을 붙인다. 실제 파일·정적 URL 연결은 B-05-03이 담당한다(`content/images/ad/` → `/static/ads/`).
- `/metrics`: `paldo_ad_serve_total{filled}`, `paldo_ad_serve_duration_seconds{stage,status}`, `paldo_ad_impression_total{status}`, `paldo_ad_click_total{status}`.
- JSON 로그는 표준 출력과 `out/logs/ads.jsonl`에 남기고 7일 보존한다. 전달받은 X-Request-ID, serve_id, charge_id, 판정·과금·지연을 기록한다. 토큰·본문·참가자 접속 헤더는 기록하지 않는다.

외부 서비스 테스트는 준비된 별도 MySQL 테스트 스키마를 사용한다. Compose 광고 계정의 권한은 자기 스키마뿐이므로 운영 스키마와 별도의 `test_paldo_ads`를 만들고 해당 스키마 권한을 로컬 관리자에게 받아야 한다. 운영 DB 이름을 테스트 DB로 지정하지 않는다.

```powershell
python ads/manage.py test tests.ads.test_http --keepdb -v 2
python -m unittest tests.contract.test_ads -v
python -m tools.perf.ads_bench --n 200 --warmup 20
python -m tools.check_evidence D-05-03 --format
```

벤치마크는 단일 Django 개발 WSGI 서버를 임시 loopback 포트에 띄우고 종료한다. 준비 20회 뒤 실제 HTTP 200회를 순차 호출하며 MySQL 조회·TCP·JSON을 포함한다. 캠페인이 모두 채워지는지 확인하고 결과는 `write_result`로 `docs/evidence/perf/D-05-03/serve.json`, 개별 표본·소스 해시는 `out/perf/ads_bench.json`에 쓴다.

### 합성 HTTP 트래픽 (D-06-03)

```powershell
python -m tools.ad_traffic_sim --run-id d0603-r5 --seed 10 --requests 10000 --invalid-rate 0.05 --fatigue-decay 0.2 --replay
python -m tools.perf.ad_traffic_bench --requests 10000
python -m unittest tests.tools.test_ad_traffic_sim -v
```

- CLI는 표준 라이브러리만 사용한다. `ads/testing/traffic_server.py`를 자식 프로세스로 띄우므로 실행 환경에는 기존 `requirements/web.txt`의 Django·PyMySQL·Prometheus 패키지가 필요하다. 기존 서비스·DB·.env를 사용하거나 변경하지 않는다. 자식의 Django/HMAC 키는 임시 난수이며 저장하지 않는다.
- 시험 서버는 기존 `ads.project.urls`와 Django WSGI 애플리케이션, `AdService`, `BudgetBook`을 그대로 사용한다. 캠페인 공급자만 기존 `synthetic_campaigns()`로 바꾸며 실제 캠페인·admin 데이터는 읽지 않는다. 광고 선택은 서버 경매가 한다.
- 정답 모델은 기존 `tools/gen_synthetic_ads.py::hidden_ctrs`를 재사용한다. 정답 CTR은 `out/ad_truth/<run>.json`에만 쓰고, 이 파일에 피로 감소율과 무효 유형별 수도 함께 기록한다. 광고 서버·HTTP 요청·관측 이벤트에 정답 CTR을 싣지 않는다. 피로는 같은 세션·소재의 이전 가시 노출마다 `CTR × (1-decay)`를 적용한다.
- 가시 기준(면적 50% 이상·1초 이상)을 만족한 경우만 비콘을 보낸다. 클릭 우선·비콘 손실이면 유효 클릭이 `implied_by_click` 노출을 만든다. 빠른 서로 다른 serve의 클릭은 suspect여도 유효다. 클릭 뒤 늦은 비콘을 일부러 추가하지 않아 0% 시나리오에 불필요한 중복을 만들지 않는다.
- `duplicate`, `bad_signature`, `no_serve`, `expired` 공격을 순환 배분한다. `no_serve`는 정상 서명의 미만료 토큰을 serve 저장 없이 발급하는 **시험 프로세스 표준입력 제어**다. 실제 HTTP 클릭의 pending을 확인하고 서버 시간 2분 경과 뒤 같은 클릭을 재판정한다. 만료 공격은 실제 serve 토큰을 받은 뒤 서버 시간을 30분 이상 전진시켜 HTTP 클릭한다. 운영 시험 API는 추가하지 않는다.
- 출력은 `out/ad_traffic/<run>/events.jsonl`·`summary.json`, 원본 서버 로그는 `out/logs/ad_traffic/<run>/ads.jsonl`이다. 이벤트는 시험 호스트가 실제 API 처리 결과에서 계약 dataclass로 기록한 로컬 관측물이다. 실제 아웃박스·Kafka 발행물이 아니다.
- `--replay`는 같은 프로세스에 모든 serve·비콘·클릭을 재전송한다. serve 응답 캐시 일치와 추가 예약·과금 0건을 검사한다. 클릭 재전송은 현재 메모리 판정에서 duplicate/expired 등으로 거부될 수 있다. 관측 이벤트 파일은 event_id별 최초 확정 판정을 유지하고 pending만 최종 판정으로 갱신한다. 영속 판정 결과 재생은 D-06-02 범위다.
- 수용 측정은 고정 시드 10의 원래 CTR로 0%·5%·20% 각각 실행하고 5%의 같은 시드·실행 ID를 새 프로세스에서 다시 실행해 이벤트 해시·요약 전체를 비교한다. 결과 JSON은 `docs/evidence/perf/D-06-03/traffic.json`이다. 기반 HEAD와 미커밋 소스 해시는 증빙에 남긴다.

## 설정값
| 이름 | 값·의미 |
|---|---|
| `--run-id` | 필수, 안전한 ASCII 영숫자·밑줄·하이픈 1~48자. 새 실행은 새 ID, 재현·재처리는 같은 ID |
| `--seed`, `--requests` | 기본 17, 10000. 요청 수는 최초 토큰의 유효 창을 넘기지 않도록 최대 180000. 같은 시드·실행 ID·설정·초기 상태에서 재현 |
| `--invalid-rate` | 기본 0.05. 최종 클릭 중 공격 비율, 정수 반올림 차이는 요약에 기록 |
| `--fatigue-decay` | 기본 0.2, 범위 0~1. 감소율이며 모델의 실측값이 아닌 시나리오 설정 |
| `--replay` | 같은 HTTP 요청을 같은 시험 프로세스에 다시 보냄 |
| DJANGO_SECRET_KEY | 필수. 광고 Django 서명용. 저장소에 기록하지 않는다 |
| PALDO_ADS_TOKEN_KEY | 필수. 광고 HMAC 키. 저장소에 기록하지 않는다 |
| PALDO_ADS_INTERNAL_URL | 내부 서비스 호스트. 로컬 기본 http://127.0.0.1:8001 |
| PALDO_ADS_DB_HOST·PORT·NAME·USER·PASSWORD | 광고 전용 MySQL. Compose는 127.0.0.1:3316, VM 기본 포트는 3306 |
| PALDO_ADS_DB_TEST_NAME | 별도 테스트 스키마. 기본 test_paldo_ads |
| PALDO_LOG_DIR | 기본 out/logs |
| 지면 정책 | fixtures/ads/campaigns.json의 slot_policies. 프로세스 시작 시 읽음 |
| 버전 | 계약 ads-0.5(지면 3곳, llm_wait 삭제), auction-v0.1, beta-prior-v0.1. 토큰 30분, 짝 맞추기 유예 2분 |
| 금액·귀속일 | 정수 밀리원, serve 시각의 UTC 날짜. 1건 금액은 그 건의 단가만으로 정함(CPM `floor(단가/1000)`, 나머지 이월 없음, `ads-0.6`). 지면 최저 eCPM 하한 100원 |
| DB 드라이버 | requirements/web.txt의 PyMySQL. 게임 웹 설정은 import하지 않음 |

## 한계
- 원장·판정·응답 캐시·빈도는 단일 프로세스 메모리다. 재시작 시 사라지며 여러 worker 간 공유되지 않는다. 다중 worker 운영은 D-06-02 전까지 사용하지 않는다.
- 만료 예약은 새 serve 시 정리한다. 별도 만료 프로세스, 영속 이벤트·멱등 판정 저장, 환입 소비자, Kafka는 D-06-02에서 구현한다.
- CTR은 지면 사전분포만 사용한다. 실제 집계 갱신·페이싱 연결은 D-08-02다. 캠페인별 탐색 하한도 아직 구현하지 않는다.
- D-05-03 조건 2는 미충족으로 남겼다. 경매·유효 판정과 예산 예약·확정은 HTTP 경유, 환입·대사는 기존 도메인 함수를 직접 검증했다. 계약에 없는 유지관리 HTTP API는 만들지 않았다. B-05-03은 정적 이미지 연결이며 환입·대사 API를 제공하는 태스크가 아니다.
- 현재 p95는 개발 서버의 로컬 순차 호출 실측이다. B의 실제 게임 웹 중계·네트워크·장애 시험은 B-08-03에서 확인한다.
- 이미지는 게임 웹이 제공한다. 광고 서버는 외부 공개용 CORS·정적 이미지 서비스를 제공하지 않는다.

- 합성 HTTP 검증은 단일 프로세스 메모리 경로다. MySQL 캠페인/admin 회귀, 실제 원장·아웃박스·Kafka·silver 연결과 재시작 간 멱등성은 이 시험의 검증 범위가 아니다.
- 같은 실행 ID의 파일은 재실행 시 덮어쓴다. 다른 실행을 보존하려면 새 ID를 쓴다. 같은 시드라도 다른 실행 ID는 경매 추출이 달라질 수 있다. 작은 요청 수에서는 무효 유형 네 가지가 모두 나오지 않을 수 있으며 수용 벤치마크가 이를 실패로 잡는다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 독립 Django API·admin, 메모리 예산·판정, 로그·지표·HTTP 테스트·지연 측정. 조건 2 미충족 범위 기록. 11/7 변경 기준 5·9: 등록 예산 검증·budget 후보 기록과 경계값 테스트 보강. 11/7 PM 결정(나머지 이월 폐지, `ads-0.6`): CPM 1건 금액 floor, 지면 최저 eCPM 하한 100원, 환입은 원과금만 1회, 순액 대사(`orphan_refund`) |
| D-06-01 | 11/6 | 게임 플레이어 봇의 실행·지표는 sim 모듈 문서로 연결하고 광고 HTTP 선택과 분리(일괄 문서화) |
| D-06-03 | 11/6 | 실제 Django HTTP 합성 트래픽·정답 격리·피로·4종 무효 클릭·재전송·재현 검증. D-05-03 조건 2 보류 및 D-06-02 외부 저장 범위 유지 |
