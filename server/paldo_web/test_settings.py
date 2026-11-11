"""외부 서비스 없는 단위 시험 전용. MySQL 원자성·운영 준비를 증명하지 않는다."""
import os
import secrets

os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("PALDO_PUBLIC_ORIGIN", "http://testserver")
from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ALLOWED_HOSTS = ["testserver", "localhost"]
SESSION_MONGO_URI = ""
# 기존 자유 입력 시험은 켠 상태 기준이다(환경 변수를 직접 준 미리보기만 그 값을 따른다). 끈 상태는 `test_free_text_flag.py`가 확인한다.
if "PALDO_FREE_TEXT_INPUT" not in os.environ:
    FREE_TEXT_INPUT = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
LOGGING["loggers"]["paldo.web"]["handlers"] = ["console"]
LOGGING["handlers"].pop("file")
