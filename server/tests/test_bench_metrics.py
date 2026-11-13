"""시험용 계측의 요청 격리와 원문 비노출을 확인한다."""
import json
from django.db import connection
from django.http import HttpResponse
from django.test import TestCase, RequestFactory
from server.tests.asgi_bench_metrics import BenchTimingMiddleware, CURRENT, HEADER, timed


class BenchTiming(TestCase):
    """운영 응답에 연결되지 않는 시험 전용 래퍼를 검사한다."""

    def test_npc_timeline_uses_the_actual_engine_clock_and_keeps_successful_late_returns(self):
        """실제 엔진 마감과 늦은 성공 응답을 같은 NPC 타임라인에 남긴다."""
        from types import SimpleNamespace
        from engine.domain.model import Phase
        from engine.domain.npc import vote
        from server.tests import asgi_bench_metrics as metrics

        self.assertTrue(callable(getattr(metrics, 'observe_engine_estimate', None)))
        observation = SimpleNamespace(snapshot_id='snapshot', phase=Phase.RUNOFF)
        traces = []
        trace_token = metrics.MODEL_TRACES.set(traces)
        stage_token = CURRENT.set({})
        def infer(obs):
            metrics.record_wait(100.004, 100.008)
            return object()
        model = SimpleNamespace(estimate=metrics.observe_calls(infer))
        ticks = iter([100, 100.11])
        try:
            result, reason = metrics.observe_engine_estimate(vote.estimate)(model, observation, lambda: next(ticks))
            self.assertIsNone(result)
            self.assertIs(reason, vote.FallbackReason.TIMEOUT)
            trace, = traces
            self.assertEqual(trace['round'], 'runoff')
            self.assertEqual(trace['engine_started'], 100)
            self.assertEqual(trace['engine_deadline'], 100.1)
            self.assertEqual(trace['wait_started'], 100.004)
            self.assertEqual(trace['wait_ended'], 100.008)
            self.assertLessEqual(trace['model_started'], trace['model_ended'])
            self.assertEqual(trace['model_call'], 'ok')
            self.assertEqual(trace['engine_result'], 'timeout')
        finally:
            metrics.MODEL_TRACES.reset(trace_token)
            CURRENT.reset(stage_token)

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

    def test_cancelled_request_keeps_safe_ids_and_partial_timings(self):
        """취소된 요청도 안전한 식별자와 취소 전 계측만 남긴다."""
        from asyncio import CancelledError
        from server.tests.asgi_bench_metrics import FALLBACKS, record

        for supplied, expected in [('a'*32, 'a'*32), ('private body', None)]:
            request = RequestFactory().post('/games', data='private body', content_type='application/json',
                                            HTTP_X_PALDO_BENCH_REQUEST_ID=supplied,
                                            HTTP_COOKIE='sessionid=private cookie')
            def cancel(request):
                request.request_id = 'server-request-id'
                record('load', .123)
                raise CancelledError('private exception')
            with self.subTest(supplied=supplied):
                with self.assertLogs('paldo.web', level='WARNING') as logs:
                    with self.assertRaises(CancelledError):
                        BenchTimingMiddleware(cancel)(request)
                entry, = logs.records
                self.assertEqual(entry.getMessage(), 'bench_request_aborted')
                self.assertEqual(entry.fields['bench_id'], expected)
                self.assertEqual(entry.fields['request_id'], 'server-request-id')
                self.assertEqual(entry.fields['error_type'], 'CancelledError')
                self.assertEqual(entry.fields['stages'], {'load': 123})
                self.assertGreaterEqual(entry.fields['duration_ms'], 0)
                self.assertNotIn('private', str(entry.fields))
                self.assertIsNone(CURRENT.get())
                self.assertIsNone(FALLBACKS.get())


    def test_fallback_correlates_a_successful_model_return_with_engine_timeout(self):
        """포트 성공 뒤 엔진 마감 초과를 별도 폴백으로 대사한다."""
        from types import SimpleNamespace
        from engine.domain.events import DomainEvent, EventType
        from engine.domain.model import Phase
        from server.tests.asgi_bench_metrics import observe_calls, observe_fallbacks, FALLBACKS

        obs = SimpleNamespace(snapshot_id='snapshot')
        def apply():
            observe_calls(lambda obs: object())(obs)
            return SimpleNamespace(events=[DomainEvent(EventType.DECISION, 1, Phase.RUNOFF, {
                'snapshot_id': 'snapshot', 'policy_version': 'rule-fallback',
                'reason_codes': ['model_fallback_timeout'],
            })])
        events = []
        token = FALLBACKS.set(events)
        try:
            observe_fallbacks(apply)()
        finally:
            FALLBACKS.reset(token)
        self.assertEqual(events, [dict(round='runoff', reason='model_fallback_timeout', model_call='ok')])
