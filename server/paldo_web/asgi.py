"""HTTP와 출처·세션으로 보호한 광고 웹소켓의 ASGI 진입점."""
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.paldo_web.settings")
from django.core.asgi import get_asgi_application

http_application = get_asgi_application()
from django.conf import settings
from .channel_layers import check_reachable

# Redis 채널 레이어가 켜져 있으면 기동 때 연결을 확인한다(실패 시 InMemory로 떨어지지 않고 멈춤, E-09-10).
check_reachable(settings.CHANNEL_LAYERS)
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from server.api.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": http_application,
    "websocket": OriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
                                 [settings.PUBLIC_ORIGIN.rstrip("/")]),
})
