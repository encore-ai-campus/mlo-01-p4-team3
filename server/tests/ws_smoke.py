"""실제 Daphne HTTP·인증 웹소켓을 임시 DB로 검사한다. 실행: python -m server.tests.ws_smoke."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

if os.environ.get("PALDO_WS_SMOKE_CHILD"):
    from server.paldo_web.asgi import application
    from server.paldo_web.composition import configure_ad_gateway
    from server.app.ads_port import AdGateway
    from ads.testing.fakes import FakeAdProvider
    configure_ad_gateway(AdGateway(FakeAdProvider()))


def _recv_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise RuntimeError("socket_closed")
        data += chunk
    return data


def _send(sock, message):
    data = json.dumps(message).encode()
    mask = secrets.token_bytes(4)
    length = bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack("!H", len(data))
    sock.sendall(bytes([0x81]) + length + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(data)))


def _receive(sock):
    opcode, length = _recv_exact(sock, 2)
    length &= 127
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    return opcode & 15, _recv_exact(sock, length)


def main():
    """임시 계정·Daphne를 만들고 HTTP와 인증 비콘·로그아웃 차단을 검사한다."""
    with tempfile.TemporaryDirectory(prefix="paldo-ws-") as folder:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        os.environ.update(DJANGO_SETTINGS_MODULE="server.tests.ws_smoke_settings",
                          PALDO_WS_SMOKE_DB=str(Path(folder) / "auth.sqlite3"),
                          PALDO_PUBLIC_ORIGIN=origin, DJANGO_SECRET_KEY=secrets.token_urlsafe(48))
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth.models import User
        from django.contrib.sessions.models import Session
        from django.test import Client
        from django.db import connections
        call_command("migrate", verbosity=0)
        user = User.objects.create_user(username="socket-pilot")
        client = Client()
        client.force_login(user)
        cookie = "sessionid=" + client.cookies["sessionid"].value
        env = dict(os.environ, PALDO_WS_SMOKE_CHILD="1")
        log = open(Path(folder) / "daphne.log", "w", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(port),
                                    "server.tests.ws_smoke:application"], env=env, stdout=log, stderr=log)
        try:
            for _ in range(100):
                try:
                    with urlopen(origin + "/accounts/login", timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError("Daphne failed to start")
                    time.sleep(.1)
            else:
                raise RuntimeError("Daphne startup timed out")
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                key = base64.b64encode(secrets.token_bytes(16)).decode()
                sock.sendall((f"GET /ws/game/ HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {origin}\r\n"
                              f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                              f"Sec-WebSocket-Key: {key}\r\nCookie: {cookie}\r\n\r\n").encode())
                header = b""
                while not header.endswith(b"\r\n\r\n"):
                    header += _recv_exact(sock, 1)
                assert header.startswith(b"HTTP/1.1 101"), header
                _send(sock, {"type": "hello", "dropped": 3,
                             "dropped_by_reason": {"queue_full": 2, "page_exit": 1}})
                from uuid import uuid4
                event = {"type": "ad.impression", "msg_id": str(uuid4()), "token": "fake-smoke",
                         "visible_ratio": .5, "visible_ms": 1000, "occurred_at_ms": 1000}
                _send(sock, event)
                opcode, body = _receive(sock)
                assert opcode == 1 and json.loads(body)["relayed"] is True
                with urlopen(origin + "/metrics", timeout=3) as response:
                    metrics = response.read().decode()
                assert 'paldo_ad_beacon_dropped_total{reason="queue_full"} 2.0' in metrics
                assert 'paldo_ad_beacon_dropped_total{reason="page_exit"} 1.0' in metrics
                assert "paldo_ad_beacon_reported_dropped_total" not in metrics
                Session.objects.all().delete()
                _send(sock, event)
                opcode, body = _receive(sock)
                assert opcode == 8 and struct.unpack("!H", body[:2])[0] == 4401
            print("Daphne smoke: HTTP 200, WS 101, relay ack true, reason metrics 2/1, revoked session 4401")
        finally:
            if sys.platform == "win32":
                # Windows venv redirector can leave its Python child alive.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                process.terminate()
            process.wait(timeout=10)
            log.close()
            connections.close_all()


if __name__ == "__main__":
    main()
