"""MySQL 예산 원장. 예산 행 잠금과 원장·아웃박스 변경을 한 트랜잭션에 둔다."""
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from ads.domain.model import BillingType, ChargeState, LedgerRow
from ads.domain.rules import REFUND_SUFFIX, RELEASE_AFTER_MS, charge_id, reservation_amount
from ads.web.models import AdDailyBudget, AdLedgerEntry, AdOutbox
from ads.infra.timing import phase


def _dto(row):
    return LedgerRow(row.charge_id, row.budget.campaign_id, row.amount_mw, ChargeState(row.state),
                     row.budget.attribution_day.isoformat(), row.source_event_id, row.kind,
                     BillingType(row.billing), row.price_mw, row.release_after_ms)


def _audit(row, action):
    # 상태 변경은 로컬에 보존한다. 공개 Kafka 토픽은 광고 이벤트 계약의 세 가지뿐이다.
    AdOutbox.objects.create(event_id=f"ledger:{row.charge_id}:{action}", message_key=row.charge_id,
                            published_at=timezone.now(), payload={
                                "action": action, "charge_id": row.charge_id, "amount_mw": row.amount_mw,
                                "source_event_id": row.source_event_id,
                            })


class MySQLLedger:
    """정수 밀리원 원장. 동일 예산을 변경하는 모든 쓰기는 예산 행부터 잠근다."""

    def get(self, cid):
        """과금 또는 환입 행의 현재 스냅샷을 반환한다."""
        row = AdLedgerEntry.objects.select_related("budget").filter(pk=cid).first()
        return _dto(row) if row else None

    def committed_mw(self, campaign_id, day):
        """일 예산에서 예약과 확정 순액으로 점유 중인 금액."""
        return AdDailyBudget.objects.filter(campaign_id=campaign_id, attribution_day=day).values_list(
            "committed_mw", flat=True).first() or 0

    def can_reserve(self, campaign, day, amount_mw):
        """후보 필터용 조회. 실제 예산 상한 검사는 reserve에서 다시 수행한다."""
        with phase("budget_check"):
            return self.committed_mw(campaign.campaign_id, day) + amount_mw <= campaign.daily_budget_mw

    def reserve(self, campaign, serve):
        """실제 청구 금액을 예약한다. 중복 또는 일 예산 초과 시 False. 예산 행 잠금 대기를 포함해 잰다."""
        with phase("budget_reserve"):
            return self._reserve(campaign, serve)

    @transaction.atomic
    def _reserve(self, campaign, serve):
        amount = reservation_amount(campaign, serve)
        cid = charge_id(serve.serve_id, serve.billing)
        table = AdDailyBudget._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} (campaign_id, attribution_day, committed_mw) VALUES (%s, %s, 0) "
                "ON DUPLICATE KEY UPDATE id = id", [campaign.campaign_id, serve.attribution_day])
        budget = AdDailyBudget.objects.select_for_update().get(
            campaign_id=campaign.campaign_id, attribution_day=serve.attribution_day)
        if budget.committed_mw + amount > campaign.daily_budget_mw:
            return False
        try:
            with transaction.atomic():
                row = AdLedgerEntry.objects.create(
                    charge_id=cid, budget=budget, amount_mw=amount, state=ChargeState.RESERVED,
                    source=serve.source, kind="charge", slot=serve.slot, billing=serve.billing,
                    price_mw=serve.price_mw, release_after_ms=serve.served_at_ms + RELEASE_AFTER_MS)
        except IntegrityError as exc:
            if exc.__cause__.args[0] == 1062:
                return False
            raise
        budget.committed_mw += amount
        budget.save(update_fields=["committed_mw"])
        _audit(row, "reserve")
        return True

    def _locked_charge(self, cid):
        budget_id = AdLedgerEntry.objects.filter(pk=cid, kind="charge").values_list("budget_id", flat=True).first()
        if budget_id is None:
            return None
        budget = AdDailyBudget.objects.select_for_update().get(pk=budget_id)
        row = AdLedgerEntry.objects.select_for_update().get(pk=cid)
        row.budget = budget
        return row

    @transaction.atomic
    def confirm(self, cid, source_event_id):
        """예약 금액을 변경 없이 확정하며 재확정은 무효과다."""
        row = self._locked_charge(cid)
        if row is None or row.state != ChargeState.RESERVED:
            return False
        row.state, row.source_event_id = ChargeState.CONFIRMED, source_event_id
        row.save(update_fields=["state", "source_event_id"])
        _audit(row, "confirm")
        return True

    def release_expired(self, now_ms, limit=250):
        """해제 기한을 지난 예약을 최대 limit건 처리하고 변경된 ID를 반환한다."""
        ids = list(AdLedgerEntry.objects.filter(state=ChargeState.RESERVED, release_after_ms__lt=now_ms)
                   .order_by("release_after_ms", "pk").values_list("pk", flat=True)[:limit])
        return [cid for cid in ids if self._release(cid, now_ms)]

    @transaction.atomic
    def _release(self, cid, now_ms):
        row = self._locked_charge(cid)
        if row is None or row.state != ChargeState.RESERVED or row.release_after_ms >= now_ms:
            return False
        row.state = ChargeState.RELEASED
        row.save(update_fields=["state"])
        row.budget.committed_mw -= row.amount_mw
        row.budget.save(update_fields=["committed_mw"])
        _audit(row, "release")
        return True

    @transaction.atomic
    def refund(self, cid, reason_event_id, *, source_event_id=None, source=None):
        """원과금의 확인된 재판정만 같은 귀속일에 한 번 환입한다."""
        original = self._locked_charge(cid)
        if original is None or original.state != ChargeState.CONFIRMED:
            return None
        if source_event_id is not None and original.source_event_id != source_event_id:
            return None
        if source is not None and original.source != source:
            return None
        rid = cid + REFUND_SUFFIX
        if AdLedgerEntry.objects.filter(pk=rid).exists():
            return None
        row = AdLedgerEntry.objects.create(
            charge_id=rid, budget=original.budget, amount_mw=-original.amount_mw, state=ChargeState.CONFIRMED,
            source_event_id=reason_event_id, source=original.source, kind="refund",
            slot=original.slot, billing=original.billing, price_mw=original.price_mw)
        row.budget.committed_mw -= original.amount_mw
        row.budget.save(update_fields=["committed_mw"])
        _audit(row, "refund")
        return _dto(row)

    def confirmed_amounts(self):
        """대사용 확정 행과 환입 행의 금액을 반환한다."""
        return dict(AdLedgerEntry.objects.filter(state=ChargeState.CONFIRMED).values_list("charge_id", "amount_mw"))


def export_ledger_rows():
    """원장 전체를 gold `--ledger` 행 형식으로 읽는다(인증값·토큰 없음, charge_id 순)."""
    return [dict(charge_id=r.charge_id, amount_mw=r.amount_mw, state=r.state, kind=r.kind,
                 source_event_id=r.source_event_id, source=r.source, campaign_id=r.budget.campaign_id,
                 attribution_day=r.budget.attribution_day.isoformat(), slot=r.slot)
            for r in AdLedgerEntry.objects.select_related("budget").order_by("pk")]


def campaign_daily_budgets():
    """캠페인별 현재 일 예산(밀리원)."""
    from ads.web.models import Campaign
    return dict(Campaign.objects.values_list("pk", "daily_budget_mw"))
