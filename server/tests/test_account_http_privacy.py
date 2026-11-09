"""인증 전체 시나리오에서 별도 D Django 광고 API의 실제 HTTP 수신·로그 비노출 검사."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
from threading import Thread

from django.test import TestCase
from server.infra.ad_client import HTTPAdProvider
from server.tests import test_accounts as accounts


class AccountHTTPPrivacy(TestCase):
    """실제 D WSGI·API·서비스와 메모리 광고 저장소를 사용한다. VM/MySQL 광고 검증은 아니다."""

    test_credentials_absent_from_logs_outbox_and_metrics = accounts.Accounts.test_credentials_absent_from_logs_outbox_and_metrics

    def privacy_provider(self):
        """별도 프로세스의 D 광고 API를 시작하고 B HTTP 어댑터를 연결한다."""
        process = subprocess.Popen([sys.executable, '-B', '-m', 'server.tests.ad_privacy_host'],
            cwd=Path(__file__).resolve().parents[2], env=dict(os.environ, PYTHONIOENCODING='utf-8'),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        self.process = process
        self.replies = queue.Queue()
        def reader():
            for line in process.stdout:
                self.replies.put(line)
            self.replies.put(None)
        thread = Thread(target=reader, daemon=True)
        thread.start()
        def close():
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            thread.join(5)
            process.stdout.close()
        self.addCleanup(close)
        ready = self._reply()

        class RecordingHTTPProvider(HTTPAdProvider):
            """B 직렬화 전에 보낸 DTO를 D 수신과 대조할 때만 보관한다."""

            def __init__(self, origin):
                """시험 전용 내부 출처와 비영속 요청 목록을 준비한다."""
                super().__init__(origin)
                self.calls = []
            def request_ad(self, request, connect_timeout_ms, total_timeout_ms):
                """운영 어댑터로 성공한 HTTP 요청의 DTO를 기록한다."""
                result = super().request_ad(request, connect_timeout_ms, total_timeout_ms)
                self.calls.append(request)
                return result
        return RecordingHTTPProvider('http://127.0.0.1:' + str(ready['port']))

    def _reply(self):
        line = self.replies.get(timeout=30)
        if line is None:
            raise RuntimeError('isolated D host exited')
        return json.loads(line)

    def check_ad_http_privacy(self, provider, forbidden, identities):
        """수신 본문과 DTO 일치, 헤더 허용 목록, D 파일 로그의 인증 정보 검출 0건을 검사한다."""
        self.process.stdin.write(json.dumps({'op': 'snapshot'}) + '\n')
        self.process.stdin.flush()
        snapshot = self._reply()
        calls = snapshot['calls']
        self.assertEqual(len(calls), 4)
        self.assertGreater(snapshot['filled'], 0)
        for call, request in zip(calls, provider.calls):
            self.assertEqual(call['path'], '/ads/v1/serve')
            self.assertEqual(call['status'], 200)
            self.assertEqual(call['body'], json.loads(json.dumps(asdict(request))))
            headers = {k.lower(): v for k, v in call['headers'].items()}
            self.assertLessEqual(set(headers), {'host', 'content-length', 'content-type', 'x-request-id'})
            self.assertEqual(headers['x-request-id'], request.request_id)
        wire = json.dumps(calls)
        self.assertTrue(snapshot['logs'])
        for output in (wire, snapshot['logs']):
            self.assertFalse(any(value in output for value in (*forbidden, *identities,
                'forged-player', 'forged-game')), '인증 정보 또는 게임 식별자가 D 수신/로그에 노출되었습니다.')
        records = [json.loads(line) for line in snapshot['logs'].splitlines()]
        self.assertEqual({r['request_id'] for r in records}, {r.request_id for r in provider.calls})
        self.assertTrue(all(r['route'] == 'ads/v1/serve' and r['status'] == 200 for r in records))
        print('B-06-04 HTTP privacy: received=4, status_200=4, '
              f'filled={snapshot["filled"]}, identities=1, forbidden_matches=0', flush=True)
