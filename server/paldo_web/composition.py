"""운영 또는 테스트에서 주입한 게임 서비스의 조립 경계."""
from threading import Lock

from django.conf import settings
from django.utils.module_loading import import_string

_game_service = None
_lock = Lock()


class GameServiceUnavailable(RuntimeError):
    """게임 서비스가 아직 조립되지 않았다."""


def get_game_service():
    """설정한 게임 서비스를 반환하고 미연결이면 요청을 차단한다."""
    global _game_service
    if _game_service is None and settings.GAME_SERVICE_FACTORY:
        with _lock:
            if _game_service is None:
                _game_service = import_string(settings.GAME_SERVICE_FACTORY)()
    if _game_service is None:
        raise GameServiceUnavailable("game_service_not_configured")
    return _game_service


def configure_game_service(service):
    """호스트 조립이나 테스트에서 계약 구현을 주입한다."""
    global _game_service
    with _lock:
        _game_service = service
