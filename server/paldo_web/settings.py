"""환경 변수 기반 설정. 운영 계정 저장소는 MySQL만 사용한다."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY 환경 변수가 필요합니다.")
DEBUG = False
PUBLIC_ORIGIN = os.environ.get("PALDO_PUBLIC_ORIGIN", "")
_origin = urlsplit(PUBLIC_ORIGIN)
if _origin.scheme not in ("http", "https") or not _origin.hostname or _origin.path not in ("", "/") or _origin.query or _origin.fragment or _origin.username:
    raise ImproperlyConfigured("PALDO_PUBLIC_ORIGIN에 게임 웹 출처를 지정하세요.")
ALLOWED_HOSTS = [_origin.hostname]
CSRF_TRUSTED_ORIGINS = [PUBLIC_ORIGIN.rstrip("/")]
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = _origin.scheme == "https"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 1_209_600
if SESSION_COOKIE_SECURE:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
ROOT_URLCONF = "server.paldo_web.urls"
WSGI_APPLICATION = "server.paldo_web.wsgi.application"
ASGI_APPLICATION = "server.paldo_web.asgi.application"
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
LOGIN_URL = "/accounts/login"
SIGNUP_INVITE_CODE = os.environ.get("PALDO_SIGNUP_INVITE_CODE", "")
GAME_SERVICE_FACTORY = os.environ.get("PALDO_GAME_SERVICE_FACTORY", "")
INTENT_PARSER_FACTORY = ""  # 기준선 사용 시 "intent.parser.BaselineIntentParser"를 지정한다.
SESSION_MONGO_URI = os.environ.get("PALDO_MONGO_URI", "")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "server.accounts",
]
MIDDLEWARE = [
    "server.paldo_web.middleware.RequestTelemetryMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
APPEND_SLASH = False
DATABASES = {"default": {
    "ENGINE": "django.db.backends.mysql",
    "NAME": os.environ.get("PALDO_AUTH_DB_NAME", ""),
    "USER": os.environ.get("PALDO_AUTH_DB_USER", ""),
    "PASSWORD": os.environ.get("PALDO_AUTH_DB_PASSWORD", ""),
    "HOST": os.environ.get("PALDO_AUTH_DB_HOST", ""),
    "PORT": os.environ.get("PALDO_AUTH_DB_PORT") or "3306",
    "OPTIONS": {"charset": "utf8mb4", "isolation_level": "read committed"},
}}
import pymysql
pymysql.install_as_MySQLdb()
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
              "DIRS": [BASE_DIR / "web" / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": [
                  "django.template.context_processors.request",
                  "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages",
              ], "libraries": {"meeting": "server.api.template_tags"}}}]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "server.accounts.validators.PasswordRulesValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "out" / "static"
STATICFILES_DIRS = [BASE_DIR / "web" / "static"]
ASSET_MANIFEST = BASE_DIR / "content" / "images" / "manifest.json"
LOG_DIR = BASE_DIR / "out" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"json": {"()": "server.paldo_web.middleware.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
        "file": {"class": "logging.handlers.TimedRotatingFileHandler",
                 "filename": str(LOG_DIR / "web.jsonl"), "when": "midnight", "utc": True,
                 "backupCount": 7, "encoding": "utf-8", "formatter": "json"},
        "null": {"class": "logging.NullHandler"},
    },
    "loggers": {
        "paldo.web": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["null"], "propagate": False},
        "django.security": {"handlers": ["null"], "propagate": False},
        "django.server": {"handlers": ["null"], "propagate": False},
    },
}
