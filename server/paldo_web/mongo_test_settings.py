"""로컬 Mongo 회차 저장소를 검사하는 Django test 전용 설정."""
import os
import sys

from django.core.exceptions import ImproperlyConfigured

if sys.argv[1:2] != ["test"]:
    raise ImproperlyConfigured("mongo_test_settings는 Django test 명령에서만 사용합니다.")

from .test_settings import *  # noqa: F403
from .settings import BASE_DIR as _base_dir

MONGO_TEST_URI = os.environ.get("PALDO_MONGO_URI")
_env_path = _base_dir / "server" / ".env"
if not MONGO_TEST_URI and _env_path.is_file():
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise ImproperlyConfigured("server/.env를 읽으려면 python-dotenv가 필요합니다.") from exc
    MONGO_TEST_URI = dotenv_values(_env_path, encoding="utf-8-sig", interpolate=False).get("PALDO_MONGO_URI")
if not MONGO_TEST_URI:
    raise ImproperlyConfigured("환경 변수 또는 server/.env에 로컬 PALDO_MONGO_URI를 지정하세요.")
