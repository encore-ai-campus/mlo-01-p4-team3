"""별도 광고 HTTP 프로세스의 표준 Django ASGI 진입점."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE','ads.project.settings')
application=get_asgi_application()
