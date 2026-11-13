"""지연 로드 광고 지면의 게임 웹 라우트."""
from django.urls import path
from .ad_slots import slot

urlpatterns = [path("ads/slot/<str:slot>", slot, name="ad-slot")]
