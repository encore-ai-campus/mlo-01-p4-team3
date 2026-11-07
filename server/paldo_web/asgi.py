"""HTTP와 출처·세션으로 보호한 광고 웹소켓의 ASGI 진입점."""
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.paldo_web.settings")
from django.core.asgi import get_asgi_application

http_application = get_asgi_application()
from django.conf import settings
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import OriginValidator
from server.api.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": http_application,
    "websocket": OriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
                                 [settings.PUBLIC_ORIGIN.rstrip("/")]),
})
