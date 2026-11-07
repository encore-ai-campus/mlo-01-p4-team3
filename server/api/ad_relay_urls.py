"""광고 클릭·가상 랜딩 중계 URL."""
from django.urls import path
from . import ad_relay

urlpatterns = [path("ads/click", ad_relay.click, name="ad-click"),
               path("ads/landing/<slug:creative_id>", ad_relay.landing, name="ad-landing")]
