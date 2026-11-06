"""운영 상태 확인과 게임 JSON API 경로를 등록한다."""
from django.urls import include, path

from . import probes

urlpatterns = [
    path("healthz", probes.health, name="health"),
    path("readyz", probes.ready, name="ready"),
    path("metrics", probes.metrics, name="metrics"),
    path("", include("server.api.urls")),
]
