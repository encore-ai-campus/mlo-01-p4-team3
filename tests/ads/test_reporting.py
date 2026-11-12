"""D-12-04 운영 조회의 금액·귀속일·권한·무변경 검증."""
from datetime import date
from unittest import TestCase as UnitTestCase

from ads.domain.reporting import summarize_day


class ReportCalculations(UnitTestCase):
    """원장 합계를 표시 값으로 계산하며 입력과 과금 상태를 바꾸지 않는다."""

    def test_available_budget_and_ctr_preserve_integer_money(self):
        row = dict(daily_budget_mw=10**18, reserved_mw=30000, charged_mw=80000,
                   refunded_mw=-40000, committed_mw=70000, impressions=4, clicks=1)
        result = summarize_day(row)
        self.assertEqual(result["net_mw"], 40000)
        self.assertEqual(result["available_mw"], 10**18 - 70000)
        self.assertEqual(result["ctr_percent"], 25)
        self.assertTrue(result["balanced"])
        self.assertNotIn("available_mw", row)

    def test_empty_ctr_deleted_campaign_and_inconsistent_balance(self):
        result = summarize_day(dict(daily_budget_mw=None, reserved_mw=30000, charged_mw=0,
                                    refunded_mw=0, committed_mw=0, impressions=0, clicks=0))
        self.assertIsNone(result["available_mw"])
        self.assertIsNone(result["ctr_percent"])
        self.assertFalse(result["balanced"])


from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from ads.app.reporting import build_report
from ads.infra.reporting import read_report_rows
from ads.web.models import AdDailyBudget, AdLedgerEntry, AdStoredRequest, Campaign


class OperationsReport(TestCase):
    """실제 조회 경로에서 사람·합성 분리와 고아 예약까지 대조한다."""

    def setUp(self):
        self.day = date(2026, 11, 12)
        self.campaign = Campaign.objects.create(campaign_id="report", advertiser_id="adv", name="조회 광고",
            slots=["lobby_banner"], billing="cpc", max_bid_mw=100000, daily_budget_mw=1000000,
            start_date="2026-11-01", end_date="2026-12-01")
        self.budget = AdDailyBudget.objects.create(campaign_id="report", attribution_day=self.day,
                                                   committed_mw=70000)
        for key, amount, state, kind in [("reserved", 30000, "reserved", "charge"),
                                         ("charge", 80000, "confirmed", "charge"),
                                         ("refund", -40000, "confirmed", "refund"),
                                         ("released", 90000, "released", "charge")]:
            AdLedgerEntry.objects.create(charge_id=key, budget=self.budget, amount_mw=amount, state=state,
                kind=kind, source="human", slot="lobby_banner", billing="cpc", price_mw=abs(amount))
        # 11/11 15:00 UTC는 서울 11/12. 요청·클릭 수신일 대신 원 serve 귀속일을 쓴다.
        AdStoredRequest.objects.create(request_id="observed", session_id="private", serve_id="serve-observed",
            request="request-digest", impressed=True, clicked=True,
            serve_data={"load_test": False, "record": {
                "campaign_id": "report", "source": "human", "served_at_ms": 1794409200000}})
        self.operator = get_user_model().objects.create_superuser("report_operator", password="local-check-strong")
        self.client.force_login(self.operator)
        self.url = reverse("admin:ads_web_campaign_operations")
        self.filters = {"start_day": "2026-11-12", "end_day": "2026-11-12", "source": "human"}

    def report(self, source="human"):
        return build_report(read_report_rows, self.day, self.day, source)

    def test_report_includes_orphan_reservations_and_matches_committed(self):
        row = self.report()[0]
        self.assertEqual((row["reserved_mw"], row["charged_mw"], row["refunded_mw"]), (30000, 80000, -40000))
        self.assertEqual((row["available_mw"], row["impressions"], row["clicks"]), (930000, 1, 1))
        self.assertEqual(row["available_mw"], self.campaign.daily_budget_mw - self.budget.committed_mw)
        response = self.client.get(self.url, self.filters)
        self.assertContains(response, "조회 광고")
        self.assertContains(response, "고아 예약")
        self.assertContains(response, "00:40")
        self.assertNotContains(response, "serve-observed")
        self.assertNotContains(response, "private")
        self.budget.refresh_from_db()
        self.assertEqual(self.budget.committed_mw, 70000)
        self.assertEqual(AdLedgerEntry.objects.count(), 4)

    def test_source_date_and_campaign_filters_and_deleted_campaign(self):
        Campaign.objects.create(campaign_id="syn:report", advertiser_id="adv", name="합성 광고",
            slots=["lobby_banner"], billing="cpc", max_bid_mw=100000, daily_budget_mw=2000000,
            start_date="2026-11-01", end_date="2026-12-01")
        self.assertEqual([row["campaign_id"] for row in self.report("synthetic")], ["syn:report"])
        self.assertEqual([row["campaign_id"] for row in self.report()], ["report"])
        previous = build_report(read_report_rows, date(2026, 11, 11), date(2026, 11, 11), "human")
        self.assertEqual(previous[0]["impressions"], 0)
        self.assertEqual(build_report(read_report_rows, self.day, self.day, "human", "missing"), [])
        self.campaign.delete()
        self.assertIsNone(self.report()[0]["available_mw"])

    def test_load_test_and_other_source_do_not_increment_human_counts(self):
        for key, source, load_test in [("load", "human", True), ("other", "synthetic", False)]:
            AdStoredRequest.objects.create(request_id=key, session_id="private", request="request-digest",
                impressed=True, clicked=True, serve_data={"load_test": load_test, "record": {
                    "campaign_id": "report", "source": source, "served_at_ms": 1794409200000}})
        row = self.report()[0]
        self.assertEqual((row["impressions"], row["clicks"]), (1, 1))

    def test_permissions_invalid_filters_post_and_csrf(self):
        self.assertEqual(Client().get(self.url).status_code, 302)
        staff = get_user_model().objects.create_user("unprivileged", is_staff=True)
        client = Client()
        client.force_login(staff)
        self.assertEqual(client.get(self.url).status_code, 403)
        response = self.client.get(self.url, self.filters | {"source": "unknown"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        response = self.client.get(self.url, self.filters | {"end_day": "2026-11-11"})
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(self.client.post(self.url, self.filters).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.operator)
        self.assertEqual(csrf.post(self.url, self.filters).status_code, 403)
