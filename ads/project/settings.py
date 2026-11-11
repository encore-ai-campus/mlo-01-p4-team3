"""게임 웹과 분리된 광고 서버의 MySQL·로깅 설정."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pymysql
from django.core.exceptions import ImproperlyConfigured

pymysql.install_as_MySQLdb()
BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
ADS_TOKEN_KEY = os.environ.get("PALDO_ADS_TOKEN_KEY", "")
if not SECRET_KEY or not ADS_TOKEN_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY와 PALDO_ADS_TOKEN_KEY가 필요합니다.")
DEBUG = False
_internal = urlsplit(os.environ.get("PALDO_ADS_INTERNAL_URL", "http://127.0.0.1:8001"))
ALLOWED_HOSTS = [_internal.hostname or "127.0.0.1", "localhost"]
ROOT_URLCONF = "ads.project.urls"
WSGI_APPLICATION = "ads.project.wsgi.application"
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "ads.web.apps.AdsConfig",
]
MIDDLEWARE = [
    "ads.web.telemetry.RequestLogMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
DATABASES = {"default": {
    "ENGINE": "django.db.backends.mysql",
    "NAME": os.environ.get("PALDO_ADS_DB_NAME", ""),
    "USER": os.environ.get("PALDO_ADS_DB_USER", ""),
    "PASSWORD": os.environ.get("PALDO_ADS_DB_PASSWORD", ""),
    "HOST": os.environ.get("PALDO_ADS_DB_HOST", ""),
    "PORT": os.environ.get("PALDO_ADS_DB_PORT", "3306"),
    "OPTIONS": {"charset": "utf8mb4", "isolation_level": "read committed"},
    "TEST": {"NAME": os.environ.get("PALDO_ADS_DB_TEST_NAME", "test_paldo_ads")},
    # 11/11: 원격 MySQL TLS 연결을 요청마다 새로 맺으면 첫 조회에 약 230ms가 든다. gunicorn gthread(고정 스레드)에서
    # 스레드별 연결을 60초 재사용하고, 재사용 전에 끊긴 연결을 확인한다. 0이면 요청마다 닫는다(이전 동작).
    "CONN_MAX_AGE": int(os.environ.get("PALDO_ADS_DB_CONN_MAX_AGE", "60")),
    "CONN_HEALTH_CHECKS": True,
}}
if _db_ca := os.environ.get("PALDO_ADS_DB_SSL_CA"):
    DATABASES["default"]["OPTIONS"].update(
        ssl_ca=_db_ca, ssl_verify_cert=True, ssl_verify_identity=True)
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [], "APP_DIRS": True, "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages"]}}]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _internal.scheme == "https"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "out" / "ads_static"
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOG_DIR = Path(os.environ.get("PALDO_LOG_DIR", BASE_DIR / "out" / "logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"json": {"()": "ads.web.telemetry.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"},
        "file": {"class": "logging.handlers.TimedRotatingFileHandler",
            "filename": str(LOG_DIR / "ads.jsonl"), "when": "midnight", "utc": True,
            "backupCount": 7, "encoding": "utf-8", "formatter": "json"},
        "null": {"class": "logging.NullHandler"},
    },
    "loggers": {
        "paldo.ads": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        **{name: {"handlers": ["null"], "propagate": False}
           for name in ("django.request", "django.security", "django.server")},
    },
}
