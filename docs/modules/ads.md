# 광고 서버

## 무엇을
- `ads/domain/`: 표준 라이브러리 경매·예산·서명·유효 판정 기준 구현.
- `ads/app/serve.py`: 캠페인·타기팅·빈도·예산 필터, 소재별 사전 CTR 톰슨 추출, 2등 가격, 예산 예약. 동일 request_id·동일 입력은 응답을 재사용하고 다른 입력이면 409.
- `ads/app/beacon.py`·`click.py`: 토큰·최초 수신 시각으로 판정, CPC 클릭 또는 CPM 노출 확정. 클릭이 먼저 오면 노출도 인정한다.
- `ads/project/`·`ads/web/`: 게임 웹과 독립된 Django 서비스. MySQL은 캠페인·소재와 admin 계정, 일 예산 원장, serve·판정·아웃박스를 저장한다. 일 예산은 캠페인의 `daily_budget_mw`로 관리한다.

## 왜
기획서 v1.3 9장(v1.1 14장), `docs/contracts/ads.md` 3~7절을 따른다. 게임 서버는 AdProvider 포트로 호출하고 브라우저에는 게임 웹 출처만 노출한다. D-06-02는 원장·판정·이벤트를 한 MySQL 트랜잭션에 기록하고 별도 프로세스에서 발행한다.

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

### 원장과 별도 작업자 (D-06-02)

- `ads/infra/schema.sql`은 Django migration `0002_mysql_ledger`·`0003_pending_rejudgement`·`0004_optimization_versions`의 SQL 참조다. 광고 DB에는 위의 `migrate`로 적용한다.
- 광고 웹과 아래 명령 세 개를 각각 독립 프로세스로 실행한다. 환입 프로세스는 브로커·DB 실패 시 커밋하지 않은 offset부터 다시 읽도록 서비스 관리자의 재시작 설정을 사용한다.
- 로컬은 Compose Kafka를 시작하고 `PALDO_KAFKA_BOOTSTRAP`을 지정한다. 운영은 C가 인계한 광고 DB TLS 계정·CA 및 Kafka TLS 클라이언트 설정을 사용한다. 브로커에 이미 생성된 `ad.*` 토픽을 사용한다.
- 발행 완료 표시는 브로커 ACK 후 기록한다. ACK와 완료 표시 사이에 중단되면 같은 `event_id`를 다시 보낸다. C 소비자는 이 ID로 중복을 제거한다. 원장 해제·환입의 상태 변경 기록은 같은 DB 트랜잭션에 남고 공개 Kafka 토픽은 이벤트 계약의 세 종류를 사용한다.
- worker 로그는 `component`로 구분한다. 각 `--metrics-port`의 루프백 HTTP 엔드포인트에 `paldo_ad_worker_processed_total`과 `paldo_ad_worker_errors_total`이 노출된다.
- 만료 작업자는 최초 수신 뒤 유예 2분이 끝난 pending 이벤트도 다시 판정한다. 검증된 claims·최초 수신 시각·비콘 값만 저장하고 원문 토큰은 저장하지 않는다. 별개 이벤트는 각자의 최초 수신 시각으로 만료를 판정한다.

```powershell
docker compose -p paldo-dev -f ops/compose/dev.yml up -d kafka
$env:PALDO_KAFKA_BOOTSTRAP = "127.0.0.1:9092"
python ads/manage.py ad_worker expire --metrics-port 9101
python ads/manage.py ad_worker publish --metrics-port 9102
python ads/manage.py ad_worker refund --metrics-port 9103
```

운영의 publish와 refund에는 각각 `--kafka-config <보호 JSON 파일>`을 지정한다. 테스트용 DB와 로컬 브로커에서 다음 명령을 실행한다.

```powershell
$env:PALDO_ADS_TEST_KAFKA = "127.0.0.1:9092"
python ads/manage.py test tests.ads.test_http tests.ads.test_mysql_ledger tests.ads.test_persistent_http tests.ads.test_workers tests.ads.test_worker_stack --keepdb
```

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

### CTR·페이싱·시나리오 (D-08-02)

`ads/app/ctr_update.py`는 전체 확정 관측 셀을 소재×지면으로 합해 사전분포부터 재계산한다. 같은 소재가 캠페인을 옮긴 이력도 합한다. 이전 사후분포에 새 집계를 더하지 않는다. `ads/infra/optimization.py`는 불변 MySQL 버전 행을 저장하고 HTTP 프로세스마다 60초 TTL로 최신 기준 시각의 행을 읽는다. 첫 요청/재시작도 DB에서 시작한다. 재적재 실패 시 마지막 정상 캐시와 오류 로그를 남긴다.

아래 명령은 설정을 주입한 개발 환경에서 실행한다. 11/8 기존 개발 DB 적용·기동은 자동 승인 검토에서 차단됐다. 11/9 사용자 요청으로 별도 MySQL 8.4.11 컨테이너와 B/D Daphne 서버를 기동해 세 지면 직접 연결을 검증했다. B 인증은 임시 SQLite, 회차·광고 가명은 메모리 시험 저장소로 구성했다. 원본·명령·범위는 [D-08-02 증빙](../evidence/D-08-02.md)에 있다.

```powershell
python ads/manage.py migrate ads_web 0004 --noinput
python ads/manage.py ad_update ctr --input <C가-내보낸-전체-확정-집계.json>
python ads/manage.py ad_update pacing --once
python ads/manage.py ad_update pacing
python ads/manage.py runserver 127.0.0.1:8002 --noreload
python -m tools.ad_scenarios --scenario 1..5 --seeds 17,29,43 --requests 12000
$env:PALDO_SERVICE_TESTS = "1"
python ads/manage.py test tests.ads --keepdb
```

- CTR 입력 봉투·셀 형식은 [ads 계약 5절](../contracts/ads.md)에 있다. C-08-02 gold의 물리 스키마가 이 형식이라는 뜻은 아니다. C가 확정 범위·원과금일 환입·노출/클릭의 유효 상태·전체 export를 보장한 뒤 연결한다. 부분 집계 파일을 전체 입력처럼 쓰면 이전 관측을 잃으므로 금지한다.
- 페이싱은 단일 작업자가 5분 버킷마다 Primary의 예약+확정 순액을 읽는다. 같은 버킷 재실행으로 두 번 조정하지 않는다. 확률 범위·조정 폭은 기존 도메인 함수(0.05~1, ±0.2)를 사용한다. CTR 입력의 비용이나 평가 정답은 소진액으로 쓰지 않는다.
- serve는 CPC 순위와 CPM 소재 선택 모두 적재된 Beta를 추출한다. 탐색 부족분은 노출이 가장 적은 합법 소재를 고르되 페이싱을 우회하지 않는다. 유효 노출만 탐색 분모·분자로 세며, MySQL의 기존 serve 기록으로 재시작 뒤에도 이어간다. 요청마다 캠페인 일 노출 집계를 SQL 집계로 읽으므로 운영 규모에서 해당 쿼리 지연은 추가 측정이 필요하다.
- 평가 생성기는 학습 생성기를 import하지 않고 시간 변화·사용자 이질성·반복 피로·1~90초 지연 클릭·비콘 손실을 만든다. 3개 지면을 모두 사용한다. 정답은 `out/ad_scenarios/*-truth.json`, 모델에 주는 입력은 확정 관측 N/C뿐이다. CTR 평가는 최종 사후분포와 노출 가중 잠재 CTR을 비교하며, 학습 초기를 포함하는 온라인 예측 오차도 별도로 보고한다. 두 수치를 혼동하지 않는다.
- 비교 정책은 톰슨, 독립 Uniform(0,1) CTR 점수의 무작위 순위, 지면 사전 CTR 고정 탐욕, 평가 전용 true-CTR oracle이다. 초기 요청·후보·예산·빈도·탐색·페이싱 규칙을 같게 둔다. 후회는 oracle 전일 유효 클릭/확정 순액에서 각 정책의 같은 값을 뺀 부호 있는 차이이며, 표본 변동·가격/예산 경로 때문에 음수도 가능하다. 이 oracle이 전역 최적이라는 주장은 하지 않는다. random/fixed/oracle의 CTR 오차 열은 저장된 사전분포 진단값이고 순위 점수의 정확도는 아니다(oracle은 순위에 정답을 쓴다). CTR 합격선은 톰슨 정책에만 적용한다.
- CLI는 시드 3개 이상을 요구하고 로컬 수치/기대 동작 미달 시 종료 코드 1을 반환한다. 원본·정답·표·JSON은 `out/ad_scenarios/`, 커밋할 측정 요약은 `docs/evidence/perf/D-08-02/scenarios.json`이다. 결론은 **알고리즘·회계·제어 루프 동작 검증**으로 한정한다.
- B-08-03 로컬 직접 연결은 세 지면의 serve 200·인증 웹소켓 비콘/D 204 valid·클릭 302 valid·랜딩 200을 확인했다. B/D 요청 ID는 12건 일치했고 MySQL 아웃박스는 9건이었다. 첫 로컬 시험에서는 웹 원본에 serve_id가 없어 대조 표를 만들었다. 이후 B 보완을 반영한 app-01 시험 웹·ad-01 원본 로그에서 동일 요청/serve ID 8쌍을 대조했다. 운영 배포본 브라우저 확인은 B-08-03 담당 E의 후속 범위다. B에 전달할 설정은 내부 주소와 네 경로, 요청/serve ID 조회 방법이며 서명 키·광고 DB 인증은 D에서 관리한다.
- 초기 개발 DB 점검에서는 0001만 적용돼 있었다. 이후 격리 MySQL에서 전체 migration을 검증하고, 운영자가 ad-01의 기존 0001~0003 위에 `0004_optimization_versions`를 적용했다. 새 원장/예산/요청/세션/이벤트/판정/버전 테이블과 pending 열·색인을 추가한다. SQL은 `ads/infra/schema.sql`, Django 적용 파일은 `ads/web/migrations/0002*`~`0004*`에서 검토할 수 있다.

### 실제 VM 합성 입력 (D-08-02 조건 7)

11/9 운영자가 ad-01에서 D-08-02 코드 11개와 migration 0004를 적용했다. 배포 보고서의 해시 11개가 로컬 소스와 일치했고 광고·만료·발행·환입 서비스가 모두 active이며 실제 metrics는 HTTP 200이었다.

같은 날 D 실행 `c08_d0802_20261109b`의 실제 요청 1,000건과 공식 bronze 1,960건을 대조했다. C Spark의 기준선 대사와 Kafka 재판정 한 건에 따른 MySQL −524,590밀리원 환입 후 재대사는 모두 불일치 0건이다. 최종 Delta·MySQL 순액은 6,815,093밀리원이다. 확정 단계는 합성 검증용 논리 종료 시각을 적용했다.

검증된 Delta 집계의 유효 노출 913·클릭 43을 CTR 3행으로 변환해 실제 MySQL에 저장했다. 페이싱 11행도 실제 원장의 예약·확정 순액으로 계산해 저장했다. CTR 재실행과 같은 5분 bucket의 페이싱 재실행은 각각 버전 1행을 유지했다. 실제 광고 서버는 갱신 직후 기존 버전을 유지하고 61초 뒤 재시작 없이 새 버전을 세 지면에 반영했다. serve·노출·클릭 9개 이벤트의 버전 고정과 Beta 평균 사용, 부하 시험의 확정 과금 0건·캠페인 상태 복구를 확인했다. [실제 버전 증빙](../evidence/perf/D-08-02/live_versions.json)

app-01에서는 별도 Daphne·임시 SQLite·loopback Mongo replica set으로 10명 기준 정상 5분과 장애 4종 각 5분을 측정했다. 정상 구간은 실제 ad-01의 합성 캠페인, 장애 구간은 시험용 loopback listener를 사용했다. 보완 전에는 네 장애의 p95 증가가 542.802~666.232ms로 기준을 넘었다. dev `44a6206` 재시험에서는 게임 API 15,000건의 오류가 0건이고, 새 정상 대비 장애별 p95 변화는 −40.240~−14.249ms로 조건 2의 측정 기준을 충족했다. 장애 클릭의 홈 복귀는 0건이다. 운영 웹 배포·브라우저 검증은 남아 있다. [D-08-02 증빙](../evidence/D-08-02.md)

`tools.ad_traffic_sim --base-url <내부 출처> --campaigns-manifest <JSON> --run-id <ID>`는 운영자가 준비한 합성 캠페인만 사용해 실제 serve·비콘·클릭을 전송한다. manifest는 `run_id`와 도메인 `Campaign` JSON 목록인 `campaigns`를 담는다. 숨긴 CTR은 클라이언트의 `out/ad_truth/<ID>.json`, 관측은 `out/ad_traffic/<ID>/http.jsonl`에 저장한다. 같은 실행의 파일이 있으면 거절해 덮어쓰기를 방지한다. 실행 ID가 `c08_d0802_...`이면 C의 기존 `syn:c08_` 입력 검증과 호환된다.

실제 서버 모드의 공격은 원래 합성 serve에 연결되는 중복 클릭이다. 위조 토큰은 원래 source를 판정할 수 없어 실제 지표에 섞일 수 있으므로 `unsupported_invalid_types`에 위조·만료·serve 없음을 표시한다. 자체 서버의 시계·서명 제어는 실제 서버에 제공하지 않는다. HTTP 성공은 원장 검증과 별도이고 `ledger_verified=false`를 기록한다. 운영자는 해당 실행의 원본 아웃박스·원장·예산을 내보내 C의 실제 bronze 전량 대조·silver/gold·건별 대사에 사용해야 한다. 자체 서버 모드는 기존 CLI로 실행한다.

### 맥락 CTR과 자동 입찰 (D-10-02)

`ads_gold.py --phase final --export-optimization <폴더>`는 고정 Delta 버전에서 `ctr.json`과 `bid.json`을, 같은 silver snapshot에서 `ad_update ctr` 입력 `ctr_cells.json`을 내보낸다. 하루 1회 자동 실행은 `ops/ads_aggregate.sh`(ad-01 timer, `python ads/manage.py ad_export_ledger --out <폴더>`로 원장·일 예산 내보내기 포함)이고 절차는 `pipeline/README.md`에 있다. 학습·등록은 [ML 사용법](ml.md#광고-ctr-d-10-02)을 따르고 운영자에게 등록된 후보 보고서를 전달한다.

```powershell
python ads/manage.py migrate ads_web 0005 --noinput
python ads/manage.py ad_update bid --input <확정-Delta-export>/bid.json
python ads/manage.py ad_update ctrml --input <등록된-후보-보고서>.json
python ads/manage.py ad_update rollback-ctr --expected-version <활성-CTR-버전> --signal fallback_rate --value 0.0625
```

- `ad_ctr_canary` 로그의 `fallback`과 `lookup_ms`로 악화 신호를 확인한다. 조회를 시도한 요청에서 일부라도 베타로 내려가면 폴백이다. 롤백은 운영자가 실행하며 60초 캐시로 반영된다. 그동안 이미 발행된 토큰은 원래 경매 버전을 보존한다.
- 자동 입찰은 CPC `target_cpc_mw`로 활성화하고 `min_bid_mw`~`max_bid_mw`에서만 갱신한다. 최신 확정일에 과금 방식이 섞인 캠페인은 입력에서 제외한다. 동일 입력의 재시도는 같은 MySQL 버전을 반환한다. 새 값은 경매 아웃박스 `candidates[].bid_mw`와 `bid_version`에서 확인한다. 실현 CPC는 다음 경매의 2등 가격과 트래픽에 따라서도 달라진다.
- `ctrml`을 실행하는 운영자 프로세스는 MLflow 접속 설정과 ML 패키지를 사용해 후보 보고서를 레지스트리의 원본과 대조한다. 광고 요청 처리와 `rollback-ctr`은 MySQL만 읽는다.
- 5분 페이싱은 시간대별 소진 오차를 줄이기 위해 이득 8.0을 쓴다. 하루 소진 오차·카나리아·실현 CPC 실측은 [D-10-02 증빙](../evidence/D-10-02.md)을 참조한다.

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
| PALDO_ADS_DB_CONN_MAX_AGE | DB 연결 재사용 초(기본 60, 0이면 요청마다 닫음). 운영 gunicorn gthread 고정 스레드에서만 효과가 있다(`CONN_HEALTH_CHECKS` 켬) |
| `ads_server`(보호 JSON) / PALDO_ADS_SERVER | 광고 서버 실행 방식 `gunicorn`(기본)·`daphne`(되돌리기). `ops/mysql/service_runtime.py` |
| `ads_threads`(보호 JSON) | gunicorn 작업 스레드 수(1~64, 기본 16). 스레드마다 DB 연결을 따로 가지므로 지연 비교 때 낮춰 본다 |
| serve 단계 시간(`ad_http` 로그 `timing`) | serve 한 건의 단계별 ms(11/11 지연 원인 분리, `ads/infra/timing.py`): `connect`(상태 확인·연결 확보)·`new_connection`·`stale_connection_replaced`·`thread_first_connection`, `build_service`, `session_lock`, `stored_request`, `frequency_query`, `decide`(아래 단계 포함: `campaigns_load`, `campaign_lock`, `exposure_query`, `budget_check`, `budget_reserve`), `save`, `outbox`, `commit`, `db_queries`·`db_ms`, `thread`. 같은 단계가 여러 번이면 `<단계>_n`. 값만 남기고 세션 가명은 남기지 않는다 |
| PALDO_ADS_DB_HOST·PORT·NAME·USER·PASSWORD | 광고 전용 MySQL. Compose는 127.0.0.1:3316, VM 기본 포트는 3306 |
| PALDO_ADS_DB_TEST_NAME | 별도 테스트 스키마. 기본 test_paldo_ads |
| PALDO_LOG_DIR | 기본 out/logs |
| 지면 정책 | fixtures/ads/campaigns.json의 slot_policies. 프로세스 시작 시 읽음 |
| PALDO_ADS_DB_SSL_CA | 운영 광고 DB CA 파일 경로. 설정 시 CA와 서버 호스트 이름을 함께 검증한다 |
| PALDO_KAFKA_BOOTSTRAP, --kafka-config | 로컬 브로커 또는 운영 Kafka TLS 클라이언트 JSON 파일 |
| 버전 | 계약 ads-0.6. 버전 행이 없으면 auction-v0.1/beta-prior-v0.1, 적재 후 pacing/ctr SHA-256 버전. 토큰 30분, 짝 맞추기 유예 2분 |
| 금액·귀속일 | 정수 밀리원, serve 시각의 Asia/Seoul 날짜. 1건 금액은 그 건의 단가만으로 정함(CPM `floor(단가/1000)`, 나머지 이월 없음, `ads-0.6`). 지면 최저 eCPM 하한 100원 |
| DB 드라이버 | requirements/web.txt의 PyMySQL. 게임 웹 설정은 import하지 않음 |

## 한계
- D-06-03의 합성 시험 호스트는 메모리 원장으로 격리한다. 운영 HTTP 원장·판정·응답 재생·빈도는 MySQL을 사용한다.
- 11/9 ad-01 작업자 배포, 실제 Kafka 재판정·환입·Delta 재대사와 합성 집계 기반 버전 반영을 검증했다. 운영 전체 광고 gold의 상시 스케줄과 사람 광고 성과는 후속 범위다.
- 원장·아웃박스·요청 재생 데이터의 보존 기간과 삭제 절차는 운영 인계에서 정한다.
- CTR·페이싱·탐색은 D-08-02로 연결했다. 같은 D 실행의 C 실제 Delta 집계·대사·환입과 ad-01 버전 저장·재적재를 검증했다. 합성 검증용 논리 마감 시각과 결과 범위는 해당 증빙에 보존한다.
- D-05-03 조건 2는 11/8 PM 결정(PR #263) 반영 뒤 로컬 검증을 통과했다. 경매·유효 판정과 예산 예약·확정·해제는 HTTP 경유, 환입·대사는 HTTP로 만든 원장에 도메인 연산을 적용한다. 대사 시험도 원과금을 HTTP serve·click으로 만들고 원장 금액·상태·행 수와 모든 픽스처 기대값을 확인한다. 실제 Kafka 환입·Delta 대사는 D-06-02·C-08-02의 별도 범위다. [D-05-03 증빙](../evidence/D-05-03.md)에 결정 근거와 재검증 범위를 기록했다.
- 현재 p95는 개발 서버의 로컬 순차 호출 실측이다. B의 실제 게임 웹 중계·네트워크·장애 시험은 B-08-03에서 확인한다.
- 이미지는 게임 웹이 제공한다. 광고 서버는 외부 공개용 CORS·정적 이미지 서비스를 제공하지 않는다.

- 합성 HTTP 검증은 단일 프로세스 메모리 경로다. MySQL 캠페인/admin 회귀, 실제 원장·아웃박스·Kafka·silver 연결과 재시작 간 멱등성은 이 시험의 검증 범위가 아니다.
- 같은 실행 ID의 파일은 재실행 시 덮어쓴다. 다른 실행을 보존하려면 새 ID를 쓴다. 같은 시드라도 다른 실행 ID는 경매 추출이 달라질 수 있다. 작은 요청 수에서는 무효 유형 네 가지가 모두 나오지 않을 수 있으며 수용 벤치마크가 이를 실패로 잡는다.

## 변경 이력
| 태스크 | 날짜 | 바뀐 점 |
|---|---|---|
| D-05-03 | 11/5 | 독립 Django API·admin·메모리 예산·판정·로그·지표. 등록·필터는 최대 입찰가 기준, ads-0.6 CPM 건별 floor·나머지 이월 없음·원과금 1회 환입·순액 대사. 11/8 PM 결정(PR #263)을 반영해 HTTP로 생성한 원장 대사 시험 보강, 조건 1~9 로컬 검증 통과 |
| D-06-01 | 11/6 | 게임 플레이어 봇의 실행·지표는 sim 모듈 문서로 연결하고 광고 HTTP 선택과 분리(일괄 문서화) |
| D-06-02 | 11/6 | ads-0.6 MySQL 원장·판정 재생·아웃박스, 만료·Kafka 발행·환입 작업자, 로컬 통합과 운영 인계 구분 |
| D-06-03 | 11/6 | 실제 Django HTTP 합성 트래픽·정답 격리·피로·4종 무효 클릭·재전송·재현 검증. D-05-03 조건 2의 최신 판정은 해당 증빙 참조, D-06-02 외부 저장 범위 유지 |
| D-08-02 | 11/8 | 전체 확정 Beta·MySQL 버전·60초 재적재·5분 페이싱·탐색·독립 평가. 11/9 실제 C 집계·환입 재대사·ad-01 새 버전 반영과 B 로그·장애 재시험 통과. 병목에 따른 인계 지연을 기록하고 사용자 승인으로 완료 판정 |
| D-08-04 | 11/8 | 일괄 문서화 대상으로 D-08-02의 실행·설정·실측·한계와 변경 이력 6개 절 확인 |
| D-10-02 | 11/9 | 맥락 CTR 카나리아·롤백, CPC 상하한·버전 갱신, 페이싱 제어 이득 보정 |
| C-08-02 | 11/11 | 하루 집계 자동화: `ad_export_ledger`(원장·일 예산 내보내기), `ctr_cells.json` → `ad_update ctr`, ad-01 timer(E 보강) |
| D-06-03 | 11/11 | 합성 트래픽 시험 호스트 대기 한도를 시작 180초·제어 30초로 나눔(E 보완, 병렬 검사 중 시작 시간 초과 수정) |
| B-08-03 | 11/11 | 지면 빈도 제한 3 → 1000(사실상 해제, 사용자 결정). 소재가 지면당 1~3개라 한 로그인 몇 판 만에 빈 응답. 운영 ad-01 픽스처도 같은 값으로 바꿈(E 보완) |
| B-08-03 | 11/11 | 광고 서버 실행을 daphne → gunicorn gthread(프로세스 1·스레드 16)로 바꾸고 DB 연결 60초 재사용(사용자 결정). 운영 실측에서 요청마다 원격 MySQL TLS 연결을 새로 맺어 첫 조회 약 230ms(이후 1~9ms)였고 게임 웹 300ms 제한을 자주 넘었다. 프로세스 1개는 지표 레지스트리·서비스 상태가 프로세스 안에 있어서. `ads_server: daphne`로 되돌림(E 보완) |
| B-08-03 | 11/11 | 캠페인 시간 조건(`targeting.hours`)을 서울 시각 기준으로 통일. 게임 웹이 서버 시간대(UTC)와 무관하게 서울 시각을 보냄. 운영 갓 캠페인 값도 같은 배포에서 서울 홀수 시로 바꿈(E 보완) |
