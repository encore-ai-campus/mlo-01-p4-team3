"""B-07-05·B-08-03 시험 조립. 실제 Mongo·녹화 엔진과 가짜/HTTP 광고를 분리한다."""
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

runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None,
                             uri=os.environ["PALDO_MONGO_URI"], collection_prefix=os.environ["PALDO_BENCH_PREFIX"])
game = fixture_service(runtime)
if os.environ.get('PALDO_BENCH_INTENT_FACTORY'):
    from server.app.interactions import InteractionService
    from server.infra.interaction_store import MongoInteractionStore
    from django.utils.module_loading import import_string
    from server.paldo_web.intent import ObservedIntentParser
    from server.tests.asgi_bench_metrics import timed

    parser = ObservedIntentParser(import_string(os.environ['PALDO_BENCH_INTENT_FACTORY'])())
    parser.parse = timed(parser.parse, 'intent_parse')
    store = MongoInteractionStore(runtime.database[os.environ['PALDO_BENCH_PREFIX'] + 'b_interactions'])
    game.interactions = InteractionService(game, store, parser=parser)
configure_game_service(game)
configure_session_service(runtime.sessions)
from server.tests.asgi_bench_metrics import timed


for name in ('load', 'find_command', 'commit', 'create'):
    setattr(game.repository, name, timed(getattr(game.repository, name), name))
game.dependencies = timed(game.dependencies, 'content')
for name in ('load', 'add_game'):
    setattr(game.progress, name, timed(getattr(game.progress, name), 'progress_' + name))
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
    from dataclasses import replace
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
