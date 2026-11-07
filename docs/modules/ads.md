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
- 등록·수정은 `validate_campaign_budget`로 일 예산이 최대 입찰가 기준 1건 상한(CPC 입찰가, CPM 입찰가/1000 올림) 이상인지 검사하고 admin `daily_budget_mw` 오류로 표시한다.
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

## 설정값
| 이름 | 값·의미 |
|---|---|
| DJANGO_SECRET_KEY | 필수. 광고 Django 서명용. 저장소에 기록하지 않는다 |
| PALDO_ADS_TOKEN_KEY | 필수. 광고 HMAC 키. 저장소에 기록하지 않는다 |
| PALDO_ADS_INTERNAL_URL | 내부 서비스 호스트. 로컬 기본 http://127.0.0.1:8001 |
| PALDO_ADS_DB_HOST·PORT·NAME·USER·PASSWORD | 광고 전용 MySQL. Compose는 127.0.0.1:3316, VM 기본 포트는 3306 |
| PALDO_ADS_DB_TEST_NAME | 별도 테스트 스키마. 기본 test_paldo_ads |
| PALDO_LOG_DIR | 기본 out/logs |
| 지면 정책 | fixtures/ads/campaigns.json의 slot_policies. 프로세스 시작 시 읽음 |
| 버전 | 계약 ads-0.5(지면 3곳, llm_wait 삭제), auction-v0.1, beta-prior-v0.1. 토큰 30분, 짝 맞추기 유예 2분 |
| 금액·귀속일 | 정수 밀리원, serve 시각의 UTC 날짜. CPM 나머지 이월은 BudgetBook 기준 |
| DB 드라이버 | requirements/web.txt의 PyMySQL. 게임 웹 설정은 import하지 않음 |

## 한계
- 원장·판정·응답 캐시·빈도는 단일 프로세스 메모리다. 재시작 시 사라지며 여러 worker 간 공유되지 않는다. 다중 worker 운영은 D-06-02 전까지 사용하지 않는다.
- 만료 예약은 새 serve 시 정리한다. 별도 만료 프로세스, 영속 이벤트·멱등 판정 저장, 환입 소비자, Kafka는 D-06-02에서 구현한다.
- CTR은 지면 사전분포만 사용한다. 실제 집계 갱신·페이싱 연결은 D-08-02다. 캠페인별 탐색 하한도 아직 구현하지 않는다.
- D-05-03 조건 2는 미충족으로 남겼다. 경매·유효 판정과 예산 예약·확정은 HTTP 경유, 환입·대사는 기존 도메인 함수를 직접 검증했다. 계약에 없는 유지관리 HTTP API는 만들지 않았다. B-05-03은 정적 이미지 연결이며 환입·대사 API를 제공하는 태스크가 아니다.
- 현재 p95는 개발 서버의 로컬 순차 호출 실측이다. B의 실제 게임 웹 중계·네트워크·장애 시험은 B-08-03에서 확인한다.
- 이미지는 게임 웹이 제공한다. 광고 서버는 외부 공개용 CORS·정적 이미지 서비스를 제공하지 않는다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 독립 Django API·admin, 메모리 예산·판정, 로그·지표·HTTP 테스트·지연 측정. 조건 2 미충족 범위 기록. 11/7 변경 기준 5·9: 등록 예산 검증·budget 후보 기록과 경계값 테스트 보강 |
