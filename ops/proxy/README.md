# 내부 사용자 테스트 HTTPS

게임 웹 앞에 Caddy를 두고 HTTPS·wss를 제공한다. `Caddyfile`은 내부 CA(`tls internal`)를 쓰며 HTTP를 308로 보낸다. HSTS는 보내지 않는다. 사이트와 bind는 참가자가 입력할 내부 주소 `PALDO_PROXY_HOST`와 같아야 하며 인증서 SAN도 일치해야 한다. 실제 주소와 접근 시간은 저장소 밖 운영 인계 기록을 E가 참가자 안내에 반영한다. 광고 서버는 이 프록시에 붙이지 않는다.

## 기동·종료

서버에서 Caddy의 서명된 배포 패키지를 설치한다. 환경 파일에 `PALDO_PROXY_HOST`(스킴·포트 없는 내부 호스트), `PALDO_STATIC_ROOT`(공개 static 복사 디렉터리)를 두고 Caddy systemd unit의 EnvironmentFile로 읽힌다. source/out 전체를 Caddy에 읽히지 않고 collectstatic 결과만 별도 공개 디렉터리에 복사한다. 환경값을 적용한 `caddy validate --config <설정 경로>`가 성공한 뒤 다음을 실행한다.

```sh
sudo systemctl start caddy paldo-web
sudo systemctl status caddy paldo-web
sudo journalctl -u caddy --since '10 minutes ago'
# 종료는 기존 PJ3 서비스와 별개인 두 unit만 대상으로 한다.
sudo systemctl stop caddy paldo-web
```

웹은 Daphne **2개·127.0.0.1:8000·8002**로 실행한다(E-09-10, 아래 "웹 프로세스 2개" 절). 웹의 `PALDO_PUBLIC_ORIGIN`은 브라우저 HTTPS 출처와 같게 설정한다. Caddy만 내부 인터페이스의 80/443에 bind한다. XFF는 사용자 입력을 버리고 실제 remote_host로 덮어쓰고 XFP는 https로 덮어쓴다. 처음 bind를 좁히는 reload가 기존 wildcard HTTP/3 UDP listener와 충돌하면 검증한 설정으로 Caddy를 재시작한다. 방화벽 정책 변경은 별도 운영 범위이며 이번 검증에서 변경하지 않았다.

## 웹 프로세스 2개 (E-09-10)

기본 구성은 Daphne 2개 + `InMemoryChannelLayer`다. 지금 웹소켓은 광고 신호 전용이라 프로세스 사이 전달이 없다(근거는 [서버 설계](../../server/DESIGN.md) "동시성 구조와 한계"). `Caddyfile`은 두 upstream(8000·8002)을 `least_conn`으로 나누고 `/healthz`를 5초마다 확인한다(Host는 `PALDO_PROXY_HOST`). 실제 unit 파일 경로·프로필 경로는 비공개 운영 인계 기록을 따른다.

### 필수 절차 (C, 약 20~30분)

1. 웹 unit을 포트 인자를 받는 템플릿으로 바꾼다. 기존 `paldo-web` unit을 복사해 `paldo-web@.service`로 만들고 실행 줄만 `service_runtime.py --profile <보호 프로필> web --port %i`로 바꾼다. `Restart=always`, `RestartSec=2`를 둔다(한 프로세스가 죽으면 systemd가 다시 띄우고, 그동안 Caddy가 그 upstream을 뺀다). 허용 포트는 8000·8002뿐이다.
2. 적용한다.
   ```sh
   sudo systemctl daemon-reload
   sudo systemctl disable --now paldo-web
   sudo systemctl enable --now paldo-web@8000 paldo-web@8002
   caddy validate --config <설정 경로>   # 환경값 적용 상태로
   sudo systemctl reload caddy
   ```
3. 확인한다.
   - 각 프로세스: `curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $PALDO_PROXY_HOST" http://127.0.0.1:8000/healthz`, 같은 명령을 8002로. 둘 다 200.
   - Caddy: `sudo journalctl -u caddy --since '5 minutes ago' | grep -i unhealthy`가 비어 있음.
   - 브라우저: HTTPS 홈 200, `/ws/game/` 101(위 "참가자에게 전달할 문구"의 운영자 확인과 같다).
   - 로그: 소스의 `out/logs/web.jsonl`(8000)과 `out/logs/web-8002.jsonl`(8002)이 각각 늘어남.
4. 지표: C-08-01의 수집 설정에서 게임 웹 대상을 프로세스마다 둔다(8000·8002, `process` 라벨 `web-1`·`web-2`, 로컬 예시는 `ops/compose/prometheus.yml`의 `paldo-web`). 웹은 loopback에만 bind하므로 metrics-01에서 원격으로 긁으려면 app-01 안에서 수집해 넘기는 방식이 필요하다. 방식은 C-08-01에서 정한다. 수집 요청의 Host는 대상 주소의 호스트(예: `127.0.0.1`)이고 Prometheus는 Host 헤더를 바꿀 수 없으므로, 웹 프로필 `environment`에 `PALDO_EXTRA_ALLOWED_HOSTS=127.0.0.1`처럼 수집용 호스트를 더해야 `/metrics`가 400이 아니다(기본 없음). 대시보드의 게임 API p95·오류율은 두 프로세스를 합산한다.

### 되돌림 (1개, 약 2분)

```sh
sudo systemctl disable --now paldo-web@8002
```

Caddy 설정은 바꾸지 않는다. 능동 확인이 8002를 빼고, 그 사이 접속 실패는 8000으로 다시 보낸다. 다시 2개로: `sudo systemctl enable --now paldo-web@8002`.

### 프로세스 수 비교 측정 (C-08-04 5번)

`tools/perf/api_bench.py --web-processes`가 같은 서버에서 시험용 daphne를 N개 직접 띄우고, 운영과 같은 분배·상태 확인 설정의 Caddy를 앞에 세워 같은 부하(사용자당 초당 1요청 + 사용자별 웹소켓 비콘 1개)를 경우마다 차례로 준다. 운영 중인 `paldo-web@8000·8002`와 Caddy는 건드리지 않는다(빈 포트를 따로 고른다). 이미 떠 있는 앞단 주소로 부하만 보내는 모드는 없다. 이 부하는 시험용 엔진과 시험 접두어 컬렉션을 쓰므로 실제 엔진이 도는 서버로 보낼 수 없다.

- C-08-04 5번 명령 `python -B tools/perf/api_bench.py --compare-asgi --users 30 --minutes 3`에 `--web-processes`를 붙이면 이 비교로 넘어간다(`--compare-asgi`는 받기만 한다). `--result-name <이름>`을 주면 증빙 결과 JSON을 `docs/evidence/perf/<--result-task>/<이름>.json`에 쓴다(기본 태스크 E-09-10, C 측정은 `--result-task C-08-04` 등 해당 태스크로).
- 옵션: `--web-processes 2,1`(띄울 수 목록, 1~4), `--channel-layer memory|redis`(기본 memory, redis면 `--redis-url` 필요), `--users`(기본 30), `--minutes`(기본 3), `--front docker|binary`(기본 docker. Docker가 없으면 `binary`와 `--caddy-bin <caddy 경로>`. Linux의 docker는 host 네트워크로 loopback daphne에 붙는다), `--keep-logs <out/ 아래 폴더>`.
- 준비: 소스 checkout과 `requirements/web.txt`가 설치된 venv, Mongo 레플리카셋 접속값 `PALDO_MONGO_URI`(DB 이름 포함). 시험은 `b0705_<임의값>_` 접두어 컬렉션만 만들고 끝나면 지운다. 운영 데이터와 섞지 않으려면 DB 이름을 시험용(예: `/paldo_bench`)으로 바꾼 URI를 쓴다. 계정은 임시 SQLite에 만들므로 MySQL은 쓰지 않는다.
- 시간: 경우당 약 3분 30초(기동·상태 확인 포함). 2개·1개 비교는 약 8분.
- 실행 예:
  ```sh
  cd <소스>
  PALDO_MONGO_URI='<보호 접속값, DB 이름은 시험용>' <venv>/bin/python tools/perf/api_bench.py --compare-asgi --users 30 --minutes 3 --web-processes 2,1 --front binary --caddy-bin "$(command -v caddy)" --result-name server_2p_1p --result-task C-08-04
  # Redis 비용까지 볼 때(선택 절 Redis를 띄운 뒤)
  PALDO_MONGO_URI='...' <venv>/bin/python tools/perf/api_bench.py --web-processes 2 --channel-layer redis --redis-url redis://127.0.0.1:6379/15 --front binary
  ```
- 결과: 소스의 `out/handoff/e0910-multiproc-report.json`(`rows`마다 `processes`·`p50_ms`·`p95_ms`·`p99_ms`·`errors`·`beacon_errors`·`per_process_commits`). 화면에도 경우마다 한 줄 JSON이 나온다. 2개에서 `per_process_commits`가 두 포트로 나뉘어야 분배가 된 것이다.
- 해석: app-01은 2 vCPU라 프로세스를 늘려도 최대 약 2배다. 부하 생성기도 같은 VM에서 돌면 CPU를 나눠 쓰므로 수치는 경우끼리 비교만 한다.

### 선택: Redis 채널 레이어 (Redis를 켤 때만)

서버가 다른 프로세스의 웹소켓 연결로 메시지를 보내는 기능(예: 서버 쪽 판 상태 전송)이 생기면 켠다. 지금 기본 구성에서는 하지 않는다.

1. app-01 같은 VM 안에 Redis를 설치하고 loopback에만 연다(밖으로 열지 않음). 저장은 끈다.
   ```sh
   sudo apt-get install -y redis-server
   # /etc/redis/redis.conf: bind 127.0.0.1 -::1, protected-mode yes, save "", appendonly no, maxmemory 64mb
   sudo systemctl enable --now redis-server
   redis-cli -h 127.0.0.1 ping    # PONG
   ```
2. 웹 프로필 `environment`에 `PALDO_CHANNEL_LAYER=redis`, `PALDO_REDIS_URL=redis://127.0.0.1:6379/0`를 넣고 `sudo systemctl restart paldo-web@8000 paldo-web@8002`. venv에 `requirements/web.txt`(channels_redis 4.3.0, redis 7.4.1. redis 8.1.0은 웹소켓이 끊겨 쓰지 않는다)가 설치돼 있어야 한다.
3. 확인: 두 프로세스 `/healthz` 200, `redis-cli info clients`의 `connected_clients`가 웹 프로세스 수만큼 늘어남(channels_redis는 프로세스마다 수신 연결 하나를 나눠 쓴다. 30명 측정에서 1→3). Redis에 연결할 수 없으면 웹이 기동 때 "Redis 채널 레이어에 연결할 수 없습니다"로 멈춘다(InMemory로 떨어지지 않음).
4. 되돌림: `PALDO_CHANNEL_LAYER=memory`로 바꾸고(또는 줄 삭제) 웹을 다시 시작한다. Redis는 `sudo systemctl disable --now redis-server`.

## 참가자에게 전달할 문구

지원 브라우저는 최신 Chrome·Edge다. 담당자가 전달한 **정확한 HTTPS 주소**로 접속한다. 처음에 인증서 경고가 뜨면 주소와 아래 지문을 담당자가 전달한 값과 대조한 뒤 진행한다. 다른 사이트 경고에는 이 안내를 적용하지 않는다.

- Chrome: ‘연결이 비공개로 설정되어 있지 않습니다’ → **고급** → **[접속 주소](안전하지 않음)(으)로 이동**. 영문은 ‘Advanced’ → ‘Proceed to [address] (unsafe)’다.
- Edge: ‘연결이 비공개로 설정되어 있지 않습니다’ → **고급** → **[접속 주소](안전하지 않음)(으)로 계속**. 브라우저 버전·언어에 따라 문구가 조금 다르다.
- 접속 후 가입·로그인하고 배정된 판을 시작한다. 운영자는 개발자 도구 Network에서 `/ws/game/`의 **101**, wss, 혼합 콘텐츠 없음, 광고 내부 주소 요청 없음, sessionid/csrftoken의 Secure 속성을 확인한다.

관리 정책으로 ‘계속’이 없으면 정책을 우회하거나 보안을 전역 해제하지 않는다. 담당자가 안전한 경로로 전달한 **Caddy 내부 루트 공개 인증서**를 아래 지문과 대조하고, 허용된 **그 기기의 현재 사용자 신뢰 루트 저장소에만** 설치하는 방식을 사용한다. Windows는 인증서 관리자(`certmgr.msc`)의 현재 사용자 → 신뢰할 수 있는 루트 인증 기관 → 인증서 → 가져오기다. 테스트 후 같은 지문의 인증서만 제거한다. 관리 기기에서 가져오기도 막히면 관리자에게 요청한다. 개인 키·서버 계정·브라우저 쿠키는 전달하지 않는다.

## 인증서 대조

루트 CA SHA-256(2026-11-08 실측):

```text
9A:4A:7D:73:B7:AF:25:50:2D:8C:E6:AE:27:99:AF:D4:4B:3D:47:B2:3E:08:A4:A5:60:11:7A:2A:D7:42:33:93
```

당시 서버 leaf SHA-256:

```text
35:2C:9B:5A:0D:7E:43:FD:C6:FB:C3:A1:A0:A0:BB:72:B9:75:E1:81:62:83:8F:34:F5:77:A2:9C:72:8A:BA:94
```

leaf 만료는 `Nov 8 17:44:20 2026 GMT`였고 Caddy가 갱신하면 leaf 지문도 바뀐다. 참가자 안내 직전에 운영자가 `openssl x509 -in <공개 인증서> -noout -sha256 -fingerprint -dates -ext subjectAltName`으로 다시 읽는다. 루트도 재생성하면 바뀌므로 기존 지문을 무조건 신뢰하지 않는다. 실제 주소·인증서 파일·개인 키는 저장소에 넣지 않는다. CA 비교와 SAN 검증은 같은 개념이 아니므로 둘 다 확인한다.

## 실제 검증과 재현

`browser_check.py --origin ... --directory ... --phase register`는 **새 합성 계정**을 실제 가입·로그인으로 생성하고 같은 호스트의 0600 파일에만 상태를 저장한다. 운영자가 그 참가자에게 테스트 회차를 배정한 뒤 `--phase game`으로 판 시작·wss·쿠키·브라우저 오류를 확인한다. 이때 기본 TLS 예외는 자동 검증 컨텍스트에만 적용한다. `--certificate-interstitial`은 예외를 끄고 실제 Chrome 경고 UI의 Advanced/Proceed를 누른다. 이미 시작한 판은 이어 열어 새 이벤트를 만들지 않는다. 11/8 서버와 다른 물리 노트북의 Chrome 157에서 경고 UI → wss 101까지 확인했다. Edge 자체 실측은 수행하지 않았다. OS 신뢰 저장소도 자동 수정하지 않았다.

```sh
python ops/proxy/header_probe.py --template ops/proxy/Caddyfile
python -m tools.perf.api_bench --proxy-profile "$PRIVATE_ACTORS" --seconds 60 --out "$PRIVATE_BENCH_RESULT"
```

header_probe는 같은 Caddyfile을 임시 loopback·고포트·별도 CA로 실행해 위조 XFF/XFP가 교체되는지 확인한다. 종료 시 임시 프로세스와 파일을 정리한다. 실제 public route나 운영 인증정보를 시험 backend로 보내지 않는다.

api_bench의 proxy 모드는 **Django TestClient가 아닌 실제 HTTP 요청**이다. 웹 호스트에 이미 만든 합성 세션으로 완료된 판을 직접 HTTP/HTTPS 각각 60초·초당 1회 조회하고 상태·본문 SHA-256 일치를 확인한다. 쿠키는 다른 호스트에 복사하지 않는다. 실측 직접 p50/p95=68.669/77.156ms, HTTPS=68.909/76.316ms, 각 60회 오류 0이다. 한 번의 작은 표본으로 성능 개선을 주장하지 않는다. 자세한 결과는 [C-07-04](../../docs/evidence/C-07-04.md)와 결과 JSON을 따른다.

## 운영 인계 범위

웹·광고·Outbox·광고 publish/expire/refund·공식 bronze를 동시에 실행했고 앱 6개 Python 프로세스는 모두 3.14.4, HTTP 지표 6개 엔드포인트는 모두 200이었다. Spark driver/executor 3.14.4도 별도 작업에서 확인했다. Mongo 콘텐츠는 `content` 컬렉션의 `content-1.0`을 실제 읽었다. 이후 C-06-01 후속 작업에서 기존 관리자 보호 파일로 정상 인증해 Mongo 전용 DB·계정으로 전환했고, 전환 후 wss와 새 게임 52건 Kafka/Delta 일치도 확인했다(ops/mongo.md). B의 실제 HTTP 광고 어댑터, C-08-01 중앙 Prometheus 대시보드·경보 및 C-08-02 광고 silver/rejudge/gold는 후속 태스크이며 이 프록시 검증으로 완료 처리하지 않는다. E의 INT 통합 판정도 E가 수행한다.
