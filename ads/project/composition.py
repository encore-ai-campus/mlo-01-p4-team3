"""ORM 캠페인과 도메인 광고 서비스를 연결하는 구성 지점."""
import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from ads.app.serve import AdService
from ads.domain.model import AdSlot, SlotPolicy
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.mysql_store import MySQLAdStore
from ads.web.models import Campaign


def load_campaigns():
    """캐시 없이 캠페인과 소재를 읽어 admin 변경을 다음 요청에 반영한다."""
    return [row.to_domain() for row in Campaign.objects.prefetch_related("creatives").order_by("pk")]


def load_policies():
    """계약 픽스처의 지면 정책을 읽는다. 캠페인 이미지는 하드코딩하지 않는다."""
    data = json.loads((Path(settings.BASE_DIR) / "fixtures/ads/campaigns.json").read_text(encoding="utf-8"))
    return {AdSlot(row["slot"]): SlotPolicy(**{**row, "slot": AdSlot(row["slot"])})
            for row in data["slot_policies"]}


_policies = lru_cache(maxsize=1)(load_policies)


def get_service():
    """HTTP 요청의 임시 작업 상태를 만들고 공유 상태는 MySQL에서 읽는다."""
    return AdService(load_campaigns, _policies().copy(), settings.ADS_TOKEN_KEY.encode(),
                     book=MySQLLedger(), store=MySQLAdStore())
