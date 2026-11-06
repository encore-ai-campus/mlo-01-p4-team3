"""로컬 MySQL의 전용 스키마에서 Django 계정 시험을 실행한다."""
import os
import sys

from django.core.exceptions import ImproperlyConfigured

if sys.argv[1:2] != ["test"]:
    raise ImproperlyConfigured("mysql_test_settings는 Django test 명령에서만 사용합니다.")

from .test_settings import *  # noqa: F403
from .settings import BASE_DIR as _base_dir, DATABASES as _mysql_databases

if all(os.environ.get(key) for key in ("PALDO_AUTH_DB_NAME", "PALDO_AUTH_DB_USER", "PALDO_AUTH_DB_PASSWORD")):
    _values = os.environ
else:
    _env_path = _base_dir / "server" / ".env"
    if not _env_path.is_file():
        raise ImproperlyConfigured("로컬 MySQL 시험 접속값을 환경 변수 또는 server/.env에 지정하세요.")
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise ImproperlyConfigured(".env를 사용하는 로컬 MySQL 시험에는 python-dotenv가 필요합니다.") from exc
    _values = dotenv_values(_env_path, encoding="utf-8-sig", interpolate=False)
_db = {
    **_mysql_databases["default"],
    "HOST": _values.get("PALDO_AUTH_DB_HOST") or "127.0.0.1",
    "PORT": _values.get("PALDO_AUTH_DB_PORT") or "3306",
    "NAME": _values.get("PALDO_AUTH_DB_NAME"),
    "USER": _values.get("PALDO_AUTH_DB_USER"),
    "PASSWORD": _values.get("PALDO_AUTH_DB_PASSWORD"),
}
if _db["HOST"] not in {"127.0.0.1", "::1", "localhost"}:
    raise ImproperlyConfigured("PALDO_AUTH_DB_HOST에 로컬 MySQL 주소를 지정하세요.")
if _db["NAME"] != "test_paldo_auth":
    raise ImproperlyConfigured("PALDO_AUTH_DB_NAME은 시험 전용 test_paldo_auth여야 합니다.")
if not _db["USER"] or _db["USER"].lower() == "root" or not _db["PASSWORD"]:
    raise ImproperlyConfigured("PALDO_AUTH_DB_USER와 PALDO_AUTH_DB_PASSWORD에 시험 전용 계정을 지정하세요.")

DATABASES = {"default": {**_db, "TEST": {"NAME": _db["NAME"], "CHARSET": "utf8mb4"}}}
