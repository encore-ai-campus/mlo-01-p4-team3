"""시험용 계측의 요청 격리와 원문 비노출을 확인한다."""
import json
from django.db import connection
from django.http import HttpResponse
from django.test import TestCase, RequestFactory
from server.tests.asgi_bench_metrics import BenchTimingMiddleware, CURRENT, HEADER, timed


class BenchTiming(TestCase):
    """운영 응답에 연결되지 않는 시험 전용 래퍼를 검사한다."""

    def test_correlated_sql_and_stages_are_numeric_and_request_local(self):
        """같은 요청의 SQL·단계를 숫자로만 반환하고 다음 요청에 누적하지 않는다."""
        def response(request):
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
            timed(lambda: None, 'engine_apply')()
            return HttpResponse('ok')
        first = BenchTimingMiddleware(response)(RequestFactory().get('/'))
        data = json.loads(first[HEADER])
        self.assertEqual(set(data), {'account_sql', 'engine_apply', 'server_total'})
        self.assertTrue(all(isinstance(value, (int, float)) and value >= 0 for value in data.values()))
        self.assertNotIn('SELECT', first[HEADER])
        second = BenchTimingMiddleware(lambda request: HttpResponse('ok'))(RequestFactory().get('/'))
        self.assertEqual(set(json.loads(second[HEADER])), {'server_total'})
        self.assertIsNone(CURRENT.get())

    def test_exception_does_not_leak_request_context(self):
        """처리 예외 뒤에도 요청 계측 컨텍스트는 해제한다."""
        def fail(request):
            raise RuntimeError('failure')
        with self.assertRaises(RuntimeError):
            BenchTimingMiddleware(fail)(RequestFactory().get('/'))
        self.assertIsNone(CURRENT.get())
