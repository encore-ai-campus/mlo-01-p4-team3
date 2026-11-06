"""게임 서비스와 B 테스트 회차 저장소의 조립 경계."""
from functools import partial
from threading import Lock

from django.conf import settings
from django.utils.module_loading import import_string
from server.app.usecases import StartUnavailable

_game_service = None
_lock = Lock()
_session_service = None
_session_lock = Lock()


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
    service = _game_service
    if service.prepare_start is None:
        service.prepare_start = partial(_prepare_fixed_start, load_game=service.repository.load)
    return service


def _prepare_fixed_start(data, player, load_game):
    return get_session_service().prepare(data, player, load_game)


def configure_game_service(service):
    """호스트 조립이나 테스트에서 계약 구현을 주입한다."""
    global _game_service
    with _lock:
        _game_service = service


def get_session_service():
    """환경의 Mongo 연결 또는 명시적으로 주입한 회차 서비스를 반환한다."""
    global _session_service
    if _session_service is None:
        if not settings.SESSION_MONGO_URI:
            raise StartUnavailable
        with _session_lock:
            if _session_service is None:
                from server.app.test_sessions import TestSessionService
                from server.infra.test_sessions import connect_session_store
                _session_service = TestSessionService(connect_session_store(settings.SESSION_MONGO_URI))
    return _session_service


def configure_session_service(service):
    """검증용 회차 서비스를 주입하거나 검증 종료 뒤 해제한다."""
    global _session_service
    with _session_lock:
        _session_service = service
