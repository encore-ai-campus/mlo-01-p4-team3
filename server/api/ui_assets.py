"""정식 UI는 공통 manifest의 에셋 ID만 조회한다."""
import json
from pathlib import Path

from django.conf import settings
from .assets import asset_url


def review_asset_url(content_id):
    """공식 에셋 조회의 템플릿 호환 이름. 시안 파일은 읽지 않는다."""
    return asset_url(content_id)


def vn_sprite_url(npc_id, expression="", mudang_portrait_id="portrait:mudang_f"):
    """공개 인물·표정 ID를 manifest로 조회하고 없는 표정은 같은 인물의 기본 그림을 쓴다.

    무당은 기존 판의 남/여 선택을 따른다. 미등록·임시·파일 누락인 표정은 기본으로
    대체하며 경로를 추측하거나 시안 디렉터리·외부 URL을 읽지 않는다.
    """
    actor = (mudang_portrait_id or "portrait:mudang_f").removeprefix("portrait:") if npc_id == "mudang" else npc_id
    base = f"sprite:{actor}"
    if expression in ("joy", "flustered", "sad"):
        variant = f"{actor}__{expression}"
        entries = json.loads(Path(settings.ASSET_MANIFEST).read_text(encoding="utf-8"))
        for item in entries:
            if item["kind"] == "sprite" and item["id"] == variant and not item.get("placeholder", False):
                candidate = (settings.BASE_DIR / item["path"]).resolve()
                if candidate.is_relative_to((settings.BASE_DIR / "content/images").resolve()) and candidate.is_file():
                    return item["url"]
                break
    return asset_url(base)
