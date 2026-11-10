"""D-10-02 gate, gold CPC bounds and snapshot input isolation."""
from dataclasses import replace
from unittest import TestCase

from ads.app.bid_update import build_bids
from ads.domain.model import AdSlot, BillingType
from ml.ad_ctr import split_rows, weighted_metrics
from ml.gate import ad_ctr_gate
from pipeline.spark.ads_gold import cpc_bid_days
from tools.gen_synthetic_ads import synthetic_campaigns


class AdCtrBidTests(TestCase):
    """Compare identical impressions and exclude data unavailable at serving time."""

    def test_ctr_split_is_temporal_and_ignores_other_source(self):
        base = dict(slot=AdSlot.RESULT_BANNER.value, creative_id="syn:ad", source="synthetic",
                    outcome="win", preset_id="base6", hour=9, impressions=100, clicks=10)
        earlier = base | {"attribution_day": "2026-11-07"}
        later = base | {"attribution_day": "2026-11-08"}
        snapshot = dict(finalized=True, source_version="delta:training:1", as_of_ms=1, rows=[
            later, earlier, later | {"creative_id": "human", "source": "human"}])
        self.assertEqual(split_rows(snapshot, "2026-11-08", "synthetic"), ([earlier], [later]))
        with self.assertRaises(ValueError):
            split_rows(snapshot | {"rows": snapshot["rows"] + [later]}, "2026-11-08", "synthetic")

    def test_weighted_metrics_and_gate(self):
        score = weighted_metrics([{"impressions": 100, "clicks": 20},
                                  {"impressions": 10, "clicks": 8}], [.2, .8])
        self.assertAlmostEqual(score["ece"], 0)
        candidate = score | {"model_impressions": 110}
        self.assertTrue(ad_ctr_gate(candidate, score).accepted)
        self.assertFalse(ad_ctr_gate(candidate | {"model_impressions": 0}, score).accepted)
        self.assertFalse(ad_ctr_gate(candidate | {"ece": .051}, score).accepted)
        self.assertFalse(ad_ctr_gate(candidate | {"log_loss": score["log_loss"] + .011}, score).accepted)

    def test_cpc_export_excludes_mixed_billing_on_latest_day(self):
        base = dict(campaign_id="syn:cpc", source="synthetic", status="valid", load_test=False, billing="cpc")
        first = base | {"attribution_day": "2026-11-01"}
        second = base | {"attribution_day": "2026-11-02"}
        cutoff = 1793979120001
        self.assertEqual(cpc_bid_days([first, second], cutoff), {("syn:cpc", "synthetic", "2026-11-02")})
        self.assertEqual(cpc_bid_days([first, second, second | {"billing": "cpm"}], cutoff), set())

    def test_bid_clamps_both_bounds_and_excludes_load_test(self):
        cpc = next(c for c in synthetic_campaigns() if c.billing is BillingType.CPC)
        cpc = replace(cpc, min_bid_mw=20, max_bid_mw=100, target_cpc_mw=50)
        cpm = replace(cpc, campaign_id="syn:cpm", billing=BillingType.CPM)
        gold = dict(attribution_day="2026-11-08", hour=10, slot=cpc.slots[0].value,
                    source="synthetic", campaign_id=cpc.campaign_id, billing="cpc", load_test=False, clicks=10)
        snapshot = dict(finalized=True, source_version="delta:gold:2", as_of_ms=1)
        previous = {cpc.campaign_id: dict(bid_mw=80, min_bid_mw=20, max_bid_mw=100, target_cpc_mw=50,
                                         attribution_day="2026-11-07", clicks=1, cost_mw=1)}
        for cost, expected in ((100, 100), (2000, 20), (600, 67)):
            rows = [gold | {"cost_mw": cost}, gold | {"cost_mw": 900, "load_test": True},
                    gold | {"cost_mw": 900, "campaign_id": "syn:cpm"}]
            bids = build_bids(snapshot | {"rows": rows}, [cpc, cpm], previous)
            self.assertEqual(len(bids), 1)
            self.assertEqual(bids[0]["bid_mw"], expected)
            self.assertEqual(bids[0]["realized_cpc_mw"], cost / 10)
            same = build_bids(snapshot | {"rows": rows, "source_version": "delta:gold:3"},
                              [cpc], {cpc.campaign_id: bids[0]})
            self.assertEqual(same, bids)
