"""정식 UI는 공통 manifest의 에셋 ID만 조회한다."""
from .assets import asset_url


def review_asset_url(content_id):
    """공식 에셋 조회의 템플릿 호환 이름. 시안 파일은 읽지 않는다."""
    return asset_url(content_id)
