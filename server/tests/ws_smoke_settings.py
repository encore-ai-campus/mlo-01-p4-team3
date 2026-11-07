"""Daphne 소켓 스모크 전용 임시 SQLite 설정. 운영 조립에 사용하지 않는다."""
import os
from server.paldo_web.test_settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["PALDO_WS_SMOKE_DB"]}}
ALLOWED_HOSTS = ["127.0.0.1"]
