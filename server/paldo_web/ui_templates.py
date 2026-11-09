"""UI 설정에 따라 기존 화면 또는 검토 중인 창귀 화면의 템플릿을 선택한다."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.loaders.filesystem import Loader as FilesystemLoader
from django.template.loaders.cached import Loader as TemplateCache


class CachedLoader(TemplateCache):
    """설정별 캐시를 분리해 기존 화면과 VN 템플릿이 섞이지 않게 한다."""

    def cache_key(self, template_name, skip=None):
        """기본 템플릿 캐시 키 앞에 선택한 UI 모드를 붙인다."""
        return settings.GAME_UI + ':' + super().cache_key(template_name, skip)


class Loader(FilesystemLoader):
    """classic은 기존 템플릿을 우선하고 vn은 창귀 템플릿을 사용한다."""

    def get_dirs(self):
        """캐시 키가 모드별로 달라지도록 실제 디렉터리 경로를 반환한다."""
        mode = settings.GAME_UI
        if mode not in {'classic', 'vn'}:
            raise ImproperlyConfigured('PALDO_GAME_UI는 classic 또는 vn이어야 합니다.')
        root = settings.BASE_DIR / 'web/templates'
        return [root / 'classic', root] if mode == 'classic' else [root]
