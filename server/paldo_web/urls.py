"""상태 확인, 게임 API·화면, 정적 에셋 경로를 등록한다."""
from django.urls import include, path

from server.api.assets import asset_file
from . import probes

urlpatterns = [
    path("healthz", probes.health, name="health"),
    path("readyz", probes.ready, name="ready"),
    path("metrics", probes.metrics, name="metrics"),
    path("static/<path:asset_path>", asset_file, name="game-static"),
    path("", include("server.api.urls")),
]
