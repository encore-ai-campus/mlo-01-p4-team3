"""격리 광고 DB에서 가명 삭제와 남은 캠페인 잔액을 검증한다."""
from datetime import date

from django.test import TestCase

from ads.domain.model import BillingType
from ads.domain.rules import REFUND_SUFFIX, charge_id
from ads.infra.mysql_store import _digest
from ads.infra.privacy_cleanup import collect_scope, purge_scope
from ads.web.models import AdDailyBudget, AdLedgerEntry, AdOutbox, AdReceipt, AdSession, AdStoredRequest


class PrivacyCleanupTests(TestCase):
    """대상과 비대상에 과금·환입·예약이 함께 있는 경우."""

    def test_target_removal_reconciles_shared_budget_and_is_repeatable(self):
        """대상 순액만 빼고 비대상 과금·요청과 잔액을 유지한다."""
        budget = AdDailyBudget.objects.create(campaign_id="campaign", attribution_day=date(2026, 11, 9),
                                              committed_mw=550)
        target = charge_id("target-serve", BillingType.CPC)
        refunded = charge_id("refunded-serve", BillingType.CPC)
        other = charge_id("other-serve", BillingType.CPC)
        for key, value, kind in [(target, 50, "charge"), (refunded, 100, "charge"),
                                 (refunded + REFUND_SUFFIX, -100, "refund"), (other, 500, "charge")]:
            AdLedgerEntry.objects.create(charge_id=key, budget=budget, amount_mw=value, kind=kind,
                                         state="reserved" if key == target else "confirmed",
                                         source="human", slot="lobby_banner", billing="cpc", price_mw=100)
        for serve, subject in (("target-serve", "target"), ("refunded-serve", "target"), ("other-serve", "other")):
            AdStoredRequest.objects.create(request_id=serve, serve_id=serve, request={}, session_id=subject)
        AdSession.objects.create(pk=_digest("target", False))
        AdSession.objects.create(pk=_digest("other", False))
        digest = "a" * 64
        AdReceipt.objects.create(pk=digest, first_received_ms=1)
        AdOutbox.objects.create(event_id=f"judged:{digest}", payload={"session_pseudonym": "target"})
        AdOutbox.objects.create(event_id="refund-audit", message_key=refunded + REFUND_SUFFIX, payload={})
        AdOutbox.objects.create(event_id="unrelated", payload={"session_pseudonym": "other"})
        scope = collect_scope(["target"])
        result = purge_scope(scope)
        self.assertEqual(result, {"ledger": 3, "outbox": 2, "receipts": 1,
                                  "requests": 2, "sessions": 1, "budgets_reconciled": 1})
        budget.refresh_from_db()
        self.assertEqual(budget.committed_mw, 500)
        self.assertTrue(AdLedgerEntry.objects.filter(pk=other).exists())
        self.assertTrue(AdOutbox.objects.filter(event_id="unrelated").exists())
        self.assertTrue(AdStoredRequest.objects.filter(pk="other-serve").exists())
        self.assertTrue(all(value == 0 for value in purge_scope(scope).values()))
