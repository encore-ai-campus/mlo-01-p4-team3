"""환경 변수 기반 설정. 운영 계정 저장소는 MySQL만 사용한다."""
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY 환경 변수가 필요합니다.")
DEBUG = False
GAME_UI = os.environ.get("PALDO_GAME_UI", "vn")
if GAME_UI not in {"classic", "vn"}:
    raise ImproperlyConfigured("PALDO_GAME_UI는 classic 또는 vn이어야 합니다.")
PUBLIC_ORIGIN = os.environ.get("PALDO_PUBLIC_ORIGIN", "")
_origin = urlsplit(PUBLIC_ORIGIN)
if _origin.scheme not in ("http", "https") or not _origin.hostname or _origin.path not in ("", "/") or _origin.query or _origin.fragment or _origin.username:
    raise ImproperlyConfigured("PALDO_PUBLIC_ORIGIN에 게임 웹 출처를 지정하세요.")
ALLOWED_HOSTS = [_origin.hostname]
# E-09-10: 지표 수집기는 Host를 바꿀 수 없어(Prometheus는 Host 헤더 설정 금지) 대상 주소의 호스트로 온다.
# 수집용 호스트 이름만 더한다(기본 없음, 와일드카드 금지). 로컬 Compose 예: host.docker.internal
_extra_hosts = [h.strip().lower() for h in os.environ.get("PALDO_EXTRA_ALLOWED_HOSTS", "").split(",") if h.strip()]
if any(not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", h) for h in _extra_hosts):
    raise ImproperlyConfigured("PALDO_EXTRA_ALLOWED_HOSTS는 쉼표로 나눈 호스트 이름만 받습니다(와일드카드·포트 금지).")
ALLOWED_HOSTS += _extra_hosts
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
from .channel_layers import ChannelLayerConfigError, channel_layers
try:  # E-09-10: 기본 memory(웹 프로세스 2개·1개 모두), Redis는 선택. server/DESIGN.md "동시성 구조와 한계"
    CHANNEL_LAYERS = channel_layers(os.environ)
except ChannelLayerConfigError as error:
    raise ImproperlyConfigured(str(error)) from None
LOGIN_URL = "/accounts/login"
# 직접 입력창(문장 → 의도 해석 → 카드) 표시·수신(11/11 사용자 결정). 기본 off: 화면에서 입력창을 그리지 않고
# `POST /games/{id}/utterances…`는 403 `free_text_disabled`. on이면 기존 동작 그대로(발표·재학습 시연용).
_free_text = os.environ.get("PALDO_FREE_TEXT_INPUT", "off").strip().lower()
if _free_text not in {"on", "off"}:
    raise ImproperlyConfigured("PALDO_FREE_TEXT_INPUT은 on 또는 off여야 합니다.")
FREE_TEXT_INPUT = _free_text == "on"
SIGNUP_INVITE_CODE = os.environ.get("PALDO_SIGNUP_INVITE_CODE", "")
GAME_SERVICE_FACTORY = os.environ.get("PALDO_GAME_SERVICE_FACTORY", "")
INTENT_PARSER_FACTORY = ""  # 기준선 사용 시 "intent.parser.BaselineIntentParser"를 지정한다.
INTENT_BERT_RUN_DIR = os.environ.get("PALDO_INTENT_BERT_RUN_DIR", "")
ADS_INTERNAL_URL = os.environ.get("PALDO_ADS_INTERNAL_URL", "")
SESSION_MONGO_URI = os.environ.get("PALDO_MONGO_URI", "")
# 회차 없는 일반 판의 content_version(운영 Mongo에 적재된 활성 버전, E-08-14). 비어 있으면 일반 판 시작은 503
ACTIVE_CONTENT_VERSION = os.environ.get("PALDO_CONTENT_VERSION", "")
# 회차 없는 일반 판의 규칙(11/11 투표 기권). 비우면 배포 코드 기본(rule-0.8), "rule-0.9"면 투표 기권 규칙.
# 고를 수 있는 값은 `engine.domain.params.RULE_BASES`. 이미 시작한 판은 판 메타의 규칙 버전으로 이어 간다.
ACTIVE_RULE_BASE = os.environ.get("PALDO_RULE_BASE", "").strip()
# 일반 판 카나리아 배정 비율(B-09-01, ml_lifecycle.md 4절). 0이면 끔. 사용자 테스트 회차 판은 비율과 무관하게 끈다.
MODEL_CANARY_RATIO = float(os.environ.get("PALDO_MODEL_CANARY_RATIO", "0") or 0)
MLFLOW_MODELS = os.environ.get("PALDO_MLFLOW_MODELS", "").strip()
MLFLOW_MODEL_CACHE = os.environ.get("PALDO_MLFLOW_MODEL_CACHE", "mlruns")
# 실제 의심 모델 사용(B-09-01). 비어 있으면 끔(규칙 의심도 `FakeSuspicionModel`). 값은 `<mlruns>/suspicion-v0/<버전>/`의
# 버전 이름. 시작 때 못 띄우면 끔과 같게 동작하고, 판 진행 중 장애는 그 결정만 규칙 v0로 대신한다.
SUSPICION_MODEL_VERSION = os.environ.get("PALDO_SUSPICION_MODEL_VERSION", "").strip()
# 신뢰된 로컬 모델 산출물 폴더(`models.pkl`·`report.json`, D-08-04 인계 형식). 상대 경로는 웹 작업 폴더 기준.
SUSPICION_MODEL_ROOT = os.environ.get("PALDO_MLRUNS_ROOT", "mlruns")
# 씐 자 전략 밴딧(D-09-02, 11/12 운영 연결). "on"·"1"·"true"면 새 일반 사람 판에 게시된 밴딧 버전과 팔을 고정한다.
# 비어 있거나 다른 값이면 끔(기존 균등 혼합). 끄더라도 이미 팔을 고정한 판은 그 팔로 끝까지 간다.
POSSESSED_BANDIT = os.environ.get("PALDO_POSSESSED_BANDIT", "").strip().lower() in {"on", "1", "true"}
# 복기 해설 LLM 요약(E-10-01). 생성 서버(OpenAI 호환) 주소, 예: 게임 서버에서 생성 기기로 가는 터널의 로컬 끝
# `http://127.0.0.1:<port>`. 비어 있으면 기능 꺼짐(작업 미등록·템플릿 해설만·Kanana 표기 숨김). 실제 값은 저장소 밖.
REVIEW_LLM_URL = os.environ.get("PALDO_REVIEW_LLM_URL", "").strip()
# 복기 해설 작업 마감(초). 지나면 템플릿으로 확정한다.
REVIEW_DEADLINE_S = float(os.environ.get("PALDO_REVIEW_DEADLINE_S", "180") or 180)
# NPC 대사 LLM(E-11-13). 생성 서버 주소(상한 스위치): 비어 있으면 모드와 관계없이 꺼짐(템플릿 대사만·Kanana 표기 숨김).
# 보통 복기와 같은 터널 끝이다. 실제 값은 저장소 밖.
NPC_LLM_URL = os.environ.get("PALDO_NPC_LLM_URL", "").strip()
# 운영 설정 문서(`<접두어>ops_settings`의 `npc_llm`)가 없을 때 쓰는 모드: off | polish | live. 코드 기본값 off.
# 재배포 없이 바꿀 때는 관리 명령 `npc_llm_mode`를 쓴다.
NPC_LLM_MODE = os.environ.get("PALDO_NPC_LLM_MODE", "off").strip()
# LLM 작업 프로세스 지표 파일(E-12-01, Prometheus 텍스트 형식). 비면 쓰지 않는다. `{worker}`는 npc_line·review로
# 바뀌고, 없으면 파일 이름 앞에 `<worker>_`를 붙인다. 수집 폴더는 운영(C-12-03)이 정한다.
LLM_METRIC_FILE = os.environ.get("PALDO_LLM_METRIC_FILE", "").strip()
# D-10-03의 공개 특징 전용 로지스틱 JSON. 비어 있으면 복기 승률 예측을 생략한다.
WIN_PROBABILITY_MODEL = os.environ.get("PALDO_WIN_PROBABILITY_MODEL", "").strip()
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
if _db_ca := os.environ.get("PALDO_AUTH_DB_SSL_CA"):
    DATABASES["default"]["OPTIONS"].update(
        ssl_ca=_db_ca, ssl_verify_cert=True, ssl_verify_identity=True)
import pymysql
pymysql.install_as_MySQLdb()
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates",
              "DIRS": [BASE_DIR / "web" / "templates"], "APP_DIRS": False,
              "OPTIONS": {"loaders": [("server.paldo_web.ui_templates.CachedLoader", ["server.paldo_web.ui_templates.Loader", "django.template.loaders.app_directories.Loader"])], "context_processors": [
                  "django.template.context_processors.request",
                  "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages",
                  "server.paldo_web.ui_flags.ui_flags",
              ], "libraries": {"meeting": "server.api.template_tags", "ad_slots": "server.api.ad_slot_tags"}}}]
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
# 웹 프로세스가 둘이면(E-09-10) 같은 파일을 함께 회전하지 않도록 두 번째부터 포트를 붙인 파일에 쓴다.
_web_instance = os.environ.get("PALDO_WEB_INSTANCE", "")
if _web_instance and not _web_instance.isdigit():
    raise ImproperlyConfigured("PALDO_WEB_INSTANCE는 웹 포트 숫자여야 합니다.")
WEB_LOG_FILE = "web.jsonl" if _web_instance in ("", "8000") else f"web-{_web_instance}.jsonl"
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"json": {"()": "server.paldo_web.middleware.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
        "file": {"class": "logging.handlers.TimedRotatingFileHandler",
                 "filename": str(LOG_DIR / WEB_LOG_FILE), "when": "midnight", "utc": True,
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
