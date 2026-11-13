"""D-08-02 persistent versions, reload and auction-time event pinning."""
import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from ads.infra.optimization import OptimizationCache, save_version
from ads.web.models import AdOptimizationVersion, AdOutbox, Campaign, Creative
from ads.project import composition
from tests.ads.test_persistent_http import PersistentHTTPTests


class VersionTests(TestCase):
    """Immutable, content-addressed versions survive process restart."""

    def test_replay_and_older_snapshot_do_not_advance_latest(self):
        row = save_version("ctr", [], 100, "gold-1")
        self.assertEqual(save_version("ctr", [], 100, "gold-1").pk, row.pk)
        save_version("ctr", [], 90, "older")
        cache = OptimizationCache(clock=lambda: 0)
        self.assertEqual(cache.get()["ctr"].version, row.version)
        self.assertEqual(AdOptimizationVersion.objects.count(), 2)

    def test_reload_at_60_seconds_and_restart(self):
        now = [0.]
        cache = OptimizationCache(clock=lambda: now[0])
        self.assertEqual(cache.get(), {})
        row = save_version("ctr", [], 100, "gold-1")
        now[0] = 59.999
        self.assertEqual(cache.get(), {})
        now[0] = 60.
        self.assertEqual(cache.get()["ctr"].version, row.version)
        self.assertEqual(OptimizationCache().get()["ctr"].version, row.version)

    def test_conflicting_same_source_revision_rejected(self):
        save_version("ctr", [], 100, "gold-1")
        with self.assertRaises(ValueError):
            save_version("ctr", [{"different": 1}], 100, "gold-1")

    def test_ctr_command_replays_full_snapshot_and_rejects_invalid(self):
        snapshot = dict(finalized=True, source_version="gold-1", as_of_ms=100,
            rows=[dict(campaign_id="syn:c", creative_id="syn:cr", slot="lobby_banner",
                       source="synthetic", attribution_day="2026-11-08", hour=0,
                       valid_impressions=1000, clicked_impressions=100)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            call_command("ad_update", "ctr", input=path)
            call_command("ad_update", "ctr", input=path)
            row = AdOptimizationVersion.objects.get()
            self.assertEqual(row.payload[0]["alpha"], 104.)
            path.write_text(json.dumps(snapshot | {"finalized": False}), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("ad_update", "ctr", input=path)
            self.assertEqual(AdOptimizationVersion.objects.count(), 1)

    def test_pacing_same_bucket_does_not_take_second_step(self):
        # The command runs outside transactions; TestCase owns an outer rollback transaction.
        with patch("ads.web.management.commands.ad_update.time.time", return_value=1794092400), \
                patch("ads.web.management.commands.ad_update.close_old_connections"):
            call_command("ad_update", "pacing", once=True)
            call_command("ad_update", "pacing", once=True)
        self.assertEqual(AdOptimizationVersion.objects.count(), 1)

    def test_reload_failure_keeps_last_good(self):
        clock = [0.]
        cache = OptimizationCache(clock=lambda: clock[0])
        row = save_version("ctr", [], 100, "gold-1")
        cache.get()
        clock[0] = 60.
        with patch.object(AdOptimizationVersion.objects, "filter", side_effect=RuntimeError("offline")):
            self.assertEqual(cache.get()["ctr"].version, row.version)


# TestCase는 시험 전체를 커밋하지 않는 트랜잭션으로 감싸서 별도 연결이 시험 데이터를 보지 못한다.
# 예약 별도 연결 동작은 tests/ads/test_mysql_ledger.py의 TransactionTestCase에서 따로 검증한다.
@override_settings(ADS_RESERVE_DB="default")
class ContextAndBidHTTPTests(TestCase):
    """Observe bids, rollout and rollback through HTTP and the MySQL outbox."""

    def test_context_canary_rollback_and_bid_reload(self):
        campaign = Campaign.objects.create(campaign_id="syn:cpc", advertiser_id="syn:adv", name="CTR experiment",
            slots=["result_banner"], billing="cpc", min_bid_mw=10_000, max_bid_mw=100_000,
            target_cpc_mw=40_000, daily_budget_mw=10_000_000, start_date="2026-11-01", end_date="2026-12-01")
        Creative.objects.create(creative_id="syn:cr", campaign=campaign, title="ad", body="ad",
                                image_path="/static/promo/example.png")
        now = [0.]
        cache = OptimizationCache(clock=lambda: now[0])
        actual_service = composition.get_service

        def service():
            instance = actual_service()
            instance.clock = lambda: 1794092400000  # 08:00 KST
            return instance

        def serve(index, canary):
            for i in range(100):
                rid = f"syn:probe:{index}:{i}"
                assigned = int.from_bytes(hashlib.sha256(rid.encode()).digest()[:8], "big") % 5 == 0
                if assigned == canary:
                    break
            response = self.client.post("/ads/v1/serve", json.dumps(dict(request_id=rid, slot="result_banner",
                source="synthetic", session_pseudonym=rid, context=dict(preset_id="base6", outcome="win", hour=8))),
                content_type="application/json").json()
            return response, AdOutbox.objects.get(topic="ad.serve", payload__request_id=rid).payload

        with (tempfile.TemporaryDirectory() as directory, patch.object(composition, "_optimization", cache),
              patch("ads.web.views.get_service", side_effect=service),
              patch("ads.web.management.commands.ad_update.registered_report") as registered):
            path = Path(directory) / "gold.json"
            gold = dict(finalized=True, source_version="delta:ads-gold:1", as_of_ms=3_600_000, rows=[dict(
                campaign_id=campaign.pk, source="synthetic", attribution_day="2026-11-08", hour=8,
                slot="result_banner", billing="cpc", load_test=False, clicks=2, cost_mw=100_000)])
            path.write_text(json.dumps(gold), encoding="utf-8")
            beta = save_version("ctr", [], 100, "beta-test")
            _, initial = serve("initial", True)
            self.assertEqual(initial["candidates"][0]["bid_mw"], 100_000)
            call_command("ad_update", "bid", input=path)
            call_command("ad_update", "bid", input=path)
            bid = AdOptimizationVersion.objects.get(kind="bid")
            self.assertEqual(bid.payload["bids"][0]["bid_mw"], 80_000)
            path.write_text(json.dumps(gold | {"rows": []}), encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "bid source revision content conflict"):
                call_command("ad_update", "bid", input=path)

            report = dict(source="synthetic", registry_version="paldo-ad-ctr-synthetic:1", feature_version="ad-context-v1",
                          gate=dict(accepted=True), beta=dict(impressions=500, log_loss=.4, ece=.02),
                          lightgbm=dict(impressions=500, model_impressions=500, log_loss=.3, ece=.02),
                          predictions=[["result_banner", "syn:cr", "win", "base6", 8, .3]])
            registered.return_value = {key: value for key, value in report.items() if key != "registry_version"}
            path.write_text(json.dumps(report | {"gate": {"accepted": False}}), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command("ad_update", "ctrml", input=path)
            self.assertEqual(serve("rejected", True)[1]["ctr_version"], beta.version)
            path.write_text(json.dumps(report), encoding="utf-8")
            call_command("ad_update", "ctrml", input=path)
            candidate = AdOptimizationVersion.objects.get(kind="ctrml")
            now[0] = 59.999
            self.assertEqual(serve("before-ttl", True)[1]["ctr_version"], beta.version)
            now[0] = 60.
            result, auction = serve("canary", True)
            self.assertEqual(auction["ctr_version"], candidate.version)
            self.assertEqual(auction["bid_version"], bid.version)
            self.assertAlmostEqual(auction["candidates"][0]["ctr_mean"], .3)
            self.assertEqual(auction["candidates"][0]["bid_mw"], 80_000)
            self.assertEqual(serve("control", False)[1]["ctr_version"], beta.version)
            save_version("ctr", [], 200, "beta-during-canary")
            with self.assertRaises(CommandError):
                call_command("ad_update", "rollback-ctr", expected_version=candidate.version,
                             signal="fallback_rate", value=.05)
            call_command("ad_update", "rollback-ctr", expected_version=candidate.version,
                         signal="fallback_rate", value=.06)
            now[0] = 120.
            self.assertEqual(serve("after-rollback", True)[1]["ctr_version"], beta.version)
            self.assertEqual(self.client.get("/ads/v1/click", {"t": result["serve_token"]}).status_code, 302)
            click = AdOutbox.objects.get(topic="ad.click").payload
            self.assertEqual((click["ctr_version"], click["bid_version"]), (candidate.version, bid.version))
            fresh = save_version("ctr", [], 300, "beta-after-rollback")
            now[0] = 180.
            self.assertEqual(serve("fresh-beta", True)[1]["ctr_version"], fresh.version)
            with self.assertRaises(CommandError):
                call_command("ad_update", "ctrml", input=path)


class UpdatedHTTPTests(PersistentHTTPTests):
    """Use real MySQL HTTP flow and change versions between serve and click."""

    def test_updated_serve_and_late_events_keep_original_versions(self):
        cache = OptimizationCache()
        with patch.object(composition, "_optimization", cache):
            ctr = save_version("ctr", [dict(creative_id="creative", slot="lobby_banner", alpha=9000., beta=1000.)], 100, "gold-1")
            pacing = save_version("pacing", [], 100, "bucket-1")
            original = self.serve("versioned").json()
            self.assertEqual(original["ctr_version"], ctr.version)
            self.assertEqual(original["policy_version"], pacing.version)
            event = AdOutbox.objects.get(topic="ad.serve").payload
            self.assertEqual(event["candidates"][0]["ctr_mean"], .9)
            save_version("ctr", [], 200, "gold-2")
            cache.loaded_at = float("-inf")
            self.click(original["serve_token"], "late-versioned")
        for event in AdOutbox.objects.exclude(topic="").values_list("payload", flat=True):
            self.assertEqual(event["ctr_version"], ctr.version)
            self.assertEqual(event["policy_version"], pacing.version)

    def test_exploration_uses_persistent_impressions_between_requests(self):
        for i in range(25):
            response = self.serve(f"explore-{i}", session=f"explore-session-{i}").json()
            self.click(response["serve_token"], f"explore-click-{i}")
        from ads.web.models import AdStoredRequest
        records = [r["record"] for r in AdStoredRequest.objects.values_list("serve_data", flat=True)]
        self.assertEqual(sum(r["exploration"] for r in records), 2)
