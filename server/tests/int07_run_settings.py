"""INT-07 웹 자동 재현(`tools/perf/int07_web_run.py`) 전용 설정. 시험 설정 위에 실행용 SQLite 계정 DB와 실제 Mongo를 얹는다.

`PALDO_INT07_SQLITE`(계정 DB 파일 경로)와 `PALDO_MONGO_URI`(레플리카셋, DB 이름 포함)는 실행 도구가 넣는다.
"""
import os

from server.paldo_web.test_settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["PALDO_INT07_SQLITE"]}}
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
SESSION_MONGO_URI = os.environ["PALDO_MONGO_URI"]
