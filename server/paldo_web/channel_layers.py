"""게임 웹 채널 레이어 설정(E-09-10). 환경 값만 읽어 Django `CHANNEL_LAYERS`를 만든다.

- `PALDO_CHANNEL_LAYER=memory`(값이 없을 때도 이것): 프로세스 안 채널 레이어. 기본 배포값(웹 프로세스 2개)과
  되돌림 값(1개) 모두 이것이다. 지금 웹소켓은 광고 신호 전용이라 프로세스 사이 전달이 없다.
- `PALDO_CHANNEL_LAYER=redis`: Redis 채널 레이어(선택). 서버가 다른 프로세스의 연결로 보내는 기능이 생기면 켠다.
  `PALDO_REDIS_URL`(redis://호스트:포트/DB)이 필요하고, 기동 때 연결을 확인해 실패하면 멈춘다.
"""
from urllib.parse import urlsplit

MEMORY = "memory"
REDIS = "redis"
_MEMORY_BACKEND = "channels.layers.InMemoryChannelLayer"
_REDIS_BACKEND = "channels_redis.core.RedisChannelLayer"


class ChannelLayerConfigError(ValueError):
    """채널 레이어 환경 값이 잘못됐다. 메시지에 주소·비밀값을 넣지 않는다."""


def layer_kind(env) -> str:
    """환경 값에서 채널 레이어 종류(memory·redis)를 읽는다."""
    kind = (env.get("PALDO_CHANNEL_LAYER") or MEMORY).strip().lower()
    if kind not in (MEMORY, REDIS):
        raise ChannelLayerConfigError("PALDO_CHANNEL_LAYER는 redis 또는 memory여야 합니다.")
    return kind


def channel_layers(env) -> dict:
    """Django `CHANNEL_LAYERS` 값을 만든다. Redis인데 주소가 없거나 형식이 틀리면 예외를 낸다."""
    if layer_kind(env) == MEMORY:
        return {"default": {"BACKEND": _MEMORY_BACKEND}}
    url = (env.get("PALDO_REDIS_URL") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("redis", "rediss") or not parts.hostname:
        raise ChannelLayerConfigError(
            "PALDO_CHANNEL_LAYER=redis에는 PALDO_REDIS_URL(redis://호스트:포트/DB)이 필요합니다.")
    return {"default": {"BACKEND": _REDIS_BACKEND, "CONFIG": {
        "hosts": [url],
        # 같은 Redis를 다른 용도와 함께 쓸 때 키가 섞이지 않게 접두어를 둔다.
        "prefix": (env.get("PALDO_REDIS_PREFIX") or "paldo-web").strip(),
    }}}


def uses_redis(layers: dict) -> bool:
    """설정이 Redis 채널 레이어인지 돌려준다."""
    return layers.get("default", {}).get("BACKEND") == _REDIS_BACKEND


def check_reachable(layers: dict, timeout: float = 2.0) -> None:
    """Redis 채널 레이어면 PING으로 연결을 확인한다. 실패하면 InMemory로 떨어지지 않고 기동을 멈춘다."""
    if not uses_redis(layers):
        return
    try:
        import redis
    except ImportError:
        raise RuntimeError("Redis 채널 레이어 패키지(channels_redis·redis)가 설치되지 않았습니다. "
                           "requirements/web.txt를 설치하거나 되돌림 값 PALDO_CHANNEL_LAYER=memory를 쓰세요.") from None
    client = None
    try:
        client = redis.Redis.from_url(layers["default"]["CONFIG"]["hosts"][0],
                                      socket_connect_timeout=timeout, socket_timeout=timeout)
        client.ping()
    except (redis.RedisError, OSError, ValueError):
        # 주소에 비밀번호가 들어갈 수 있으므로 원문 오류를 붙이지 않는다.
        raise RuntimeError("Redis 채널 레이어에 연결할 수 없습니다. PALDO_REDIS_URL과 Redis 상태를 확인하거나 "
                           "되돌림 값 PALDO_CHANNEL_LAYER=memory를 쓰세요.") from None
    finally:
        if client is not None:
            client.close()
