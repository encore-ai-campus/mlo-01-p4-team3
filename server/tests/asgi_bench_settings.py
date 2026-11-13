"""B-07-05 소켓 부하 전용 설정. 임시 계정 DB이며 운영에서 사용하지 않는다."""
import os
from urllib.parse import urlsplit
from server.paldo_web.test_settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["PALDO_BENCH_AUTH_DB"]}}
ALLOWED_HOSTS = ["127.0.0.1", urlsplit(os.environ["PALDO_PUBLIC_ORIGIN"]).hostname]
MIDDLEWARE = ['server.tests.asgi_bench_metrics.BenchTimingMiddleware', *MIDDLEWARE]
INTENT_PARSER_FACTORY = os.environ.get('PALDO_BENCH_INTENT_FACTORY', '')
