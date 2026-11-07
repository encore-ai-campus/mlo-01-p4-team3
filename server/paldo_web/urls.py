"""상태 확인, 계정·관리 화면, 게임 API·화면과 정적 에셋 경로를 등록한다."""
from django.contrib import admin
from django.urls import include, path

from server.accounts import views as account_views
from server.api.assets import asset_file
from server.api.sessions import sessions
from . import probes

urlpatterns = [
    path("", account_views.home, name="home"),
    path("healthz", probes.health, name="health"),
    path("readyz", probes.ready, name="ready"),
    path("metrics", probes.metrics, name="metrics"),
    path("static/<path:asset_path>", asset_file, name="game-static"),
    path("accounts/", include("server.accounts.urls")),
    # Django admin도 일반 계정과 같은 잠금 규칙을 적용한다.
    path("admin/login/", account_views.signin, name="admin-login"),
    path("admin/sessions", sessions, name="test-sessions"),
    path("admin/", admin.site.urls),
    path("", include("server.api.urls")),
    path("", include("server.api.ad_relay_urls")),
    path("", include("server.api.ad_slots_urls")),
]
