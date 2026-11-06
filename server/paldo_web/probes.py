"""계정 DB 상태 확인과 Prometheus 지표 응답."""
from django.db import DatabaseError, connection
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def _database_reachable():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return False
    return True


@require_GET
def health(request):
    """DB 연결을 확인하고 장애 시 주소나 오류 원문을 숨긴다."""
    if not _database_reachable():
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@require_GET
def ready(request):
    """Django 골격의 DB 연결 준비 상태를 반환한다."""
    if not _database_reachable():
        return JsonResponse({"status": "not_ready"}, status=503)
    return JsonResponse({"status": "ready"})


@require_GET
def metrics(request):
    """Prometheus 수집 형식으로 요청 지표를 반환한다."""
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
