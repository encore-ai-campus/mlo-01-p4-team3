"""B-06-04 전용 D Django 광고 API 호스트. 수신 원자료는 IPC 메모리에만 둔다."""
import io
import json
import logging
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from wsgiref.simple_server import make_server


def main():
    """임의 루프백 포트·D 원본 WSGI·메모리 광고 저장소를 부모 stdin 수명 동안 실행한다."""
    os.environ.update(DJANGO_SETTINGS_MODULE='ads.project.settings',
        PALDO_ADS_INTERNAL_URL='http://127.0.0.1', DJANGO_SECRET_KEY=secrets.token_hex(32),
        PALDO_ADS_TOKEN_KEY=secrets.token_hex(32))
    with TemporaryDirectory(prefix='paldo-ad-privacy-') as folder:
        os.environ['PALDO_LOG_DIR'] = folder
        from django.core.wsgi import get_wsgi_application
        application = get_wsgi_application()
        from ads.testing.traffic_server import _Host, _QuietHandler
        from ads.web import views
        host = _Host(application)
        from ads.domain.model import Targeting
        from tools.build_ads_fixtures import campaigns
        fixture_campaigns = campaigns()
        for campaign in fixture_campaigns:
            campaign.targeting = Targeting()
        host.service.campaigns = lambda: fixture_campaigns
        views.get_service = lambda: host.service
        logger = logging.getLogger('paldo.ads')
        logger.handlers[:] = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        calls = []

        class Handler(_QuietHandler):
            """네트워크에서 실제 받은 헤더만 WSGI 환경과 구분한다."""

            def get_environ(self):
                """OS 환경 변수의 HTTP_ 항목을 수신 헤더로 오인하지 않는다."""
                environ = super().get_environ()
                environ['paldo.wire_headers'] = dict(self.headers)
                return environ

        def capture(environ, start_response):
            size = int(environ.get('CONTENT_LENGTH') or 0)
            raw = environ['wsgi.input'].read(size)
            environ['wsgi.input'] = io.BytesIO(raw)
            call = dict(path=environ['PATH_INFO'], body=json.loads(raw) if raw else None,
                headers=environ['paldo.wire_headers'])
            def started(status, headers, exc_info=None):
                call['status'] = int(status.split()[0])
                return start_response(status, headers, exc_info)
            result = list(host(environ, started))
            calls.append(call)
            return result

        with make_server('127.0.0.1', 0, capture, handler_class=Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(json.dumps({'port': server.server_port}), flush=True)
            try:
                for line in sys.stdin:
                    command = json.loads(line)
                    if command != {'op': 'snapshot'}:
                        raise ValueError('invalid control command')
                    for handler in logger.handlers:
                        handler.flush()
                    logs = '\n'.join(p.read_text(encoding='utf-8') for p in Path(folder).glob('*.jsonl'))
                    print(json.dumps({'calls': calls, 'logs': logs,
                        'filled': sum(bool(r.filled) for r in host.service.responses.values())}), flush=True)
            finally:
                server.shutdown()
                thread.join(5)
                for handler in logger.handlers:
                    handler.close()


if __name__ == '__main__':
    main()
