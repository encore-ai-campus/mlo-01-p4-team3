"""시험 프로세스 전용 단계 계측. 숫자만 기록하고 운영 설정에서는 연결하지 않는다."""
from contextvars import ContextVar
from functools import wraps
import json
from time import perf_counter
from django.db import connection
from prometheus_client import Histogram

STAGES = Histogram('paldo_bench_stage_seconds', 'Benchmark-only dependency latency', ['stage'])
CURRENT = ContextVar('paldo_bench_request', default=None)
HEADER = 'X-Paldo-Bench-Timing'


def record(stage, seconds):
    """고정 단계 이름과 시간만 집계한다. 중첩 단계는 서로 합산하지 않는다."""
    STAGES.labels(stage).observe(seconds)
    current = CURRENT.get()
    if current is not None:
        current[stage] = current.get(stage, 0) + seconds * 1000


def timed(operation, stage):
    """시험에서 주입한 동기 포트·변환 함수의 지연을 기록한다."""
    @wraps(operation)
    def call(*args, **kwargs):
        started = perf_counter()
        try:
            return operation(*args, **kwargs)
        finally:
            record(stage, perf_counter() - started)
    return call


class BenchTimingMiddleware:
    """같은 요청의 서버 시간·계정 SQL·단계를 숫자 헤더로 연결하는 시험 전용 미들웨어."""

    def __init__(self, get_response):
        """요청 처리기를 보관한다."""
        self.get_response = get_response

    def __call__(self, request):
        """스레드별 DB 래퍼를 요청에만 적용하며 SQL·인자·쿠키는 읽거나 기록하지 않는다."""
        stages = {}
        token = CURRENT.set(stages)
        started = perf_counter()
        def sql(execute, sql, params, many, context):
            tick = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                record('account_sql', perf_counter() - tick)
        try:
            with connection.execute_wrapper(sql):
                response = self.get_response(request)
            stages['server_total'] = (perf_counter() - started) * 1000
            response[HEADER] = json.dumps({key: round(value, 3) for key, value in stages.items()}, separators=(',', ':'))
            return response
        finally:
            CURRENT.reset(token)
