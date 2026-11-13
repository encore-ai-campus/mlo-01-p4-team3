"""광고 신호 전용 웹소켓 URL. 게임 명령은 HTTP로 유지한다."""
from django.urls import path
from .consumers import GameConsumer

websocket_urlpatterns = [path("ws/game/", GameConsumer.as_asgi())]
