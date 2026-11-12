"""D-12-04 관리자 등록부터 실제 MySQL 판정·원장·조회까지 검증한다."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from ads.app.expire_reservations import expire_once
from ads.domain.model import AdSlot, SlotPolicy
from ads.domain.rules import TOKEN_TTL_MS, GRACE_MS
from ads.project.composition import get_service
from ads.web import views
from ads.web.models import AdLedgerEntry, AdOutbox


@override_settings(ADS_RESERVE_DB="default")
class AdminBillingFlow(TestCase):
    """관리자 저장·수정·중지와 과금 조회가 같은 원장에 연결된다."""

    def setUp(self):
        self.now = int(datetime(2026, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)
        self.serial = 0
        self.client.force_login(get_user_model().objects.create_superuser("billing_operator"))
        provider = patch.object(views, "get_service", side_effect=self.service)
        provider.start()
        self.addCleanup(provider.stop)

    def service(self):
        """시각과 순위 CTR을 고정하고 MySQL 저장·경매·과금 구현을 사용한다."""
        service = get_service()
        service.clock = lambda: self.now
        service.rank_ctr = lambda *args: .02
        return service

    def save_campaign(self, billing="cpc", status="active", budget=1000000, edit=False):
        """실제 관리자 POST로 부적 소재를 캠페인과 함께 등록한다."""
        suffix = "local/change/" if edit else "add/"
        response = self.client.post("/admin/ads_web/campaign/" + suffix, dict(
            campaign_id="local", advertiser_id="adv_shrine", name="서낭당 부적", status=status,
            slots='["lobby_banner", "result_banner", "review_side"]', billing=billing,
            max_bid_mw=100000 if billing == "cpc" else 1000000, daily_budget_mw=budget,
            targeting="{}", start_date="2026-11-01", end_date="2026-12-31",
            **{"creatives-TOTAL_FORMS": "1", "creatives-INITIAL_FORMS": "1" if edit else "0",
               "creatives-MIN_NUM_FORMS": "0", "creatives-MAX_NUM_FORMS": "1000",
               "creatives-0-creative_id": "local_bujeok", "creatives-0-campaign": "local" if edit else "",
               "creatives-0-title": "마을 부적", "creatives-0-body": "가상 광고",
               "creatives-0-image_path": "/static/promo/bujeok.png"}))
        self.assertEqual(response.status_code, 302, response.content)

    def serve(self, billing="cpc", slot="lobby_banner"):
        """2등 가격으로 CPC 40원 또는 CPM 1000원인 경매를 실행한다."""
        self.serial += 1
        reserve = 800000 if billing == "cpc" else 1000000
        policies = {key: SlotPolicy(key, reserve, .02) for key in AdSlot}
        with patch("ads.project.composition._policies", return_value=policies):
            response = self.client.post("/ads/v1/serve", json.dumps(dict(
                request_id=f"admin-flow-{self.serial}", session_pseudonym=f"player-{self.serial}",
                slot=slot, context={})), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def report(self):
        """운영 조회 화면이 반환한 캠페인·귀속일 행을 읽는다."""
        response = self.client.get("/admin/ads_web/campaign/operations/", {
            "start_day": "2026-11-12", "end_day": "2026-11-12", "source": "human", "campaign_id": "local"})
        self.assertEqual(response.status_code, 200)
        return response.context["rows"][0]

    def test_cpc_40_won_click_refund_and_edit_are_visible(self):
        self.save_campaign()
        ad = self.serve()
        self.assertTrue(ad["filled"])
        self.assertEqual(self.report()["reserved_mw"], 40000)
        payload = dict(token=ad["serve_token"], visible_ratio=.5, visible_ms=1000, occurred_at_ms=self.now)
        self.assertEqual(self.client.post("/ads/v1/impression", json.dumps(payload), content_type="application/json").status_code, 204)
        self.assertEqual((self.report()["impressions"], self.report()["charged_mw"]), (1, 0))
        click = self.client.get("/ads/v1/click", {"t": ad["serve_token"]})
        self.assertEqual(click.status_code, 302)
        self.assertContains(self.client.get(click["Location"]), "마을 부적")
        row = self.report()
        self.assertEqual((row["reserved_mw"], row["charged_mw"], row["clicks"]), (0, 40000, 1))
        self.assertEqual(row["available_mw"], 960000)
        count = AdLedgerEntry.objects.count()
        self.client.get("/ads/v1/click", {"t": ad["serve_token"]})
        self.client.post("/ads/v1/impression", json.dumps(payload), content_type="application/json")
        self.assertEqual(AdLedgerEntry.objects.count(), count)
        charge = AdLedgerEntry.objects.get(kind="charge")
        self.service().book.refund(charge.pk, "local-refund")
        self.assertEqual((self.report()["refunded_mw"], self.report()["net_mw"]), (-40000, 0))
        self.save_campaign(budget=2000000, edit=True)
        self.assertEqual(self.report()["available_mw"], 2000000)
        self.save_campaign(status="paused", budget=2000000, edit=True)
        self.assertFalse(self.serve()["filled"])

    def test_cpm_impression_and_click_charge_once_and_expiry_releases(self):
        self.save_campaign(billing="cpm")
        ad = self.serve("cpm", "review_side")
        self.assertTrue(ad["filled"])
        self.assertEqual(ad["creative"]["image_path"], "/static/promo/bujeok__box.png")
        self.assertEqual(self.report()["reserved_mw"], 1000)
        self.client.get("/ads/v1/click", {"t": ad["serve_token"]})
        row = self.report()
        self.assertEqual((row["charged_mw"], row["impressions"], row["clicks"]), (1000, 1, 1))
        self.client.post("/ads/v1/impression", json.dumps(dict(token=ad["serve_token"], visible_ratio=1,
            visible_ms=1500, occurred_at_ms=self.now)), content_type="application/json")
        self.assertEqual(self.report()["charged_mw"], 1000)
        self.assertEqual(self.report()["impressions"], 1)
        self.assertTrue(self.serve("cpm")["filled"])
        self.assertEqual(self.report()["reserved_mw"], 1000)
        self.now += TOKEN_TTL_MS + GRACE_MS + 1
        service = self.service()
        with patch("ads.app.expire_reservations.get_service", return_value=service), \
                patch("ads.app.expire_reservations.time.time_ns", return_value=self.now * 1000000):
            expire_once(service.book)
        self.assertEqual(self.report()["reserved_mw"], 0)
        self.assertEqual(self.report()["charged_mw"], 1000)

    def test_budget_exclusion_and_serve_id_link(self):
        self.save_campaign(billing="cpm", budget=1000)
        self.assertTrue(self.serve("cpm")["filled"])
        self.assertFalse(self.serve("cpm")["filled"])
        row = self.report()
        self.assertEqual(row["available_mw"], 0)
        charge = AdLedgerEntry.objects.get(kind="charge")
        event = AdOutbox.objects.get(topic="ad.serve", payload__filled=True)
        self.assertEqual(event.payload["reserved_charge_id"], charge.pk)
        self.assertEqual(event.payload["serve_id"] + ":impression", charge.pk)
        self.assertTrue(row["balanced"])
