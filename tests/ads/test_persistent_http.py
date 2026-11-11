"""실제 MySQL 광고 HTTP에서 판정·원장·아웃박스 트랜잭션을 검증한다."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.test import Client, TestCase

from ads.domain.events import TOPIC_CLICK, TOPIC_IMPRESSION, TOPIC_SERVE
from ads.app.expire_reservations import expire_once
from ads.domain.model import AdSlot, ServeClaims
from ads.domain.rules import GRACE_MS, TOKEN_TTL_MS, ValidityState, judge_click, sign_token
from tests.contract.test_ads import _serve
from ads.domain.model import BillingType
from ads.project.composition import get_service
from ads.web import views
from ads.web.models import AdLedgerEntry, AdOutbox, AdStoredRequest, Campaign, Creative
from tests.ads.test_http import BASE_MS


class PersistentHTTPTests(TestCase):
    """HTTP 호출마다 새 서비스를 구성해 프로세스 메모리에 의존하지 않는지 확인한다."""

    def setUp(self):
        self.now = BASE_MS
        self.client = Client()
        campaign = Campaign.objects.create(campaign_id="cp", advertiser_id="adv", name="ad",
            slots=["lobby_banner"], billing="cpm", max_bid_mw=10_000_000, daily_budget_mw=10_000_000,
            start_date="2026-11-01", end_date="2026-12-01")
        Creative.objects.create(creative_id="creative", campaign=campaign, title="ad", body="ad",
                                image_path="/static/ads/example.png")
        patcher = patch.object(views, "get_service", side_effect=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def service(self):
        """기존 조립 지점에 고정 시험 시각만 주입한다."""
        service = get_service()
        service.clock = lambda: self.now
        return service

    def serve(self, rid, session="session"):
        """정상 API에 동일 요청을 재전송할 수 있게 한다."""
        return self.client.post("/ads/v1/serve", json.dumps(dict(request_id=rid, slot="lobby_banner",
            session_pseudonym=session, context={})), content_type="application/json")

    def click(self, signed, rid):
        """요청 ID를 보존한 내부 클릭을 보낸다."""
        return self.client.get("/ads/v1/click", {"t": signed}, HTTP_X_REQUEST_ID=rid)

    def test_replay_and_cpm_click_implied_charge(self):
        original = self.serve("r").json()
        self.assertTrue(original["filled"])
        self.assertEqual(self.serve("r").json(), original)
        self.assertEqual(self.serve("r", "changed").status_code, 409)
        signed = original["serve_token"]
        self.assertEqual(self.click(signed, "click1").status_code, 302)
        self.assertEqual(self.click(signed, "click1").status_code, 302)
        self.assertEqual(self.click(signed, "click2").status_code, 302)
        self.assertEqual(AdOutbox.objects.filter(topic=TOPIC_SERVE).count(), 1)
        impression = AdOutbox.objects.get(topic=TOPIC_IMPRESSION)
        click, duplicate = list(AdOutbox.objects.filter(topic=TOPIC_CLICK).order_by("pk"))
        ledger = AdLedgerEntry.objects.get()
        self.assertEqual(impression.event_id, ledger.source_event_id)
        self.assertEqual(impression.payload["charge_mw"], ledger.amount_mw)
        self.assertIsNone(click.payload["charge_id"])
        self.assertEqual(duplicate.payload["invalid_reason"], "duplicate")
        self.assertTrue(impression.payload["implied_by_click"])

    def test_expired_click_and_beacon_do_not_charge(self):
        original = self.serve("r").json()
        self.now += TOKEN_TTL_MS + 1
        self.click(original["serve_token"], "late-click")
        self.client.post("/ads/v1/impression", json.dumps({
            "token": original["serve_token"], "visible_ratio": 1.0, "visible_ms": 1200,
            "occurred_at_ms": BASE_MS}), content_type="application/json", HTTP_X_REQUEST_ID="late-impression")
        self.assertEqual(AdLedgerEntry.objects.get().state, "reserved")
        for topic in (TOPIC_CLICK, TOPIC_IMPRESSION):
            event = AdOutbox.objects.get(topic=topic).payload
            self.assertEqual((event["invalid_reason"], event["charge_mw"]), ("expired", 0))

    def test_serve_is_rolled_back_when_public_outbox_write_fails(self):
        with patch("ads.infra.mysql_store.enqueue", side_effect=RuntimeError("outbox write failed")):
            with self.assertRaises(RuntimeError):
                self.serve("r")
        self.assertFalse(AdLedgerEntry.objects.exists())
        self.assertFalse(AdOutbox.objects.exists())
        self.assertFalse(AdStoredRequest.objects.exists())
        self.assertTrue(self.serve("r").json()["filled"])

    def test_click_rolls_back_judgement_and_confirmation_with_outbox(self):
        signed = self.serve("r").json()["serve_token"]
        original_events = AdOutbox.objects.count()
        with patch("ads.infra.mysql_store.enqueue", side_effect=RuntimeError("outbox write failed")):
            with self.assertRaises(RuntimeError):
                self.click(signed, "click")
        self.assertEqual(AdLedgerEntry.objects.get().state, "reserved")
        self.assertFalse(AdStoredRequest.objects.get().clicked)
        self.assertEqual(AdOutbox.objects.count(), original_events)
        self.click(signed, "click")
        self.assertEqual(AdLedgerEntry.objects.get().state, "confirmed")
        self.assertTrue(AdStoredRequest.objects.get().clicked)

    def test_serve_attribution_matches_korean_aggregation_day(self):
        self.now = int(datetime(2026, 11, 6, 15, tzinfo=timezone.utc).timestamp() * 1000)
        self.assertTrue(self.serve("midnight").json()["filled"])
        self.assertEqual(AdLedgerEntry.objects.get().budget.attribution_day.isoformat(), "2026-11-07")

    def test_pending_replay_retains_first_receipt_through_expiry(self):
        signed = sign_token(ServeClaims("missing", "session", AdSlot.LOBBY_BANNER,
                            self.now - TOKEN_TTL_MS + 1, self.now + 1), self.service().key)
        self.click(signed, "retry")
        self.click(signed, "retry")
        self.assertEqual(AdOutbox.objects.filter(topic=TOPIC_CLICK).count(), 1)
        self.now += GRACE_MS
        self.click(signed, "retry")
        first, final = AdOutbox.objects.filter(topic=TOPIC_CLICK).order_by("pk")
        self.assertEqual((first.payload["status"], final.payload["invalid_reason"]), ("pending", "no_serve"))
        self.assertEqual(final.payload["source_event_id"], first.event_id)
        self.assertEqual(final.payload["received_at_ms"], first.payload["received_at_ms"])
        self.assertFalse(AdLedgerEntry.objects.exists())

    def test_pending_is_finalized_by_worker_without_http_retry(self):
        signed = sign_token(ServeClaims("missing", "session", AdSlot.LOBBY_BANNER,
                            self.now, self.now + 1), self.service().key)
        self.click(signed, "first")
        self.now += GRACE_MS
        with patch("ads.app.expire_reservations.get_service", side_effect=self.service):
            expire_once(self.service().book)
            expire_once(self.service().book)
        first, final = AdOutbox.objects.filter(topic=TOPIC_CLICK).order_by("pk")
        self.assertEqual(final.payload["invalid_reason"], "no_serve")
        self.assertEqual(final.payload["source_event_id"], first.event_id)

    def test_distinct_late_event_does_not_borrow_pending_arrival(self):
        signed = sign_token(ServeClaims("missing", "session", AdSlot.LOBBY_BANNER,
                            self.now, self.now + 1), self.service().key)
        self.click(signed, "first")
        self.now += GRACE_MS
        self.click(signed, "distinct-event")
        final = AdOutbox.objects.filter(topic=TOPIC_CLICK).order_by("pk").last()
        self.assertEqual(final.payload["invalid_reason"], "expired")

    def test_service_can_be_reused_for_multiple_serves(self):
        service = self.service()
        with patch.object(views, "get_service", return_value=service):
            first = self.serve("first").json()
            second = self.serve("second").json()
        self.assertNotEqual(first["serve_token"], second["serve_token"])
        self.assertEqual(AdLedgerEntry.objects.count(), 2)

    def test_out_of_order_clicks_preserve_session_watermark(self):
        records = [_serve(sid, BillingType.CPC, 100) for sid in ("a", "b", "c")]
        state = ValidityState(serves={record.serve_id: record for record in records})
        suspect = []
        for record, received in zip(records, (4000, 2000, 4500)):
            claims = ServeClaims(record.serve_id, "session", record.slot, 0, TOKEN_TTL_MS)
            suspect.append(judge_click(state, claims, None, received, received).suspect)
        self.assertEqual(suspect, [False, False, True])
        self.assertEqual(state.last_click_ms["session"], 4500)
