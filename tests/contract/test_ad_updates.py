"""D-08-02: final aggregate updates and auction control without external services."""
import copy
import unittest
from unittest.mock import patch

from ads.app.ctr_update import build_ctr
from ads.app.pacing_update import build_pacing
from ads.app.serve import AdService
from ads.app.beacon import handle_impression
from ads.domain.model import AdContext, AdRequest, AdSlot, BillingType, Targeting
from tools.gen_synthetic_ads import synthetic_campaigns
from tools.build_ads_fixtures import slot_policies


class UpdateTests(unittest.TestCase):
    """A full snapshot replaces observations instead of adding a second time."""

    def setUp(self):
        self.policies = {p.slot: p for p in slot_policies()}
        self.row = dict(campaign_id="syn:c", creative_id="syn:cr", slot="lobby_banner",
                        source="synthetic", attribution_day="2026-11-08", hour=0,
                        valid_impressions=1000, clicked_impressions=100)
        self.snapshot = dict(finalized=True, source_version="local-1", as_of_ms=1000, rows=[self.row])

    def test_posterior_recomputed_and_dimensions_folded(self):
        first = build_ctr(self.snapshot, self.policies)
        self.assertEqual(first, build_ctr(self.snapshot, self.policies))
        policy = self.policies[AdSlot.LOBBY_BANNER]
        row = first[0]
        self.assertEqual(row["alpha"], policy.prior_ctr * policy.prior_strength + 100)
        self.assertEqual(row["beta"], (1-policy.prior_ctr) * policy.prior_strength + 900)
        changed = copy.deepcopy(self.snapshot)
        changed["rows"].append(self.row | {"hour": 1, "valid_impressions": 100, "clicked_impressions": 0})
        self.assertEqual(build_ctr(changed, self.policies)[0]["beta"], row["beta"] + 100)

    def test_bad_or_nonfinal_aggregates_are_rejected(self):
        for update in ({"clicked_impressions": 1001}, {"valid_impressions": -1},
                       {"valid_impressions": True}, {"source": "human"}, {"hour": 24}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                build_ctr(self.snapshot | {"rows": [self.row | update]}, self.policies)
        with self.assertRaises(ValueError):
            build_ctr(self.snapshot | {"finalized": False}, self.policies)
        for invalid in (None, 42, [], self.snapshot | {"rows": [None]},
                        self.snapshot | {"rows": [self.row | {"campaign_id": 42}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_ctr(invalid, self.policies)
        with self.assertRaises(ValueError):
            build_ctr(self.snapshot | {"rows": [self.row, self.row]}, self.policies)
        moved = build_ctr(self.snapshot | {"rows": [self.row, self.row | {"campaign_id": "syn:other"}]}, self.policies)
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["valid_impressions"], 2000)
        self.assertEqual(moved[0]["clicked_impressions"], 200)

    def test_pacing_reads_reserved_and_confirmed_and_limits_step(self):
        campaigns = synthetic_campaigns()
        service = AdService(lambda: campaigns, self.policies, b"unit-test")
        c = campaigns[0]
        day = "2026-11-08"
        service.book.totals[c.campaign_id, day] = c.daily_budget_mw
        rows = build_pacing(campaigns, service.book, day, 720, {})
        self.assertEqual(next(r for r in rows if r["campaign_id"] == c.campaign_id)["probability"], .8)
        rows = build_pacing(campaigns, service.book, day, 720, {c.campaign_id: .05})
        self.assertEqual(next(r for r in rows if r["campaign_id"] == c.campaign_id)["probability"], .05)

    def test_cpm_uses_learned_creative_and_pacing_is_separate(self):
        campaigns = synthetic_campaigns()
        c = next(c for c in campaigns if c.billing is BillingType.CPM)
        c.targeting = Targeting()
        service = AdService(lambda: [c], self.policies, b"unit-test")
        service.ctr = {(cr.creative_id, slot): (9000., 1000.) for cr in c.creatives for slot in c.slots}
        service.ctr_version = "learned"
        service.policy_version = "paced"
        service.pacing = {(c.campaign_id, "2026-11-08"): .05}
        service.clock = lambda: 1794092400000  # 2026-11-08 09:20 KST
        counts = dict(impressions=100, explorations=5, creatives={})
        service.exposure_counts[c.campaign_id, "2026-11-08"] = counts
        rejected = 0
        for i in range(50):
            response = service.serve(AdRequest(f"syn:unit{i}", c.slots[0], f"syn:s{i}", AdContext(), "synthetic"))
            self.assertEqual(response.ctr_version, "learned")
            rejected += not response.filled
            if service.candidates[response.request_id]:
                self.assertGreater(service.candidates[response.request_id][0].ctr_mean, .8)
        self.assertGreater(rejected, 30)

    def test_exploration_floors_valid_daily_impressions_and_resets_day(self):
        c = next(c for c in synthetic_campaigns() if c.billing is BillingType.CPM)
        c.targeting = Targeting()
        c.daily_budget_mw = 10**10
        c.start_date, c.end_date = "2026-11-01", "2026-12-01"
        now = [1794092400000]
        service = AdService(lambda: [c], self.policies, b"unit-test", clock=lambda: now[0])
        for i in range(100):
            response = service.serve(AdRequest(f"syn:floor{i}", c.slots[0], f"syn:s{i}", AdContext(), "synthetic"))
            self.assertTrue(response.filled)
            handle_impression(service, response.serve_token, 1., 1200)
        counts = service.exposure_counts[c.campaign_id, "2026-11-08"]
        self.assertEqual(counts["impressions"], 100)
        self.assertGreaterEqual(counts["explorations"], 5)
        now[0] += 86400000
        response = service.serve(AdRequest("syn:next-day", c.slots[0], "syn:next", AdContext(), "synthetic"))
        self.assertTrue(next(reversed(service.state.serves.values())).exploration)

    def test_cpc_rank_sample_uses_contract_floor(self):
        c = next(c for c in synthetic_campaigns() if c.billing is BillingType.CPC)
        service = AdService(lambda: [c], self.policies, b"unit-test")
        with patch("ads.app.serve.thompson_ctr", return_value=0.):
            self.assertEqual(service.rank_ctr(c, c.creatives[0], self.policies[c.slots[0]], None), .0001)
