"""시험 프로세스 전용 단계 계측. 숫자만 기록하고 운영 설정에서는 연결하지 않는다."""
from asyncio import CancelledError
from contextvars import ContextVar
from functools import wraps
import json
from logging import getLogger
import re
from time import monotonic, perf_counter, time
from django.db import connection
from prometheus_client import Histogram

STAGES = Histogram('paldo_bench_stage_seconds', 'Benchmark-only dependency latency', ['stage'])
CURRENT = ContextVar('paldo_bench_request', default=None)
HEADER = 'X-Paldo-Bench-Timing'
VOTE_CALLS = ContextVar('paldo_bench_vote_calls', default=None)
FALLBACKS = ContextVar('paldo_bench_fallbacks', default=None)
MODEL_TRACE = ContextVar('paldo_bench_model_trace', default=None)
MODEL_TRACES = ContextVar('paldo_bench_model_traces', default=None)
LOGGER = getLogger('paldo.web')


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


def observe_calls(estimate):
    """Correlate model port returns with the engine's decision for the same snapshot."""
    @wraps(estimate)
    def call(obs):
        stages = CURRENT.get()
        calls = VOTE_CALLS.get()
        if stages is not None:
            stages['npc_inference_calls'] = stages.get('npc_inference_calls', 0) + 1
        result = 'failed'
        trace = MODEL_TRACE.get()
        if trace is not None:
            trace['model_started'] = monotonic()
        try:
            value = estimate(obs)
            result = 'ok'
            return value
        finally:
            if trace is not None:
                trace.update(model_ended=monotonic(), model_call=result)
            if calls is not None:
                calls[obs.snapshot_id] = result
    return call


def record_wait(started, ended):
    """어댑터가 실제 잰 잠금 대기 구간을 같은 NPC 호출에 연결한다."""
    record('worker_lock_wait', ended - started)
    trace = MODEL_TRACE.get()
    if trace is not None:
        trace.update(wait_started=started, wait_ended=ended)


def observe_engine_estimate(estimate):
    """시험에서 엔진에 전달하는 시계의 첫 값을 관측하고 원래 판정을 그대로 반환한다."""
    from engine.domain.npc.vote import SUSPICION_TIMEOUT_S

    @wraps(estimate)
    def call(model, obs, clock=monotonic, timeout_s=SUSPICION_TIMEOUT_S):
        trace = dict(snapshot_id=obs.snapshot_id, round=obs.phase.value,
                     wait_started=None, wait_ended=None, engine_result='exception')
        token = MODEL_TRACE.set(trace)
        def observed_clock():
            tick = clock()
            if 'engine_started' not in trace:
                trace.update(engine_started=tick, engine_deadline=tick + timeout_s)
            return tick
        try:
            value, reason = estimate(model, obs, observed_clock, timeout_s)
            trace['engine_result'] = reason.value if reason is not None else 'ok'
            return value, reason
        finally:
            traces = MODEL_TRACES.get()
            if traces is not None:
                traces.append(trace)
            MODEL_TRACE.reset(token)
    return call


def observe_fallbacks(apply):
    """Keep vote round, engine reason and matching model return for each fallback."""
    from engine.domain.npc.vote import is_fallback_version

    @wraps(apply)
    def call(*args, **kwargs):
        calls = {}
        token = VOTE_CALLS.set(calls)
        try:
            transition = apply(*args, **kwargs)
            fallbacks = FALLBACKS.get()
            if fallbacks is not None:
                for event in transition.events:
                    payload = event.payload
                    if event.event_type.value == 'decision' and is_fallback_version(payload['policy_version']):
                        fallbacks.append(dict(
                            round=event.phase.value,
                            reason=next(code for code in payload['reason_codes'] if code.startswith('model_fallback_')),
                            model_call=calls.get(payload['snapshot_id'], 'not_called'),
                        ))
            return transition
        finally:
            VOTE_CALLS.reset(token)
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
        fallbacks = []
        fallback_token = FALLBACKS.set(fallbacks)
        traces = []
        trace_token = MODEL_TRACES.set(traces)
        started_at, started_monotonic = time(), monotonic()
        bench_id = request.headers.get('X-Paldo-Bench-Request-ID', '')
        bench_id = bench_id if re.fullmatch(r'[0-9a-f]{32}', bench_id) else None
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
            if traces:
                LOGGER.info('bench_model_timeline', extra={
                    'component': 'bench',
                    'fields': dict(request_id=response.get('X-Request-ID'), bench_id=bench_id,
                                   request_started_at=started_at, request_started_monotonic=started_monotonic,
                                   npc_inference_calls=stages.get('npc_inference_calls', 0), calls=traces),
                })
            if fallbacks:
                LOGGER.info('bench_vote_fallbacks', extra={
                    'component': 'bench',
                    'fields': dict(request_id=response.get('X-Request-ID'),
                                   worker_lock_wait_ms=round(stages.get('worker_lock_wait', 0), 3),
                                   npc_inference_calls=stages.get('npc_inference_calls', 0),
                                   fallbacks=fallbacks),
                })
            response[HEADER] = json.dumps({key: round(value, 3) for key, value in stages.items()}, separators=(',', ':'))
            return response
        except CancelledError:
            LOGGER.warning('bench_request_aborted', extra={
                'component': 'bench',
                'fields': dict(bench_id=bench_id,
                               request_id=getattr(request, 'request_id', None),
                               error_type='CancelledError',
                               duration_ms=round((perf_counter() - started) * 1000, 3),
                               stages={key: round(value, 3) for key, value in stages.items()}),
            })
            raise
        finally:
            MODEL_TRACES.reset(trace_token)
            FALLBACKS.reset(fallback_token)
            CURRENT.reset(token)
