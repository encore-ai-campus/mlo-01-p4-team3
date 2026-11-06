"""관리 화면에서 기본 사용자 문자열 대신 가명 프로필만 표시한다."""
from django.contrib import admin
from django.contrib.auth.models import User, Group
from .models import AccountProfile

for model in (User, Group):
    if admin.site.is_registered(model):
        admin.site.unregister(model)


@admin.register(AccountProfile)
class ProfileAdmin(admin.ModelAdmin):
    """로그인 ID·비밀번호를 노출하지 않는 읽기 전용 프로필 목록."""
    list_display = ("player_pseudonym", "consented_at")
    fields = readonly_fields = ("player_pseudonym", "consented_at")

    def has_add_permission(self, request):
        """계정 생성은 동의를 받는 가입 경로로 제한한다."""
        return False

    def has_change_permission(self, request, obj=None):
        """동의 시각·가명 ID를 관리자 화면에서 바꾸지 않는다."""
        return False

    def has_delete_permission(self, request, obj=None):
        """계정 삭제는 C의 삭제 절차에 맡긴다."""
        return False
