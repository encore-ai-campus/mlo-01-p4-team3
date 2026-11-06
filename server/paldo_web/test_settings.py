"""외부 서비스 없는 단위 시험 전용. MySQL 원자성·운영 준비를 증명하지 않는다."""
import os
import secrets

os.environ.setdefault("DJANGO_SECRET_KEY", secrets.token_urlsafe(48))
os.environ.setdefault("PALDO_PUBLIC_ORIGIN", "http://testserver")
from .settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ALLOWED_HOSTS = ["testserver", "localhost"]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
LOGGING["loggers"]["paldo.web"]["handlers"] = ["console"]
LOGGING["handlers"].pop("file")
