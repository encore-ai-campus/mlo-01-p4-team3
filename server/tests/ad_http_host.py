"""B 전용 loopback HTTP 대역. 운영·D 광고 서버의 완료 증빙에는 사용하지 않는다."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Event, Thread
from urllib.parse import urlsplit


class LocalAdHost:
    """메모리에 요청을 모으고 정상·오류·무응답·3초 지연을 제어한다."""

    def __init__(self):
        """임의 loopback 포트만 사용한다. 계정·DB·파일 로그를 만들지 않는다."""
        self.mode, self.calls, self.stop = 'normal', [], Event()
        self.response_override = None
        self.token = secrets.token_urlsafe(24)
        self.location = '/ads/landing/sample'
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.do_POST()

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                host.calls.append(dict(path=self.path, method=self.command, headers=dict(self.headers),
                                       body=json.loads(body) if body else None))
                mode = host.mode
                if mode in {'delay', 'unresponsive'}:
                    host.stop.wait(3 if mode == 'delay' else 30)
                if host.stop.is_set():
                    return
                status, content_type, payload = 200, 'application/json', b''
                path = urlsplit(self.path).path
                if path == '/ads/v1/serve':
                    request = json.loads(body)
                    width, height = (300, 250) if request['slot'] == 'review_side' else (728, 90)
                    payload = json.dumps(dict(request_id=request['request_id'], filled=True,
                        creative=dict(creative_id='sample', title='가상 광고', body='HTTP 시험 문안',
                                      image_path=f'/static/ads/sample__{width}x{height}.png', label='광고'),
                        serve_token=host.token, beacon_path='/ads/v1/impression',
                        click_path='/ads/v1/click?t=' + host.token)).encode()
                elif path == '/ads/v1/impression':
                    status = 204
                elif path == '/ads/v1/click':
                    status = 302
                elif path.startswith('/ads/landing/'):
                    content_type, payload = 'text/html', '<main>가상 랜딩</main>'.encode()
                else:
                    status = 404
                if mode == 'error':
                    status = 503
                if host.response_override is not None:
                    status, content_type, payload = host.response_override
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(payload)))
                    if status == 302:
                        self.send_header('Location', host.location)
                    self.end_headers()
                    if mode == 'trickle':
                        for value in payload:
                            self.wfile.write(bytes([value]))
                            self.wfile.flush()
                            if host.stop.wait(.03):
                                break
                    else:
                        self.wfile.write(payload)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread.start()

    def close(self):
        """자기 listener·요청 대기·스레드만 정리한다."""
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
