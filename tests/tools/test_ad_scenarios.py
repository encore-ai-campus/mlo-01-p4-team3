"""The evaluation world is separate from training and its reports fail closed."""
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.spark.ads_contract import reconcile_rows, silver_rows
from tools import ad_scenarios


class ScenarioTests(unittest.TestCase):
    """Small runs prove mechanics; acceptance uses the full three-seed run."""

    def test_independent_generator_and_reproducible_run(self):
        source = inspect.getsource(ad_scenarios)
        self.assertNotIn("gen_synthetic_ads", source)
        self.assertNotIn("ad_traffic_sim", source)
        a = ad_scenarios.run_policy(2, 17, 120, "thompson")
        b = ad_scenarios.run_policy(2, 17, 120, "thompson")
        self.assertEqual(a, b)
        self.assertTrue(a["truth"])
        self.assertEqual(a["metrics"]["invalid_charges"], 0)
        self.assertIsNone(a["metrics"]["gold_reconciliation_mismatches"])
        self.assertGreater(a["metrics"]["temporal_changes"], 0)
        self.assertGreater(a["metrics"]["heterogeneous_users"], 0)

    def test_aggregate_replay_and_delayed_clicks(self):
        result = ad_scenarios.run_policy(5, 29, 240, "thompson")
        self.assertEqual(result["metrics"]["invalid_charges"], 0)
        self.assertGreater(result["metrics"]["delayed_clicks"], 0)
        self.assertEqual(result["metrics"]["posterior_replay_mismatches"], 0)
        self.assertEqual(set(result["metrics"]["invalid_types"]),
                         {"duplicate", "bad_signature", "no_serve", "expired"})

    def test_cli_rejects_too_few_or_duplicate_seeds(self):
        for seeds in ("17", "17,17,29"):
            with self.assertRaises(ValueError):
                ad_scenarios.evaluate([1], [int(v) for v in seeds.split(",")], 30, Path(tempfile.gettempdir()))

    def test_replay_exports_independent_online_ledger_and_judgements(self):
        with tempfile.TemporaryDirectory() as folder:
            replay = Path(folder) / "scenario5"
            result = ad_scenarios.run_policy(5, 29, 1200, "thompson", replay_out=replay)
            bronze = [json.loads(line) for line in (replay / "bronze.jsonl").read_text().splitlines()]
            ledger = json.loads((replay / "ledger.json").read_text())
            cutoff = json.loads((replay / "cutoff.json").read_text())
        records = [dict(topic=row["topic"], event=json.loads(row["envelope_json"])) for row in bronze]
        silver = silver_rows(records, cutoff)
        self.assertTrue(any(r["implied_by_click"] for r in silver))
        self.assertEqual({r["invalid_reason"] for r in silver if r["status"] == "invalid"},
                         {"duplicate", "bad_signature", "no_serve", "expired"})
        self.assertTrue(any(r["state"] == "released" for r in ledger))
        reconciliation = reconcile_rows(ledger, silver)
        self.assertEqual(reconciliation["mismatches"], 0)
        self.assertGreater(reconciliation["ledger_net_mw"], 0)
        self.assertEqual(reconciliation["ledger_net_mw"], result["metrics"]["settled_mw"])
        next(r for r in ledger if r["state"] == "confirmed")["amount_mw"] += 1
        self.assertEqual(reconcile_rows(ledger, silver)["mismatches"], 1)
