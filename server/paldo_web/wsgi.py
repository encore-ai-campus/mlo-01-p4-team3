"""HTTP 개발용 WSGI 진입점. 광고 WS는 이후 태스크에서 연결한다."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.paldo_web.settings")
application = get_wsgi_application()
