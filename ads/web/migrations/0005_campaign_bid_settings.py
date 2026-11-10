from django.core.validators import MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("ads_web", "0004_optimization_versions")]
    operations = [
        migrations.AddField("campaign", "target_cpc_mw", models.PositiveBigIntegerField(
            blank=True, null=True, validators=[MinValueValidator(1)])),
        migrations.AddField("campaign", "min_bid_mw", models.PositiveBigIntegerField(
            blank=True, null=True, validators=[MinValueValidator(1)], help_text="비우면 1밀리원")),
    ]
