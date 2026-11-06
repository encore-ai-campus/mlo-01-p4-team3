"""Django 골격의 운영 상태 확인 경로."""
from django.urls import path

from . import probes

urlpatterns = [
    path("healthz", probes.health, name="health"),
    path("readyz", probes.ready, name="ready"),
    path("metrics", probes.metrics, name="metrics"),
]
