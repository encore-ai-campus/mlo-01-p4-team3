"""D-08-02 persistent versions, reload and auction-time event pinning."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from ads.infra.optimization import OptimizationCache, save_version
from ads.web.models import AdOptimizationVersion, AdOutbox
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
