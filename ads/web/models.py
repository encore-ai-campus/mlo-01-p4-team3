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


class AdDailyBudget(models.Model):
    """캠페인·귀속일별 예약 중 금액과 확정 순액의 합."""

    campaign_id = models.CharField(max_length=100, db_collation="utf8mb4_0900_bin")
    attribution_day = models.DateField()
    committed_mw = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=["campaign_id", "attribution_day"], name="ad_budget_campaign_day")]


class AdLedgerEntry(models.Model):
    """광고 원장 행. 환입은 원래 과금 행과 같은 귀속일·지면에 추가한다."""

    charge_id = models.CharField(max_length=160, primary_key=True, db_collation="utf8mb4_0900_bin")
    budget = models.ForeignKey(AdDailyBudget, on_delete=models.PROTECT)
    amount_mw = models.BigIntegerField()
    state = models.CharField(max_length=10)
    source_event_id = models.CharField(max_length=220, null=True, db_collation="utf8mb4_0900_bin")
    source = models.CharField(max_length=10)
    kind = models.CharField(max_length=6)
    slot = models.CharField(max_length=20)
    billing = models.CharField(max_length=3)
    price_mw = models.PositiveBigIntegerField()
    release_after_ms = models.PositiveBigIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["state", "release_after_ms"], name="ad_ledger_expiry")]
        constraints = [models.CheckConstraint(
            condition=(models.Q(kind="charge", amount_mw__gt=0, state__in=["reserved", "confirmed", "released"])
                       | models.Q(kind="refund", amount_mw__lt=0, state="confirmed")),
            name="ad_ledger_signed_state")]


class AdOutbox(models.Model):
    """발행 대기 이벤트와 같은 트랜잭션의 원장 상태 변경 기록."""

    event_id = models.CharField(max_length=240, unique=True, db_collation="utf8mb4_0900_bin")
    topic = models.CharField(max_length=32, blank=True)
    message_key = models.CharField(max_length=160, null=True, db_collation="utf8mb4_0900_bin")
    payload = models.JSONField()
    published_at = models.DateTimeField(null=True)

    class Meta:
        indexes = [models.Index(fields=["published_at", "id"], name="ad_outbox_pending")]


class AdSession(models.Model):
    """같은 세션의 빈도·빠른 클릭 판정에 쓰는 잠금 행."""

    session_id = models.CharField(max_length=128, primary_key=True, db_collation="utf8mb4_0900_bin")
    last_click_ms = models.PositiveBigIntegerField(null=True)


class AdStoredRequest(models.Model):
    """요청 재생용 응답과 serve 당시의 과금·판정 상태."""

    request_id = models.CharField(max_length=128, primary_key=True, db_collation="utf8mb4_0900_bin")
    request = models.JSONField()
    response = models.JSONField(null=True)
    serve_id = models.CharField(max_length=128, unique=True, null=True, db_collation="utf8mb4_0900_bin")
    serve_data = models.JSONField(null=True)
    session_id = models.CharField(max_length=128, db_collation="utf8mb4_0900_bin")
    creative_id = models.CharField(max_length=100, null=True, db_collation="utf8mb4_0900_bin")
    candidates = models.JSONField(default=list)
    impressed = models.BooleanField(default=False)
    clicked = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["session_id", "creative_id"], name="ad_serve_frequency")]


class AdReceipt(models.Model):
    """판정 재생용 키. 토큰은 해시로만 참조한다."""

    digest = models.CharField(max_length=64, primary_key=True)
    first_received_ms = models.PositiveBigIntegerField()
    result = models.JSONField(null=True)
    pending_input = models.JSONField(null=True)
    pending_after_ms = models.PositiveBigIntegerField(default=0, db_index=True)


class AdOptimizationVersion(models.Model):
    """Immutable complete CTR/pacing snapshots; newest as-of wins, replay never promotes."""

    kind = models.CharField(max_length=6)
    version = models.CharField(max_length=80, unique=True, db_collation="utf8mb4_0900_bin")
    source_version = models.CharField(max_length=200, db_collation="utf8mb4_0900_bin")
    as_of_ms = models.PositiveBigIntegerField()
    payload = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "source_version", "as_of_ms"],
                                                name="ad_optimization_revision")]
        indexes = [models.Index(fields=["kind", "as_of_ms"], name="ad_optimization_latest")]
