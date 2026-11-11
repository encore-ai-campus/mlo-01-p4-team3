"""B-07-05·B-08-03 시험 조립. 실제 Mongo·녹화 엔진과 가짜/HTTP 광고를 분리한다."""
import atexit
from dataclasses import replace
import os
import re

if not re.fullmatch(r"b0705_[0-9a-f]{32}_", os.environ.get("PALDO_BENCH_PREFIX", "")):
    raise RuntimeError("benchmark_process_only")

import django
django.setup()
from server.tests.api_bench import fixture_service
from server.paldo_web.mongo import open_mongo_runtime
from server.paldo_web.composition import configure_game_service, configure_session_service, configure_ad_gateway
from server.app.ads_port import AdGateway
from engine.testing.fakes import ScriptedGameEngine
from ads.testing.fakes import FakeAdProvider

from server.tests.asgi_bench_metrics import observe_calls, observe_engine_estimate, observe_fallbacks, record_wait, timed

model_version = os.environ.get("PALDO_BENCH_SUSPICION_VERSION")
actual = bool(model_version) or os.environ.get("PALDO_BENCH_ACTUAL_BASELINE") == "1"
if model_version:
    from ml.suspicion.features import FEATURE_VERSION
    from server.infra.model_selector import LocalModelSelector
    from server.infra.observed_models import ObservedSuspicionModel
    from ml.adapters.suspicion_model import SharedModelStore

    selector = LocalModelSelector(os.environ["PALDO_BENCH_MLRUNS"], suspicion_version=model_version,
                                  intent_version="", feature_version=FEATURE_VERSION)
    model_store = SharedModelStore()
    atexit.register(model_store.close)
    def load_primary():
        adapter = selector.suspicion_model(model_version)
        adapter.wait_observer = record_wait
        return adapter

    adapter = model_store.get(model_version, load_primary)
    if secondary_version := os.environ.get('PALDO_BENCH_SECONDARY_SUSPICION_VERSION'):
        model_store.get(secondary_version, lambda: selector.suspicion_model(secondary_version))

    from prometheus_client import REGISTRY
    from prometheus_client.core import GaugeMetricFamily
    from server.tests.multiproc_bench import resident_bytes

    class WorkerMemory:
        """등록된 worker별 Linux RSS. 웹 RSS는 기본 process collector가 제공한다."""

        def collect(self):
            """종료되거나 측정할 수 없는 PID를 0바이트로 위장하지 않는다."""
            gauge = GaugeMetricFamily('paldo_suspicion_worker_resident_memory_bytes',
                                      'Current worker RSS including shared pages', labels=['model_version'])
            for version, pid in model_store.worker_pids().items():
                if (rss := resident_bytes(pid)) is not None:
                    gauge.add_metric([version], rss)
            yield gauge

    REGISTRY.register(WorkerMemory())
    suspicion = ObservedSuspicionModel(adapter)
    suspicion.estimate = observe_calls(suspicion.estimate)
else:
    suspicion = None
if actual:
    from server.infra.game_factory import ContentPolicyEngine

    engine = ContentPolicyEngine(model_version=model_version or 'off')
    if model_version:
        engine.apply = observe_fallbacks(engine.apply)
        from engine.domain.npc import vote
        vote.estimate = observe_engine_estimate(vote.estimate)
else:
    engine = ScriptedGameEngine()
runtime = open_mongo_runtime(engine=engine, suspicion=suspicion,
                             uri=os.environ["PALDO_MONGO_URI"], collection_prefix=os.environ["PALDO_BENCH_PREFIX"])
if actual:
    game = runtime.game
    game.load_test = True
else:
    game = fixture_service(runtime)
if model_version:
    prepare_start = game.prepare_start

    def prepare_model_game(data, player):
        """시험 판의 이벤트에 이 프로세스가 읽은 모델 버전을 기록한다."""
        return replace(prepare_start(data, player), model_versions={"suspicion": model_version})

    game.prepare_start = prepare_model_game
if os.environ.get('PALDO_BENCH_INTENT_FACTORY'):
    from server.app.interactions import InteractionService
    from server.infra.interaction_store import MongoInteractionStore
    from django.utils.module_loading import import_string
    from server.paldo_web.intent import ObservedIntentParser

    parser = ObservedIntentParser(import_string(os.environ['PALDO_BENCH_INTENT_FACTORY'])())
    parser.parse = timed(parser.parse, 'intent_parse')
    store = MongoInteractionStore(runtime.database[os.environ['PALDO_BENCH_PREFIX'] + 'b_interactions'])
    game.interactions = InteractionService(game, store, parser=parser)
configure_game_service(game)
configure_session_service(runtime.sessions)


for name in ('load', 'find_command', 'commit', 'create'):
    setattr(game.repository, name, timed(getattr(game.repository, name), name))
game.dependencies = timed(game.dependencies, 'content')
for name in ('load', 'add_game'):
    setattr(game.progress, name, timed(getattr(game.progress, name), 'progress_' + name))
if game.find_open_games is not None:
    game.find_open_games = timed(game.find_open_games, 'find_open_games')
for name in ('new_game', 'apply', 'player_view'):
    setattr(game.engine, name, timed(getattr(game.engine, name), 'engine_' + name))
from server.api import views
from server.app import usecases
views.to_jsonable = timed(views.to_jsonable, 'response_conversion')
views.JsonResponse = timed(views.JsonResponse, 'json_response')
usecases.dumps = timed(usecases.dumps, 'stored_response_conversion')
runtime.sessions.store.ad_identity = timed(runtime.sessions.store.ad_identity, 'ad_identity')
scenario = os.environ["PALDO_BENCH_SCENARIO"]
if internal_url := os.environ.get('PALDO_BENCH_HTTP_ADS_URL'):
    from server.infra.ad_client import HTTPAdProvider

    class LoadTestHTTPAdProvider(HTTPAdProvider):
        """부하 시험에서 발급하는 광고 토큰은 실제 과금·학습에서 제외한다."""

        def request_ad(self, req, connect_timeout_ms, total_timeout_ms):
            """운영 어댑터는 그대로 사용하고 시험 요청의 load_test만 켠다."""
            return super().request_ad(replace(req, load_test=True), connect_timeout_ms, total_timeout_ms)

    gateway = AdGateway(LoadTestHTTPAdProvider(internal_url))
else:
    gateway = AdGateway(FakeAdProvider(latency_ms=3000 if scenario == "delay" else 0, fail=scenario == "failure"))
configure_ad_gateway(gateway)

if __name__ == "__main__":
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server
    from django.core.wsgi import get_wsgi_application

    class ThreadedServer(ThreadingMixIn, WSGIServer):
        """독립 HTTP 요청을 병렬 처리하는 시험용 WSGI 서버."""
        daemon_threads = True

    class QuietHandler(WSGIRequestHandler):
        """시험 로그에 쿠키·쿼리를 출력하지 않는다."""
        def log_message(self, format, *args):
            """접근 로그는 공통 Django 계측만 사용한다."""

    with make_server("127.0.0.1", int(os.environ["PALDO_BENCH_PORT"]), get_wsgi_application(),
                     server_class=ThreadedServer, handler_class=QuietHandler) as server:
        server.serve_forever()
else:
    from server.paldo_web.asgi import application  # noqa: F401
