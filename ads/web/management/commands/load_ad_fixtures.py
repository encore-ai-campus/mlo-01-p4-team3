"""기존 광고 계약 픽스처를 admin 캠페인 테이블에 적재한다."""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from ads.web.models import Campaign, Creative


class Command(BaseCommand):
    """기존 캠페인은 유지하고 없는 픽스처 행만 만든다."""
    help = "계약 픽스처의 가상 캠페인·소재를 최초 적재(기존 admin 수정 유지)"

    @transaction.atomic
    def handle(self, *args, **options):
        """행 검증과 적재를 하나의 트랜잭션으로 실행한다."""
        data = json.loads((Path(settings.BASE_DIR) / "fixtures/ads/campaigns.json").read_text(encoding="utf-8"))
        created = 0
        for row in data["campaigns"]:
            defaults = {k: v for k, v in row.items() if k not in ("campaign_id", "creatives", "target_cpc_mw")}
            c, new = Campaign.objects.get_or_create(pk=row["campaign_id"], defaults=defaults)
            created += int(new)
            c.full_clean()
            for cr in row["creatives"]:
                obj, _ = Creative.objects.get_or_create(pk=cr["creative_id"], defaults=dict(
                    campaign=c, title=cr["title"], body=cr["body"], image_path=cr["image_path"]))
                obj.full_clean()
        self.stdout.write(f"campaigns_created={created}")
