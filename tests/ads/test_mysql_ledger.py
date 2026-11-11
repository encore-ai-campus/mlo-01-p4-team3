"""D-06-02 실제 MySQL의 회계·경합·트랜잭션 검증."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import TransactionTestCase

from ads.domain.model import AdContext, AdRequest, AdSlot, BillingType, ChargeState
from ads.domain.rules import RELEASE_AFTER_MS, charge_id, reconcile
from ads.infra.mysql_ledger import MySQLLedger
from ads.web.models import AdDailyBudget, AdLedgerEntry, AdOutbox, AdStoredRequest, Campaign, Creative
from tests.contract import test_ads as ads_contract
from tests.contract.test_ads import _camp, _serve, load_json


class _LedgerRows:
    def __init__(self, book):
        self.book = book

    def __getitem__(self, cid):
        row = self.book.get(cid)
        if row is None:
            raise KeyError(cid)
        return row

    def items(self):
        return [(cid, self[cid]) for cid in AdLedgerEntry.objects.values_list("pk", flat=True)]


class _ContractLedgerMixin:
    def setUp(self):
        super().setUp()
        book = MySQLLedger()
        # 기존 계약 시험의 메모리 dict 조회 문법만 DB 조회로 바꾼다.
        book.rows = _LedgerRows(book)
        patcher = patch("ads.domain.rules.BudgetBook", return_value=book)
        patcher.start()
        self.addCleanup(patcher.stop)


class MySQLBudgetContract(_ContractLedgerMixin, ads_contract.Budget, TransactionTestCase):
    """원본 Budget 시험을 수정 없이 MySQL 원장에 대해 실행한다."""


class MySQLReviewContract(_ContractLedgerMixin, ads_contract.AdsReviewRegressions, TransactionTestCase):
    """원본 AdsReviewRegressions를 수정 없이 MySQL 원장에 대해 실행한다."""


class MySQLLedgerTests(TransactionTestCase):
    """메모리 기준과 같은 회계 시나리오를 실제 InnoDB에서 실행한다."""

    def test_replays_contract_budget_fixtures(self):
        book = MySQLLedger()
        case, matching = load_json("fixtures/ads/budget_cases.json")
        billing = BillingType(case["billing"])
        campaign = _camp(case["daily_budget_mw"], billing)
        for step in case["steps"]:
            op = step["op"]
            if op == "release":
                self.assertEqual(book.release_expired(step["now_ms"]), step["expect_released"])
                continue
            cid = charge_id(step["serve"], billing)
            if op == "reserve":
                record = _serve(step["serve"], billing, case["price_mw"], step["at_ms"])
                self.assertEqual(book.reserve(campaign, record), step["expect"])
            elif op == "confirm":
                self.assertEqual(book.confirm(cid, step["event"]), step["expect"])
            elif op == "refund":
                self.assertEqual(book.refund(cid, step["event"]).amount_mw, step["expect_amount_mw"])
        self.assertEqual(book.committed_mw("cp", "2026-11-06"), case["expect_committed_mw"])
        self.assertEqual(reconcile(matching["ledger"], matching["delta"]), matching["expect"])

    def test_cpm_dates_and_slots_refund_to_zero(self):
        book = MySQLLedger()
        campaign = _camp(10_000, BillingType.CPM)
        first = _serve('first', BillingType.CPM, 100_999)
        second = _serve('second', BillingType.CPM, 100_999)
        second.attribution_day, second.slot = '2026-11-07', AdSlot.REVIEW_SIDE
        first_id = charge_id(first.serve_id, first.billing)
        second_id = charge_id(second.serve_id, second.billing)
        for record, cid in ((first, first_id), (second, second_id)):
            self.assertTrue(book.reserve(campaign, record))
            self.assertEqual(book.get(cid).amount_mw, 100)
            self.assertTrue(book.confirm(cid, record.serve_id))
            self.assertFalse(book.confirm(cid, 'repeat'))
        row = book.refund(first_id, 'review', source_event_id='first', source='human')
        self.assertEqual((row.amount_mw, row.attribution_day), (-100, first.attribution_day))
        self.assertIsNone(book.refund(first_id, 'again'))
        self.assertIsNone(book.refund(row.charge_id, 'again'))
        self.assertEqual(book.get(first_id).state, ChargeState.CONFIRMED)
        self.assertEqual(book.committed_mw('cp', first.attribution_day), 0)
        self.assertEqual(book.committed_mw('cp', second.attribution_day), 100)
        slot_totals = dict(AdLedgerEntry.objects.values("slot").annotate(total=Sum("amount_mw"))
                           .values_list("slot", "total"))
        self.assertEqual(slot_totals, {first.slot: 0, second.slot: 100})
        self.assertFalse(any(reconcile(book.confirmed_amounts(), {second_id: [100]}).values()))

    def test_storage_rejects_invalid_charges(self):
        cpm = _camp(10000, BillingType.CPM)
        cpc = _camp(10000, BillingType.CPC)
        for campaign, price in ((cpm, 99_999), (cpc, 0), (cpc, -1), (cpc, cpc.max_bid_mw + 1)):
            with self.assertRaises(ValueError):
                MySQLLedger().reserve(campaign, _serve('invalid', campaign.billing, price))

    def test_one_hundred_concurrent_reservations_and_duplicate_refunds(self):
        campaign = _camp(100, BillingType.CPC)

        def reserve(i):
            try:
                record = _serve(f's{i}', BillingType.CPC, 10)
                return record.serve_id if MySQLLedger().reserve(campaign, record) else None
            finally:
                connections.close_all()

        def refund(i):
            try:
                return MySQLLedger().refund(cid, f'rj{i}') is not None
            finally:
                connections.close_all()

        book = MySQLLedger()
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(100)))
            accepted = [sid for sid in results if sid]
            self.assertEqual(len(accepted), 10)
            cid = charge_id(accepted[0], BillingType.CPC)
            self.assertTrue(book.confirm(cid, 'charged'))
            committed = book.committed_mw('cp', '2026-11-06')
            self.assertEqual(committed, 100)
            refunded = sum(pool.map(refund, range(24)))
            self.assertEqual(refunded, 1)
        self.assertEqual(book.committed_mw('cp', '2026-11-06'), 90)
        print(json.dumps({"reserve_attempts": len(results), "reserve_accepted": len(accepted),
                          "committed_mw": committed, "refund_concurrent_rows": refunded}))

    def test_rollback_and_expiry_from_a_new_instance(self):
        campaign = _camp(100, BillingType.CPC)
        record = _serve('s', BillingType.CPC, 10)
        cid = charge_id(record.serve_id, record.billing)
        book = MySQLLedger()
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.assertTrue(book.reserve(campaign, record))
            raise RuntimeError('abort transaction')
        self.assertIsNone(book.get(cid))
        self.assertEqual(book.committed_mw('cp', record.attribution_day), 0)
        with patch('ads.infra.mysql_ledger._audit', side_effect=RuntimeError('outbox unavailable')):
            with self.assertRaises(RuntimeError):
                book.reserve(campaign, record)
        self.assertIsNone(book.get(cid))
        self.assertTrue(book.reserve(campaign, record))
        deadline = record.served_at_ms + RELEASE_AFTER_MS
        self.assertEqual(MySQLLedger().release_expired(deadline), [])
        self.assertEqual(MySQLLedger().release_expired(deadline + 1), [cid])
        self.assertFalse(book.confirm(cid, 'late'))
        self.assertEqual(book.committed_mw('cp', record.attribution_day), 0)


class ReserveConnectionTests(TransactionTestCase):
    """11/11 사용자 결정: serve의 예산 예약은 별도 연결의 짧은 트랜잭션으로 먼저 커밋한다."""

    databases = {"default", "ads_reserve"}

    def test_reservation_commits_apart_and_orphan_expires(self):
        campaign = _camp(100, BillingType.CPC)
        record = _serve('split', BillingType.CPC, 10)
        cid = charge_id(record.serve_id, record.billing)
        book = MySQLLedger(reserve_using="ads_reserve")
        with self.assertRaises(RuntimeError), transaction.atomic():
            self.assertTrue(book.reserve(campaign, record))
            # 응답 트랜잭션이 열려 있어도 예산 행 잠금은 이미 풀려 있다(NOWAIT가 실패하지 않음).
            AdDailyBudget.objects.select_for_update(nowait=True).get(
                campaign_id='cp', attribution_day=record.attribution_day)
            self.assertEqual(book.get(cid).state, ChargeState.RESERVED)
            raise RuntimeError('serve transaction fails after reservation')
        self.assertEqual(book.get(cid).state, ChargeState.RESERVED)
        self.assertEqual(book.committed_mw('cp', record.attribution_day), 10)
        self.assertTrue(AdOutbox.objects.filter(event_id=f"ledger:{cid}:reserve").exists())
        deadline = record.served_at_ms + RELEASE_AFTER_MS
        self.assertEqual(MySQLLedger().release_expired(deadline), [])
        self.assertEqual(MySQLLedger().release_expired(deadline + 1), [cid])
        self.assertEqual(book.committed_mw('cp', record.attribution_day), 0)

    def test_confirm_does_not_wait_for_budget_row(self):
        campaign = _camp(100, BillingType.CPC)
        record = _serve('confirm', BillingType.CPC, 10)
        cid = charge_id(record.serve_id, record.billing)
        book = MySQLLedger()
        self.assertTrue(book.reserve(campaign, record))
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION innodb_lock_wait_timeout = 1")
        try:
            with transaction.atomic(using="ads_reserve"):
                AdDailyBudget.objects.using("ads_reserve").select_for_update().get(
                    campaign_id='cp', attribution_day=record.attribution_day)
                self.assertTrue(book.confirm(cid, 'impression'))
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout = DEFAULT")
        self.assertEqual(book.get(cid).state, ChargeState.CONFIRMED)
        self.assertEqual(book.committed_mw('cp', record.attribution_day), 10)

    def _campaign(self):
        campaign = Campaign.objects.create(campaign_id="split", advertiser_id="adv", name="ad",
            slots=["lobby_banner"], billing="cpm", max_bid_mw=10_000_000, daily_budget_mw=10_000_000,
            start_date="2026-11-01", end_date="2026-12-01")
        Creative.objects.create(creative_id="split-cr", campaign=campaign, title="ad", body="ad",
                                image_path="/static/promo/example.png")

    def _service(self):
        from ads.project.composition import get_service
        from tests.ads.test_http import BASE_MS
        service = get_service()
        service.clock = lambda: BASE_MS
        return service

    def test_serve_reserves_on_separate_connection_and_failure_leaves_orphan(self):
        self._campaign()
        request = AdRequest("split-req", AdSlot.LOBBY_BANNER, "split-session", AdContext())
        with patch("ads.infra.mysql_store.enqueue", side_effect=RuntimeError("outbox down")):
            with self.assertRaises(RuntimeError):
                self._service().serve(request)
        orphan = AdLedgerEntry.objects.get()
        self.assertEqual(orphan.state, ChargeState.RESERVED)
        self.assertFalse(AdStoredRequest.objects.filter(pk="split-req").exists())
        out = self._service().serve(request)
        self.assertTrue(out.filled)
        rows = dict(AdLedgerEntry.objects.values_list("pk", "state"))
        self.assertEqual(len(rows), 2)
        serve_event = AdOutbox.objects.get(topic="ad.serve").payload
        self.assertEqual(serve_event["reserved_mw"], AdLedgerEntry.objects.get(pk=serve_event["reserved_charge_id"]).amount_mw)
        self.assertEqual(self._service().serve(request), out)
        self.assertEqual(AdLedgerEntry.objects.count(), 2)

    def test_concurrent_serves_keep_budget_total(self):
        self._campaign()

        def serve(i):
            try:
                return self._service().serve(
                    AdRequest(f"split-{i}", AdSlot.LOBBY_BANNER, f"split-session-{i % 5}", AdContext())).filled
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as pool:
            filled = list(pool.map(serve, range(40)))
        self.assertTrue(all(filled))
        reserved = AdLedgerEntry.objects.filter(state=ChargeState.RESERVED).aggregate(total=Sum("amount_mw"))["total"]
        self.assertEqual(AdLedgerEntry.objects.count(), 40)
        self.assertEqual(MySQLLedger().committed_mw("split", AdLedgerEntry.objects.first().budget.attribution_day),
                         reserved)
