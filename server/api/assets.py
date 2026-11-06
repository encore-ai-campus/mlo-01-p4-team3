"""manifest ID 조회와 등록된 이미지·웹 정적 파일만의 제공."""
import json
import mimetypes
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404
from django.views.decorators.http import require_GET
from server.paldo_web.middleware import LOGGER


def asset_url(content_id):
    """kind:id로 manifest URL을 찾고 누락은 기본 이미지로 대체한다."""
    items = json.loads(Path(settings.ASSET_MANIFEST).read_text(encoding="utf-8"))
    for item in items:
        if f"{item['kind']}:{item['id']}" == content_id:
            return item["url"]
    LOGGER.warning("asset_missing", extra={"component": "assets"})
    return "/static/fallback.svg"


@require_GET
def asset_file(request, asset_path):
    """manifest에 등록한 파일과 web/static 아래 파일만 연다."""
    url = "/static/" + asset_path
    items = json.loads(Path(settings.ASSET_MANIFEST).read_text(encoding="utf-8"))
    for item in items:
        if item["url"] == url:
            candidate = (settings.BASE_DIR / item["path"]).resolve()
            if not candidate.is_relative_to((settings.BASE_DIR / "content/images").resolve()):
                raise Http404
            break
    else:
        root = (settings.BASE_DIR / "web/static").resolve()
        candidate = (root / asset_path).resolve()
        if not candidate.is_relative_to(root):
            raise Http404
    if not candidate.is_file():
        raise Http404
    response = FileResponse(candidate.open("rb"), content_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
    response["X-Content-Type-Options"] = "nosniff"
    return response
