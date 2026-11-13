"""광고 클릭·가상 랜딩 중계 URL. 랜딩 ID는 합성 소재의 "syn:" 접두어를 받도록 ':'를 허용한다."""
from django.urls import path, re_path
from . import ad_relay

urlpatterns = [path("ads/click", ad_relay.click, name="ad-click"),
               re_path(r"^ads/landing/(?P<creative_id>[A-Za-z0-9_:-]{1,128})$", ad_relay.landing, name="ad-landing")]
