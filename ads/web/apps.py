"""광고 Django 앱 등록."""
from django.apps import AppConfig


class AdsConfig(AppConfig):
    """게임 웹과 충돌하지 않는 광고 앱 이름."""
    name = "ads.web"
    label = "ads_web"
    verbose_name = "가상 광고"
