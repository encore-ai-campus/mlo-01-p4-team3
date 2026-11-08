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

웹은 Daphne **1개·127.0.0.1:8000**으로 실행한다. 웹의 `PALDO_PUBLIC_ORIGIN`은 브라우저 HTTPS 출처와 같게 설정한다. Caddy만 내부 인터페이스의 80/443에 bind한다. XFF는 사용자 입력을 버리고 실제 remote_host로 덮어쓰고 XFP는 https로 덮어쓴다. 처음 bind를 좁히는 reload가 기존 wildcard HTTP/3 UDP listener와 충돌하면 검증한 설정으로 Caddy를 재시작한다. 방화벽 정책 변경은 별도 운영 범위이며 이번 검증에서 변경하지 않았다.

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

웹·광고·Outbox·광고 publish/expire/refund·공식 bronze를 동시에 실행했고 앱 6개 Python 프로세스는 모두 3.14.4, HTTP 지표 6개 엔드포인트는 모두 200이었다. Spark driver/executor 3.14.4도 별도 작업에서 확인했다. Mongo 콘텐츠는 `content` 컬렉션의 `content-1.0`을 실제 읽었다. 관리자 보관 위치가 없어 Mongo 전용 DB 분리는 아직 pilot 범위다. B의 실제 HTTP 광고 어댑터, C-08-01 중앙 Prometheus 대시보드·경보 및 C-08-02 광고 silver/rejudge/gold는 후속 태스크이며 이 프록시 검증으로 완료 처리하지 않는다. E의 INT 통합 판정도 E가 수행한다.
