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
_ad_gateway = None
_ad_lock = Lock()


def get_ad_gateway():
    """명시적 주입을 우선하고 내부 광고 출처 설정이 있으면 HTTP 게이트웨이를 조립한다."""
    global _ad_gateway
    if _ad_gateway is None and settings.ADS_INTERNAL_URL:
        with _ad_lock:
            if _ad_gateway is None:
                from server.app.ads_port import AdGateway
                from server.infra.ad_client import HTTPAdProvider
                try:
                    provider = HTTPAdProvider(settings.ADS_INTERNAL_URL)
                except ValueError:
                    raise GameServiceUnavailable('invalid_ads_origin') from None
                _ad_gateway = AdGateway(provider)
    if _ad_gateway is None:
        raise GameServiceUnavailable("ad_gateway_not_configured")
    return _ad_gateway


def configure_ad_gateway(gateway):
    """광고 서버 인수 조립이나 테스트에서 게이트웨이를 주입한다."""
    global _ad_gateway
    with _ad_lock:
        _ad_gateway = gateway


class GameServiceUnavailable(RuntimeError):
    """게임 서비스가 아직 조립되지 않았다."""


def get_game_service():
    """설정한 게임 서비스를 반환하고 미연결이면 요청을 차단한다."""
    global _game_service
    if _game_service is None and settings.GAME_SERVICE_FACTORY:
        with _lock:
            if _game_service is None:
                service = import_string(settings.GAME_SERVICE_FACTORY)()
                _configure_parser(service)
                _game_service = service
    if _game_service is None:
        raise GameServiceUnavailable("game_service_not_configured")
    service = _game_service
    if service.prepare_start is None:
        service.prepare_start = start_policy(partial(_prepare_fixed_start, load_game=service.repository.load),
                                             service.repository.load)
    return service


def _prepare_fixed_start(data, player, load_game):
    return get_session_service().prepare(data, player, load_game)


def open_start_versions():
    """일반 판 버전: 배포 코드의 규칙·엔진 버전과 설정의 운영 활성 콘텐츠 버전(E-08-14)."""
    from engine.domain import params
    from server.app.open_start import deployed_versions
    return deployed_versions(lambda: params.RULE_PARAMS_VERSION, settings.ACTIVE_CONTENT_VERSION)


def start_policy(session_prepare, load_game):
    """회차 판은 회차 정책, 회차 없는 판은 랜덤 시드 일반 판으로 시작하는 정책을 만든다."""
    from server.app.open_start import StartPolicy
    return StartPolicy(session_prepare, open_start_versions, load_game)


def configure_game_service(service):
    """호스트 조립이나 테스트에서 계약 구현을 주입한다."""
    global _game_service
    with _lock:
        if service is not None:
            _configure_parser(service)
        _game_service = service


def _configure_parser(service):
    from .intent import ObservedIntentParser
    interactions = service.interactions
    if interactions is None or isinstance(interactions.parser, ObservedIntentParser):
        return
    factory = getattr(settings, "INTENT_PARSER_FACTORY", "")
    parser = import_string(factory)() if factory else interactions.parser
    if parser is not None:
        interactions.parser = ObservedIntentParser(parser)


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
