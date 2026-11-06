"""이전 스키마에서 가입 요청 카운터만 제거한다."""
from django.db import migrations


class Migration(migrations.Migration):
    """계정 프로필, 로그인 잠금, 세션을 보존한다."""

    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.DeleteModel(name="SignupWindow")]
