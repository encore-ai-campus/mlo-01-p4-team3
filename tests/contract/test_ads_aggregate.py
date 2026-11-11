"""광고 하루 집계 자동화: silver → `ad_update ctr` 칸, 원장 내보내기 형식, 마감·부하 시험 경계."""
import json
from pathlib import Path
import unittest

from ads.app.ctr_update import build_ctr
from ads.app.ledger_export import budgets_by_day, export_snapshot
from ads.domain.model import AdSlot, SlotPolicy
from pipeline.spark.ads_contract import (campaign_daily_rows, closing_times, ctr_cells, ctr_snapshot, final_closed_scope,
                                         gold_rows, reconcile_rows, silver_rows)
from tests.contract.test_ads_pipeline import T, event


def _policies():
    data = json.loads(Path("fixtures/ads/campaigns.json").read_text(encoding="utf-8"))
    return {AdSlot(r["slot"]): SlotPolicy(**{**r, "slot": AdSlot(r["slot"])}) for r in data["slot_policies"]}


FINAL = closing_times("2026-11-08")[1]


def serve_pair(n, click=False, **changes):
    """serve n개 각각에 유효 노출(필요하면 클릭)을 붙인다."""
    out = []
    for i in range(n):
        sid = f"syn:c08_s{i}"
        out += [event("ad.serve", f"s{i}", serve_id=sid, **changes),
                event("ad.impression", f"i{i}", 1000, serve_id=sid, **changes)]
        if click and i == 0:
            out.append(event("ad.click", f"c{i}", 2000, serve_id=sid, charge_id=sid + ":click", charge_mw=100999, **changes))
    return out


class CtrCells(unittest.TestCase):
    """확정된 날·유효·부하 시험 아님만 (캠페인, 소재, 지면, 출처, 날짜, 시간) 칸으로 센다."""

    def test_snapshot_passes_build_ctr(self):
        rows = silver_rows(serve_pair(3, click=True), FINAL)
        snap = ctr_snapshot(rows, FINAL, "delta:ads-silver:x:7")
        self.assertEqual(snap["rows"], [{"campaign_id": "syn:c08_c", "creative_id": "syn:c08_cr", "slot": "lobby_banner",
                                         "source": "synthetic", "attribution_day": "2026-11-08", "hour": 23,
                                         "valid_impressions": 3, "clicked_impressions": 1}])
        out = build_ctr(snap, _policies())
        self.assertEqual((out[0]["valid_impressions"], out[0]["clicked_impressions"]), (3, 1))

    def test_training_rows_are_rejected_by_build_ctr(self):
        # 이전 연결 결함 재현: gold 학습 행(ctr.json)은 campaign_id·valid_impressions가 없어 거부된다.
        _, training = gold_rows(silver_rows(serve_pair(2, click=True), FINAL), FINAL)
        with self.assertRaises(ValueError):
            build_ctr(dict(finalized=True, source_version="v", as_of_ms=FINAL, rows=training), _policies())

    def test_day_before_final_close_is_excluded(self):
        rows = silver_rows(serve_pair(2), FINAL - 1)
        self.assertEqual(ctr_cells(rows, FINAL - 1), [])
        self.assertEqual(len(ctr_cells(silver_rows(serve_pair(2), FINAL), FINAL)), 1)

    def test_load_test_is_excluded(self):
        rows = silver_rows(serve_pair(2, click=True, load_test=True), FINAL)
        self.assertEqual(ctr_cells(rows, FINAL), [])

    def test_click_only_counts_as_shown_and_duplicates_once(self):
        data = [event("ad.serve", "s"), event("ad.click", "c", 2000, charge_id="syn:c08_s:click", charge_mw=100999),
                event("ad.click", "c2", 3000, charge_id="syn:c08_s:click", charge_mw=100999)]
        cells = ctr_cells(silver_rows(data, FINAL), FINAL)
        self.assertEqual((cells[0]["valid_impressions"], cells[0]["clicked_impressions"]), (1, 1))

    def test_cells_are_unique_per_key(self):
        data = serve_pair(2) + [dict(e, event=dict(e["event"], event_id=e["event"]["event_id"] + "b",
                                                    serve_id=e["event"]["serve_id"] + "b", creative_id="syn:c08_cr2"))
                                for e in serve_pair(1)]
        cells = ctr_cells(silver_rows(data, FINAL), FINAL)
        keys = [tuple(c[k] for k in ("campaign_id", "creative_id", "slot", "source", "attribution_day", "hour")) for c in cells]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(cells), 2)
        build_ctr(ctr_snapshot(silver_rows(data, FINAL), FINAL, "v"), _policies())

    def test_source_version_required(self):
        with self.assertRaises(ValueError):
            ctr_snapshot([], FINAL, "")


class LedgerExport(unittest.TestCase):
    """원장 내보내기는 gold `--ledger`(대조)·`--budgets`(소진율) 입력과 같은 형식이다."""

    LEDGER = [dict(charge_id="syn:c08_s0:click", amount_mw=100999, state="confirmed", kind="charge", source_event_id="c0",
                   source="synthetic", campaign_id="syn:c08_c", attribution_day="2026-11-08", slot="lobby_banner"),
              dict(charge_id="r1", amount_mw=500, state="reserved", kind="charge", source_event_id=None,
                   source="synthetic", campaign_id="gone", attribution_day="2026-11-08", slot="lobby_banner")]

    def test_budgets_by_day_uses_current_daily_budget_and_skips_missing(self):
        self.assertEqual(budgets_by_day(self.LEDGER, {"syn:c08_c": 30000000}), {"2026-11-08": {"syn:c08_c": 30000000}})

    def test_export_matches_reconcile_and_daily_inputs(self):
        ledger, budgets, summary = export_snapshot(lambda: self.LEDGER, lambda: {"syn:c08_c": 30000000})
        self.assertEqual(summary, dict(ledger_rows=2, confirmed_rows=1, confirmed_net_mw=100999, budget_days=1))
        rows = silver_rows(serve_pair(1, click=True), FINAL)
        self.assertEqual(reconcile_rows(ledger, rows)["mismatches"], 0)
        daily = {r["campaign_id"]: r for r in campaign_daily_rows(ledger, budgets)}
        self.assertEqual((daily["syn:c08_c"]["confirmed_net_mw"], daily["syn:c08_c"]["budget_mw"]), (100999, 30000000))
        self.assertIsNone(daily["gone"]["budget_mw"])
        json.dumps([ledger, budgets])


    def test_closed_day_scope_ignores_charges_after_cutoff(self):
        # 원장을 내보내는 순간 생긴 오늘 과금(silver 밖)은 확정 마감 전이라 대조에서 빠진다.
        today = dict(self.LEDGER[0], charge_id="syn:c08_late:click", attribution_day="2026-11-09")
        ledger = [self.LEDGER[0], today]
        rows = silver_rows(serve_pair(1, click=True), FINAL)
        self.assertGreater(reconcile_rows(ledger, rows)["mismatches"], 0)
        self.assertEqual(reconcile_rows(*final_closed_scope(ledger, rows, FINAL))["mismatches"], 0)
        self.assertEqual(final_closed_scope(ledger, rows, FINAL - 1), ([], []))


if __name__ == "__main__":
    unittest.main()
