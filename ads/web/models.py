"""admin에서 관리하는 캠페인·소재. 금액 단위는 정수 밀리원."""
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from ads.domain import model as domain
from ads.domain.rules import validate_campaign_budget

_id = RegexValidator(r"^[A-Za-z0-9_:-]+$", "ID는 영문·숫자·_·:·-만 사용합니다.")


class Campaign(models.Model):
    """예산·타기팅·상태를 포함하는 광고 캠페인."""
    campaign_id = models.CharField(primary_key=True, max_length=100, validators=[_id])
    advertiser_id = models.CharField(max_length=100, validators=[_id])
    name = models.CharField(max_length=200)
    slots = models.JSONField()
    billing = models.CharField(max_length=3, choices=[(v.value, v.value) for v in domain.BillingType])
    max_bid_mw = models.PositiveBigIntegerField()
    daily_budget_mw = models.PositiveBigIntegerField(help_text="1원 = 1000밀리원")
    targeting = models.JSONField(default=dict, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, default="active",
                             choices=[(v.value, v.value) for v in domain.CampaignStatus])

    def clean(self):
        """admin 입력의 타기팅 타입·기간·허용 지면·1건 최대 청구액을 검사한다."""
        super().clean()
        if not isinstance(self.slots, list) or not self.slots or any(
                slot not in {v.value for v in domain.AdSlot} for slot in self.slots):
            raise ValidationError({"slots": "지원하는 지면의 JSON 목록이 필요합니다."})
        t = self.targeting
        if not isinstance(t, dict) or set(t) - {"preset_ids", "outcomes", "returning", "hours"}:
            raise ValidationError({"targeting": "타기팅 키를 확인하세요."})
        if (not isinstance(t.get("preset_ids", []), list)
            or any(not isinstance(v, str) for v in t.get("preset_ids", []))
            or not isinstance(t.get("outcomes", []), list)
            or any(v not in ("win", "lose") for v in t.get("outcomes", []))
            or (t.get("returning") is not None and type(t["returning"]) is not bool)
            or not isinstance(t.get("hours", []), list)
            or any(type(h) is not int or not 0 <= h <= 23 for h in t.get("hours", []))):
            raise ValidationError({"targeting": "타기팅 값의 타입·범위를 확인하세요."})
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "시작일 이후여야 합니다."})
        if (self.billing in {v.value for v in domain.BillingType}
                and self.max_bid_mw is not None and self.daily_budget_mw is not None):
            try:
                validate_campaign_budget(domain.BillingType(self.billing), self.max_bid_mw, self.daily_budget_mw)
            except ValueError as exc:
                raise ValidationError({"daily_budget_mw": str(exc)}) from exc

    def to_domain(self):
        """ORM 행과 소재를 표준 라이브러리 DTO로 변환한다."""
        return domain.Campaign(self.pk, self.advertiser_id, self.name,
            [domain.AdSlot(s) for s in self.slots], domain.BillingType(self.billing),
            self.max_bid_mw, self.daily_budget_mw,
            domain.Targeting(**{**self.targeting,
                "outcomes": [domain.GameOutcome(o) for o in self.targeting.get("outcomes", [])]}),
            self.start_date.isoformat(), self.end_date.isoformat(), domain.CampaignStatus(self.status),
            [cr.to_domain() for cr in self.creatives.all()])

    def __str__(self):
        return self.name


class Creative(models.Model):
    """이미지는 데이터 경로로 참조하고 랜딩은 ID로만 만든다."""
    creative_id = models.CharField(primary_key=True, max_length=100, validators=[_id])
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="creatives")
    title = models.CharField(max_length=200)
    body = models.TextField()
    image_path = models.CharField(max_length=300,
        validators=[RegexValidator(r"^/static/ads/[A-Za-z0-9_/-]+\.[A-Za-z0-9]+$",
                                   "사이트 안 광고 이미지 기본 경로를 입력하세요.")])

    def clean(self):
        """합성 캠페인과 소재의 접두어 격리를 검사한다."""
        super().clean()
        if self.campaign_id and self.pk.startswith("syn:") != self.campaign_id.startswith("syn:"):
            raise ValidationError("합성 캠페인과 소재는 모두 syn: 접두어를 사용합니다.")

    def to_domain(self):
        """랜딩 URL을 외부 입력 없이 소재 ID에서 계산한다."""
        return domain.Creative(self.pk, self.campaign_id, self.title, self.body, self.image_path,
                               "/ads/landing/" + self.pk)

    def __str__(self):
        return self.title
