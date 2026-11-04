"""광고 계약 테스트: 2등 가격, 예산 예약·확정·해제, 유효 판정(클릭 먼저 도착·무효 공격), 대사, 게임 쪽 포트 보호."""
from __future__ import annotations

import time
import unittest

from ads.domain import rules
from ads.domain.events import AdClickEvent, AdImpressionEvent, AdServeEvent
from ads.domain.model import (
    SLOT_SIZES, AdContext, AdRequest, AdSlot, Bidder, BillingType, Campaign, CampaignStatus, ChargeState, JudgeStatus,
    ServeClaims, ServeRecord, SlotPolicy, Targeting, slot_image_path, won,
)
from ads.testing.fakes import FakeAdProvider
from engine.domain import serde
from server.app.ads_port import AdGateway, AdProvider, CircuitBreaker

from ._util import load_json, strict

KEY = b"test-key"


class Fixtures(unittest.TestCase):
    def test_campaigns_parse(self):
        d = load_json("fixtures/ads/campaigns.json")
        camps = [strict(Campaign, c) for c in d["campaigns"]]
        [strict(SlotPolicy, p) for p in d["slot_policies"]]
        self.assertGreaterEqual(len(camps), 3)
        for c in camps:
            for cr in c.creatives:
                self.assertEqual(cr.campaign_id, c.campaign_id)
                self.assertTrue(cr.landing_path.startswith("/"), "랜딩은 사이트 안 경로")


class SlotSizes(unittest.TestCase):
    """P-19(11/5 잠정): 지면 규격과 규격별 소재 이미지 경로."""

    def test_sizes_cover_every_slot(self):
        self.assertEqual(set(SLOT_SIZES), set(AdSlot))
        self.assertEqual(SLOT_SIZES[AdSlot.LOBBY_BANNER], (728, 90))
        self.assertEqual(SLOT_SIZES[AdSlot.RESULT_BANNER], (728, 90))
        self.assertEqual(SLOT_SIZES[AdSlot.LLM_WAIT], (300, 250))
        self.assertEqual(SLOT_SIZES[AdSlot.REVIEW_SIDE], (300, 250))

    def test_fixture_copy_matches(self):
        d = load_json("fixtures/ads/campaigns.json")
        self.assertEqual({k: tuple(v) for k, v in d["slot_sizes"].items()},
                         {s.value: wh for s, wh in SLOT_SIZES.items()})

    def test_slot_image_path(self):
        self.assertEqual(slot_image_path("/static/ads/gat.png", AdSlot.LOBBY_BANNER), "/static/ads/gat__728x90.png")
        self.assertEqual(slot_image_path("/static/ads/gat.png", "review_side"), "/static/ads/gat__300x250.png")

    def test_one_file_per_creative_and_size(self):
        d = load_json("fixtures/ads/campaigns.json")
        for c in (strict(Campaign, x) for x in d["campaigns"]):
            for cr in c.creatives:
                paths = {slot_image_path(cr.image_path, s) for s in c.slots}
                self.assertEqual(len(paths), len({SLOT_SIZES[s] for s in c.slots}), cr.creative_id)

    def test_fake_returns_slot_sized_path(self):
        req = AdRequest("r1", AdSlot.LLM_WAIT, "p1", AdContext("base6", None, False, 12))
        resp = FakeAdProvider().request_ad(req, 50, 120)
        self.assertTrue(resp.creative.image_path.endswith("__300x250.png"))


class Auction(unittest.TestCase):
    def test_cases(self):
        for c in load_json("fixtures/ads/auction_cases.json"):
            with self.subTest(case=c["case_id"]):
                bidders = [serde.from_jsonable(Bidder, b) for b in c["bidders"]]
                r = rules.run_auction(bidders, c["reserve_ecpm_mw"], c["request_id"])
                exp = c["expect"]
                if "expect_any_winner" in c:
                    self.assertIn(r.winner.campaign_id, c["expect_any_winner"])
                else:
                    self.assertEqual(r.winner.campaign_id if r.winner else None, exp["winner"])
                for k in ("winner_ecpm_mw", "threshold_ecpm_mw", "price_mw", "capped"):
                    if k in exp:
                        self.assertEqual(getattr(r, k), exp[k], k)
                if r.winner:
                    self.assertLessEqual(r.price_mw, r.winner.bid_mw)

    def test_design_example(self):
        r = rules.run_auction([Bidder("a", "x", BillingType.CPC, won(300), 0.02),
                               Bidder("b", "y", BillingType.CPM, won(1000), 0.0)], won(500), "r")
        self.assertEqual((r.threshold_ecpm_mw, r.price_mw), (won(1000), won(50)))

    def test_tie_is_deterministic_per_request(self):
        bs = [Bidder("a", "x", BillingType.CPM, won(3000), 0), Bidder("b", "y", BillingType.CPM, won(3000), 0)]
        winners = {rules.run_auction(bs, won(500), f"r{i}").winner.campaign_id for i in range(20)}
        self.assertEqual(winners, {"a", "b"})     # 요청마다 다르게, 같은 요청이면 같게
        self.assertEqual(rules.run_auction(bs, won(500), "r1").winner.campaign_id,
                         rules.run_auction(list(reversed(bs)), won(500), "r1").winner.campaign_id)


def _camp(budget_mw: int, billing: BillingType) -> Campaign:
    return Campaign("cp", "adv", "c", [AdSlot.LOBBY_BANNER], billing, won(300), budget_mw, Targeting(),
                    "2026-11-01", "2026-11-30", CampaignStatus.ACTIVE, [])


def _serve(sid: str, billing: BillingType, price_mw: int, at_ms: int = 0, session: str = "s") -> ServeRecord:
    return ServeRecord(sid, "req_" + sid, "cp", "cr", AdSlot.LOBBY_BANNER, billing, price_mw, 0.02, session, at_ms,
                       "2026-11-06")


class Budget(unittest.TestCase):
    def test_reserve_confirm_release(self):
        c = load_json("fixtures/ads/budget_cases.json")[0]
        billing = BillingType(c["billing"])
        camp = _camp(c["daily_budget_mw"], billing)
        book, serves = rules.BudgetBook(), {}
        for s in c["steps"]:
            if s["op"] == "reserve":
                serves[s["serve"]] = _serve(s["serve"], billing, c["price_mw"], s["at_ms"])
                self.assertEqual(book.reserve(camp, serves[s["serve"]]), s["expect"], s)
            elif s["op"] == "confirm":
                self.assertEqual(book.confirm(rules.charge_id(s["serve"], billing), s["event"]), s["expect"], s)
            elif s["op"] == "release":
                self.assertEqual(book.release_expired(s["now_ms"]), s["expect_released"])
            elif s["op"] == "refund":
                self.assertEqual(book.refund(rules.charge_id(s["serve"], billing), s["event"]).amount_mw,
                                 s["expect_amount_mw"])
        self.assertEqual(book.committed_mw("cp", "2026-11-06"), c["expect_committed_mw"])
        self.assertEqual(book.rows["s1:click"].state, ChargeState.CONFIRMED)   # 원래 행은 고치지 않는다

    def test_reconcile_itemized(self):
        c = load_json("fixtures/ads/budget_cases.json")[1]
        self.assertEqual(rules.reconcile(c["ledger"], c["delta"]), c["expect"])

    def test_pacing_bounds(self):
        p = rules.next_pacing_probability(1.0, won(900), won(1000), 720)   # 정오에 90% 소진 → 낮춤
        self.assertAlmostEqual(p, 0.8)                                       # 한 번 조정 폭 0.2
        self.assertEqual(rules.next_pacing_probability(0.05, won(1000), won(1000), 0), 0.05)
        self.assertEqual(rules.next_pacing_probability(0.95, 0, won(1000), 1200), 1.0)
        curve = [1] * 12 + [3] * 12
        self.assertLess(rules.target_fraction(720, curve), 0.5)


class AdsReviewRegressions(unittest.TestCase):
    """광고 설계 크로스체크 지적 회귀."""

    def test_cpm_remainder_carried(self):
        camp = _camp(won(1_000_000), BillingType.CPM)
        camp.max_bid_mw = won(5000)
        book = rules.BudgetBook()
        for i in range(1000):
            rec = _serve(f"s{i}", BillingType.CPM, 1501)
            self.assertTrue(book.reserve(camp, rec))
            self.assertTrue(book.confirm(rules.charge_id(rec.serve_id, BillingType.CPM), f"e{i}"))
        self.assertEqual(book.committed_mw("cp", "2026-11-06"), 1501)   # 1000회 노출 = 청구 CPM 그대로

    def test_cpm_refund_restores_carry(self):
        camp = _camp(won(1_000_000), BillingType.CPM)
        book = rules.BudgetBook()
        a, b = _serve("a", BillingType.CPM, 500), _serve("b", BillingType.CPM, 500)
        book.reserve(camp, a)
        book.confirm("a:impression", "ea")
        book.refund("a:impression", "ea_rejudged")
        book.reserve(camp, b)
        book.confirm("b:impression", "eb")
        self.assertEqual(book.rows["b:impression"].amount_mw, 0)      # 환입한 노출의 나머지가 남지 않음
        self.assertEqual(book.cpm_carry["cp"], 500)

    def test_reserve_invariants(self):
        camp = _camp(won(100), BillingType.CPC)
        book = rules.BudgetBook()
        with self.assertRaises(ValueError):
            book.reserve(camp, _serve("neg", BillingType.CPC, -100))
        other = _serve("x", BillingType.CPC, 10)
        other.campaign_id = "other"
        with self.assertRaises(ValueError):
            book.reserve(camp, other)

    def test_one_bidder_per_campaign(self):
        with self.assertRaises(ValueError):
            rules.run_auction([Bidder("a", "a1", BillingType.CPM, won(10), 0), Bidder("a", "a2", BillingType.CPM, 100, 0)],
                              0, "r")

    def test_bad_provider_response_is_empty(self):
        class Broken:
            def request_ad(self, req, c, t):
                return None
        self.assertEqual(AdGateway(Broken()).fetch(AdRequest("r", AdSlot.LLM_WAIT, "s", AdContext()))[1], "error")


class Validity(unittest.TestCase):
    def run_case(self, c):
        billing = BillingType(c["billing"])
        camp = _camp(won(1_000_000), billing)
        camp.max_bid_mw = max(camp.max_bid_mw, c["price_mw"])
        st, book, tokens = rules.ValidityState(), rules.BudgetBook(), {}
        for s in c["steps"]:
            at = s.get("at_ms", 0)
            recv = s.get("recv_ms", at)
            if s["op"] == "serve":
                rec = _serve(s["token"], billing, c["price_mw"], 0, s["session"])
                st.serves[rec.serve_id] = rec
                book.reserve(camp, rec)
                tokens[s["token"]] = rules.sign_token(
                    ServeClaims(rec.serve_id, s["session"], rec.slot, 0, rules.TOKEN_TTL_MS), KEY)
                continue
            name = s["token"]
            if name.startswith("forged:"):
                tok = tokens[name[7:]]
                tok = tok[:-2] + ("AA" if tok[-2:] != "AA" else "BB")
            elif name in tokens:
                tok = tokens[name]
            else:
                tok = rules.sign_token(ServeClaims(name, "s9", AdSlot.LOBBY_BANNER, 0, rules.TOKEN_TTL_MS), KEY)
            claims, why = rules.verify_token(tok, KEY, recv)
            now = s.get("judge_ms", recv)
            if s["op"] == "impression":
                j = rules.judge_impression(st, claims, why, s["visible_ratio"], s["visible_ms"], recv, now)
                if j.status is JudgeStatus.VALID and billing is BillingType.CPM:
                    book.confirm(rules.charge_id(claims.serve_id, billing), f"imp_{at}")
            else:
                j = rules.judge_click(st, claims, why, recv, now)
                if j.status is JudgeStatus.VALID:
                    if billing is BillingType.CPC or j.implied_impression:
                        book.confirm(rules.charge_id(claims.serve_id, billing), f"clk_{at}")
                self.assertEqual(j.suspect, s.get("expect_suspect", False), s)
                self.assertEqual(j.implied_impression, s.get("expect_implied", False), s)
            got = j.status.value + (f":{j.reason.value}" if j.reason else "")
            self.assertEqual(got, s["expect"], s)
        confirmed = sum(r.amount_mw for r in book.rows.values() if r.state is ChargeState.CONFIRMED)
        self.assertEqual(confirmed, c["expect_confirmed_mw"])

    def test_cases(self):
        for c in load_json("fixtures/ads/validity_cases.json"):
            with self.subTest(case=c["case_id"]):
                self.run_case(c)


class GamePathProtection(unittest.TestCase):
    def req(self, i=0):
        return AdRequest(f"r{i}", AdSlot.LLM_WAIT, "sess", AdContext())

    def test_fake_is_provider(self):
        self.assertIsInstance(FakeAdProvider(), AdProvider)

    def test_ok_and_empty(self):
        self.assertEqual(AdGateway(FakeAdProvider()).fetch(self.req())[1], "ok")
        self.assertEqual(AdGateway(FakeAdProvider(empty=True)).fetch(self.req())[1], "empty")

    def test_timeout_returns_empty_fast(self):
        gw = AdGateway(FakeAdProvider(latency_ms=1000), total_timeout_ms=100)
        t0 = time.monotonic()
        resp, code = gw.fetch(self.req())
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual((resp.filled, code), (False, "timeout"))

    def test_circuit_breaker(self):
        now = [0.0]
        prov = FakeAdProvider(fail=True)
        gw = AdGateway(prov, CircuitBreaker(clock_ms=lambda: now[0]))
        codes = [gw.fetch(self.req(i))[1] for i in range(7)]
        self.assertEqual(codes, ["error"] * 5 + ["circuit_open"] * 2)
        self.assertEqual(len(prov.calls), 5)          # 열린 동안 호출 생략, 재시도 없음
        now[0] = 30_001
        prov.fail = False
        self.assertEqual(gw.fetch(self.req(9))[1], "ok")


class SyntheticAds(unittest.TestCase):
    def test_contract_and_isolation(self):
        from tools.gen_synthetic_ads import generate_ads
        serves, imps, clicks, truth, book = generate_ads(3000, 5)
        for e in serves[:50]:
            strict(AdServeEvent, serde.to_jsonable(e))
        for e in imps[:50]:
            strict(AdImpressionEvent, serde.to_jsonable(e))
        for e in clicks:
            strict(AdClickEvent, serde.to_jsonable(e))
            self.assertEqual(e.source, "synthetic")
        self.assertTrue(all(cid.startswith("syn:") for cid, _ in book.totals))
        self.assertTrue(all(e.creative_id.startswith("syn:") for e in serves if e.creative_id))
        # 무효 클릭은 과금하지 않는다
        self.assertTrue(all(c.charge_mw == 0 for c in clicks if c.status is not JudgeStatus.VALID))
        self.assertTrue(any(c.status is JudgeStatus.INVALID for c in clicks))
        # 이벤트 기반 건별 대사 = 원장
        ledger = {r.charge_id: r.amount_mw for r in book.rows.values() if r.state is ChargeState.CONFIRMED}
        delta: dict[str, list[int]] = {}
        for e in imps + clicks:
            if e.charge_id:
                delta.setdefault(e.charge_id, []).append(e.charge_mw)
        self.assertEqual(rules.reconcile(ledger, delta),
                         {"ledger_only": [], "delta_only": [], "duplicate": [], "amount_mismatch": []})


if __name__ == "__main__":
    unittest.main()


class GatewayReviewRegressions(unittest.TestCase):
    """코드 리뷰 지적 회귀: 시간 초과 호출도 끝날 때까지 동시 상한에 포함, 반열림은 시험 호출 1개."""

    def test_timed_out_call_keeps_slot(self):
        gw = AdGateway(FakeAdProvider(latency_ms=400), total_timeout_ms=50, max_concurrent=1)
        r = AdRequest("r1", AdSlot.LLM_WAIT, "s", AdContext())
        self.assertEqual(gw.fetch(r)[1], "timeout")
        self.assertEqual(gw.fetch(AdRequest("r2", AdSlot.LLM_WAIT, "s", AdContext()))[1], "busy")
        time.sleep(0.5)
        gw.provider.latency_ms = 0
        self.assertEqual(gw.fetch(AdRequest("r3", AdSlot.LLM_WAIT, "s", AdContext()))[1], "ok")

    def test_half_open_single_probe(self):
        now = [0.0]
        br = CircuitBreaker(clock_ms=lambda: now[0])
        g0 = br.allow()
        for _ in range(5):
            br.record(False, g0)
        br.record(True, g0)                   # 열리기 전에 시작한 늦은 성공은 무시
        self.assertIsNone(br.allow())
        now[0] = 30_001
        g1 = br.allow()
        self.assertIsNotNone(g1)
        self.assertIsNone(br.allow())         # 두 번째 동시 요청은 막힘
        br.record(False, g1)                  # 시험 실패 → 곧바로 다시 열림
        self.assertIsNone(br.allow())
        now[0] = 60_002
        g2 = br.allow()
        br.record(True, g2)
        self.assertIsNotNone(br.allow())
