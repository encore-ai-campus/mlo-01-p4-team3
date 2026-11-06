"""가명 계정 프로필과 로그인 잠금 상태."""
import uuid
from django.conf import settings
from django.db import models


class AccountProfile(models.Model):
    """기본 사용자와 불변 가명 ID·동의 시각의 연결."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paldo_profile")
    player_pseudonym = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    consented_at = models.DateTimeField(null=True)

    def __str__(self):
        return str(self.player_pseudonym)


class LoginLock(models.Model):
    """없는 계정도 동일하게 제한하기 위한 정규화 로그인 ID의 HMAC 키."""
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True)
