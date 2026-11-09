"""중앙 수집기만 loopback 지표를 읽도록 제한하는 고정 경로 HTTP 중계."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit


def validate(config):
    """사설 bind·명시 클라이언트·loopback upstream만 허용한다."""
    if not ipaddress.ip_address(config['bind']).is_private:raise ValueError('private_bind_required')
    if not config['allowed_clients']:raise ValueError('explicit_clients_required')
    for value in config['allowed_clients']:ipaddress.ip_address(value)
    for path,url in config['upstreams'].items():
        target=urlsplit(url)
        if not path.startswith('/') or '?' in path or target.scheme!='http' or target.hostname not in ('127.0.0.1','::1',config['bind']) or target.username or target.password:
            raise ValueError('loopback_fixed_upstream_required')
    return config


def serve(config):
    """쿠키·임의 URL을 받지 않고 읽기 전용 지표만 제한된 크기로 반환한다."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        """loopback 밖으로의 리다이렉트를 따르지 않는다."""
        def redirect_request(self,*args,**kwargs):
            """리다이렉트를 거절해 upstream 변경을 오류로 드러낸다."""
            return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            """허용된 수집기와 경로를 검사한 뒤 새 내부 요청을 만든다."""
            if self.client_address[0] not in config['allowed_clients']:
                self.send_error(403);return
            if self.path not in config['upstreams']:
                self.send_error(404);return
            request=urllib.request.Request(config['upstreams'][self.path],headers={'Host':config.get('host_headers',{}).get(self.path,config['host_header']),'X-Forwarded-Proto':'https'})
            try:
                with opener.open(request,timeout=4) as response:
                    body=response.read(2*1024*1024+1)
                    if response.status!=200 or len(body)>2*1024*1024:raise ValueError('upstream_response')
                self.send_response(200);self.send_header('Content-Type','text/plain; version=0.0.4');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except Exception:self.send_error(503,'metrics upstream unavailable')

        def log_message(self,*args):
            """주소나 요청 헤더를 중복 기록하지 않는다."""

    ThreadingHTTPServer((config['bind'],config.get('port',19108)),Handler).serve_forever()


def main(argv=None):
    """운영 주소는 저장소 밖 보호 JSON에서 읽는다."""
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args(argv)
    serve(validate(json.loads(args.config.read_text())))


if __name__=='__main__':main()
