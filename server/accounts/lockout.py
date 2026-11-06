"""계정 DB의 행 잠금으로 로그인 잠금 상태를 원자 갱신한다."""
import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.utils import timezone

from .models import AccountProfile, LoginLock
from .validators import normalize_login_id
from server.paldo_web.middleware import LOGGER, LOGIN_RESULTS


def counter_key(purpose, value):
    """로그인 ID를 그대로 저장하지 않도록 HMAC 키를 만든다."""
    return hmac.new(settings.SECRET_KEY.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def login_identity(login_id):
    """Django가 조회하는 계정과 그 계정의 정규화 ID로 만든 잠금 키를 반환한다."""
    username = normalize_login_id(login_id)
    user_model = get_user_model()
    try:
        account = user_model._default_manager.get_by_natural_key(username)
    except user_model.DoesNotExist:
        return counter_key("login", username), None
    # MySQL 문자열 비교에서 같은 계정인 철자는 저장된 ID의 잠금 행을 공유한다.
    return counter_key("login", normalize_login_id(account.get_username())), account


def login_user(request, login_id, password, now=None):
    """잠금 중 정답도 거부한다. 실패 이유는 화면에 구분해 노출하지 않는다."""
    now = now or timezone.now()
    key, account = login_identity(login_id)
    user = None
    # 동시 INSERT의 중복 키 잠금을 먼저 끝낸 뒤 갱신용 행 잠금을 잡는다.
    LoginLock.objects.get_or_create(pk=key)
    with transaction.atomic():
        row = LoginLock.objects.select_for_update().get(pk=key)
        if row.locked_until and row.locked_until > now:
            result = "locked"
        else:
            if row.locked_until:
                row.failures, row.locked_until = 0, None
            if account is None:
                # Django의 기본 인증처럼 없는 계정에도 비밀번호 해시를 한 번 계산한다.
                get_user_model()().set_password(password)
            else:
                user = authenticate(request, username=account.get_username(), password=password)
            if user is not None:
                row.failures, row.locked_until, result = 0, None, "success"
            else:
                row.failures += 1
                result = "failure"
                if row.failures >= 5:
                    row.locked_until, result = now + timedelta(minutes=15), "locked"
            row.save(update_fields=["failures", "locked_until"])
    fields = {"result": result, "request_id": getattr(request, "request_id", None)}
    if account is not None:
        profile = AccountProfile.objects.filter(user_id=account.pk).first()
        if profile:
            fields["player_pseudonym"] = str(profile.player_pseudonym)
    LOGIN_RESULTS.labels(result).inc()
    LOGGER.info("login_result", extra={"component": "auth", "fields": fields})
    return user
