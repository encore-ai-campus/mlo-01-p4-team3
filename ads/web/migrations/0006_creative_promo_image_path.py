"""소재 이미지 기본 경로에 새 공개 경로 `/static/promo/`를 허용한다(옛 `/static/ads/`도 전환 동안 허용). DB 변경 없음."""
import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads_web", "0005_campaign_bid_settings")]
    operations = [
        migrations.AlterField("creative", "image_path", models.CharField(max_length=300, validators=[
            django.core.validators.RegexValidator("^/static/(promo|ads)/[A-Za-z0-9_/-]+\\.[A-Za-z0-9]+$",
                                                  "사이트 안 광고 이미지 기본 경로를 입력하세요.")])),
    ]
