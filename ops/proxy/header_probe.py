"""동일 Caddy 설정을 loopback에서 띄워 전달 헤더 위조 방지 규칙을 검증한다."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request


class Echo(BaseHTTPRequestHandler):
    """시험 전용 loopback backend. 쿠키·외부 요청은 사용하지 않는다."""
    def do_GET(self):
        """프록시가 전달한 두 헤더만 반환한다."""
        body=json.dumps({'xff':self.headers.get('X-Forwarded-For'),'xfp':self.headers.get('X-Forwarded-Proto')}).encode()
        self.send_response(200);self.end_headers();self.wfile.write(body)

    def log_message(self,*args):
        """시험 URL을 로그에 중복 기록하지 않는다."""


def main(argv=None):
    """별도 임시 CA와 높은 포트를 쓰고 종료 시 프로세스·파일을 정리한다."""
    parser=argparse.ArgumentParser();parser.add_argument('--template',required=True);args=parser.parse_args(argv)
    backend=ThreadingHTTPServer(('127.0.0.1',0),Echo)
    threading.Thread(target=backend.serve_forever,daemon=True).start()
    process=None
    try:
        with tempfile.TemporaryDirectory(prefix='paldo-proxy-probe-') as directory:
            root=Path(directory)
            template=Path(args.template).read_text().replace('127.0.0.1:2019','127.0.0.1:2021')
            template=template.replace('http://{$PALDO_PROXY_HOST}', 'http://{$PALDO_PROXY_HOST}:18445')
            template=template.replace('https://{$PALDO_PROXY_HOST}', 'https://{$PALDO_PROXY_HOST}:18444')
            template=template.replace('127.0.0.1:8000','127.0.0.1:'+str(backend.server_port))
            config=root/'Caddyfile';config.write_text(template)
            env=dict(os.environ,PALDO_PROXY_HOST='127.0.0.1',PALDO_STATIC_ROOT=directory,
                     XDG_DATA_HOME=str(root/'data'),XDG_CONFIG_HOME=str(root/'config'),HOME=directory)
            with (root/'caddy.log').open('w') as output:
                process=subprocess.Popen(['caddy','run','--config',str(config),'--adapter','caddyfile'],env=env,stdout=output,stderr=subprocess.STDOUT)
                ca=root/'data/caddy/pki/authorities/local/root.crt'
                result=None
                for _ in range(80):
                    if process.poll() is not None:raise RuntimeError('probe_caddy_start_failed')
                    if ca.exists():
                        try:
                            ctx=ssl.create_default_context(cafile=str(ca))
                            request=urllib.request.Request('https://127.0.0.1:18444/probe',headers={'X-Forwarded-For':'203.0.113.9','X-Forwarded-Proto':'http'})
                            with urllib.request.urlopen(request,context=ctx,timeout=2) as response:
                                result=json.load(response);result['hsts_absent']=response.headers.get('Strict-Transport-Security') is None
                            break
                        except OSError:pass
                    time.sleep(.1)
                if result is None:raise RuntimeError('probe_not_ready')
                result['ok']=result['xff']=='127.0.0.1' and result['xfp']=='https' and result['hsts_absent']
                print(json.dumps(result))
                if not result['ok']:raise SystemExit(1)
    finally:
        if process is not None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        backend.shutdown();backend.server_close()


if __name__=='__main__':main()
