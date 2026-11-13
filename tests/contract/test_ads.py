"""광고 계약 테스트: 2등 가격, 예산 예약·확정·해제, 유효 판정(클릭 먼저 도착·무효 공격), 대사, 게임 쪽 포트 보호."""
from __future__ import annotations

import time
import unittest

from ads.domain import rules
from ads.domain.events import TOPIC_REJUDGE, AdClickEvent, AdImpressionEvent, AdRejudgeEvent, AdServeEvent
from ads.domain.model import (
    SLOT_SIZES, AdContext, AdRequest, AdSlot, Bidder, BillingType, Campaign, CampaignStatus, ChargeState, InvalidReason, JudgeStatus,
    ServeClaims, ServeRecord, SlotPolicy, Targeting, legacy_slot_image_url, slot_image_path, won,
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
        self.assertEqual(SLOT_SIZES[AdSlot.REVIEW_SIDE], (300, 250))

    def test_three_slots_no_meeting_screen_slot(self):
        # 11/6 PM 결정(ads-0.5): llm_wait 삭제. 회의·투표·밤 화면 지면은 없다
        self.assertEqual({s.value for s in AdSlot}, {"lobby_banner", "result_banner", "review_side"})
        with self.assertRaises(ValueError):
            AdSlot("llm_wait")

    def test_fixture_copy_matches(self):
        d = load_json("fixtures/ads/campaigns.json")
        self.assertEqual({k: tuple(v) for k, v in d["slot_sizes"].items()},
                         {s.value: wh for s, wh in SLOT_SIZES.items()})

    def test_slot_image_path(self):
        self.assertEqual(slot_image_path("/static/promo/gat.png", AdSlot.LOBBY_BANNER), "/static/promo/gat__wide.png")
        self.assertEqual(slot_image_path("/static/promo/gat.png", "review_side"), "/static/promo/gat__box.png")
        # 옛 기본 경로(운영 DB 갱신 전)도 새 주소로 바꾼다. 브라우저 광고 차단이 /static/ads/·규격 표기를 막는다.
        self.assertEqual(slot_image_path("/static/ads/gat.png", "review_side"), "/static/promo/gat__box.png")
        self.assertEqual(legacy_slot_image_url("/static/ads/gat__300x250.png"), "/static/promo/gat__box.png")
        self.assertEqual(legacy_slot_image_url("/static/ads/gukbap_a__728x90.png"), "/static/promo/gukbap_a__wide.png")
        self.assertIsNone(legacy_slot_image_url("/static/promo/gat__box.png"))
        self.assertIsNone(legacy_slot_image_url("/static/ads/gat.png"))

    def test_one_file_per_creative_and_size(self):
        d = load_json("fixtures/ads/campaigns.json")
        for c in (strict(Campaign, x) for x in d["campaigns"]):
            for cr in c.creatives:
                paths = {slot_image_path(cr.image_path, s) for s in c.slots}
                self.assertEqual(len(paths), len({SLOT_SIZES[s] for s in c.slots}), cr.creative_id)

    def test_fake_returns_slot_sized_path(self):
        req = AdRequest("r1", AdSlot.REVIEW_SIDE, "p1", AdContext("base6", None, False, 12))
        resp = FakeAdProvider().request_ad(req, 50, 120)
        self.assertTrue(resp.creative.image_path.endswith("__box.png"))


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


def _serve(sid: str, billing: BillingType, price_mw: int, at_ms: int = 0, session: str = "s",
           day: str = "2026-11-06", slot: AdSlot = AdSlot.LOBBY_BANNER) -> ServeRecord:
    return ServeRecord(sid, "req_" + sid, "cp", "cr", slot, billing, price_mw, 0.02, session, at_ms, day)


class CampaignBudgetRegistration(unittest.TestCase):
    """등록 검증은 최대 입찰가를 쓰며 CPM은 밀리원 단위로 내림한다(11/7 PM 결정, 나머지 이월 폐지)."""

    def test_maximum_charge_boundary_for_cpc_and_cpm(self):
        for billing, bid, budget, accepted in (
                (BillingType.CPC, 300000, 100000, False),
                (BillingType.CPC, 300000, 300000, True),
                (BillingType.CPM, 300000, 100000, True),
                (BillingType.CPM, 300999, 299, False),
                (BillingType.CPM, 300999, 300, True)):
            with self.subTest(billing=billing, bid=bid, budget=budget):
                if accepted:
                    rules.validate_campaign_budget(billing, bid, budget)
                else:
                    with self.assertRaises(ValueError):
                        rules.validate_campaign_budget(billing, bid, budget)


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

    def test_cpm_floor_per_impression_bounded_loss(self):
        """11/7 PM 결정: 1건 금액 = floor(청구 CPM/1000). 비정수 CPM 1000건의 누적 손실은 1000밀리원 미만."""
        camp = _camp(won(1_000_000), BillingType.CPM)
        camp.max_bid_mw = won(5000)
        book = rules.BudgetBook()
        for i in range(1000):
            rec = _serve(f"s{i}", BillingType.CPM, 100_999)
            self.assertTrue(book.reserve(camp, rec))
            self.assertEqual(book.rows[f"s{i}:impression"].amount_mw, 100)   # 예약액 = 확정액
            self.assertTrue(book.confirm(rules.charge_id(rec.serve_id, BillingType.CPM), f"e{i}"))
            self.assertEqual(book.rows[f"s{i}:impression"].amount_mw, 100)
        committed = book.committed_mw("cp", "2026-11-06")
        self.assertEqual(committed, 100_000)
        self.assertLess(100_999 - committed, 1000)                          # 정확값 100,999 대비 손실

    def test_cpm_refund_across_days_never_negative(self):
        """D-06-02 보고 재현: 11/6 노출 확정 → 11/7 같은 가격 확정 → 11/6 건 환입. 어느 날도 음수가 아니다."""
        camp = _camp(won(1_000_000), BillingType.CPM)
        camp.max_bid_mw = won(5000)
        book = rules.BudgetBook()
        a = _serve("a", BillingType.CPM, 100_500, day="2026-11-06")
        b = _serve("b", BillingType.CPM, 100_500, day="2026-11-07")
        for rec in (a, b):
            self.assertTrue(book.reserve(camp, rec))
            self.assertTrue(book.confirm(f"{rec.serve_id}:impression", "e" + rec.serve_id))
        self.assertEqual((book.committed_mw("cp", "2026-11-06"), book.committed_mw("cp", "2026-11-07")), (100, 100))
        row = book.refund("a:impression", "ea_rejudged")
        self.assertEqual((row.amount_mw, row.attribution_day), (-100, "2026-11-06"))   # 원래 금액의 부호 반전, 원래 귀속일
        self.assertEqual((book.committed_mw("cp", "2026-11-06"), book.committed_mw("cp", "2026-11-07")), (0, 100))

    def test_refund_per_slot_sum_never_negative(self):
        """같은 날 다른 지면 두 건 중 하나를 환입해도 지면별 합이 음수가 아니다(1건 금액이 순서와 무관)."""
        camp = _camp(won(1_000_000), BillingType.CPM)
        camp.max_bid_mw = won(5000)
        camp.slots = [AdSlot.LOBBY_BANNER, AdSlot.RESULT_BANNER]
        book = rules.BudgetBook()
        recs = [_serve("a", BillingType.CPM, 100_500, slot=AdSlot.LOBBY_BANNER),
                _serve("b", BillingType.CPM, 100_500, slot=AdSlot.RESULT_BANNER)]
        for rec in recs:
            book.reserve(camp, rec)
            book.confirm(f"{rec.serve_id}:impression", "e" + rec.serve_id)
        book.refund("a:impression", "ea_rejudged")
        slot_of = {f"{r.serve_id}:impression": r.slot for r in recs}
        per_slot: dict[AdSlot, int] = {}
        for cid, row in book.rows.items():
            slot = slot_of[cid.removesuffix(rules.REFUND_SUFFIX)]
            per_slot[slot] = per_slot.get(slot, 0) + row.amount_mw
        self.assertEqual(per_slot, {AdSlot.LOBBY_BANNER: 0, AdSlot.RESULT_BANNER: 100})

    def test_refund_only_once_and_only_original_charge(self):
        camp = _camp(won(1_000), BillingType.CPC)
        book = rules.BudgetBook()
        book.reserve(camp, _serve("a", BillingType.CPC, won(50)))
        book.confirm("a:click", "ea")
        self.assertEqual(book.refund("a:click", "r1").amount_mw, -won(50))
        self.assertIsNone(book.refund("a:click", "r2"))              # 중복 환입 거부
        self.assertIsNone(book.refund("a:click:refund", "r3"))       # 환입 행 재환입 거부
        self.assertEqual(book.committed_mw("cp", "2026-11-06"), 0)

    def test_cpm_below_floor_and_zero_charge_rejected(self):
        camp = _camp(won(1_000_000), BillingType.CPM)
        book = rules.BudgetBook()
        with self.assertRaises(ValueError):
            book.reserve(camp, _serve("low", BillingType.CPM, 99_999))      # 하한(100원 CPM) 미만
        self.assertTrue(book.reserve(camp, _serve("ok", BillingType.CPM, 100_000)))
        cpc = _camp(won(1_000_000), BillingType.CPC)
        with self.assertRaises(ValueError):
            book.reserve(cpc, _serve("zero", BillingType.CPC, 0))           # 0원 과금 금지

    def test_slot_policy_reserve_floor(self):
        with self.assertRaises(ValueError):
            SlotPolicy(AdSlot.LOBBY_BANNER, 99_999, 0.02)
        self.assertEqual(SlotPolicy(AdSlot.LOBBY_BANNER, 100_000, 0.02).reserve_ecpm_mw, 100_000)

    def test_reconcile_net_of_refunds(self):
        ledger = {"a:click": 50, "a:click:refund": -50,               # 환입으로 상계, Delta에 없음 → 일치
                  "b:impression": 100, "b:impression:refund": -100,   # 상계됐는데 Delta에 남음 → 금액 불일치
                  "c:click": 30,                                      # Delta와 같음 → 일치
                  "z:click:refund": -40}                              # 원과금 없는 환입
        delta = {"b:impression": [100], "c:click": [30]}
        self.assertEqual(rules.reconcile(ledger, delta),
                         {"ledger_only": [], "delta_only": [], "duplicate": [], "amount_mismatch": ["b:impression"],
                          "orphan_refund": ["z:click:refund"]})

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
        self.assertEqual(AdGateway(Broken()).fetch(AdRequest("r", AdSlot.REVIEW_SIDE, "s", AdContext()))[1], "error")


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
        return AdRequest(f"r{i}", AdSlot.REVIEW_SIDE, "sess", AdContext())

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


class RejudgeEvent(unittest.TestCase):
    def test_rejudge_event_contract(self):
        e = AdRejudgeEvent("rj_1", "ad_syn_1_0000001", "sv_1:click", InvalidReason.DUPLICATE, rules.JUDGE_VERSION,
                           1_000, "synthetic", "ads-0.5")
        self.assertEqual(TOPIC_REJUDGE, "ad.rejudge")
        strict(AdRejudgeEvent, serde.to_jsonable(e))


class SyntheticAds(unittest.TestCase):
    def test_contract_and_isolation(self):
        from tools.gen_synthetic_ads import generate_ads
        serves, imps, clicks, truth, book = generate_ads(3000, 5)
        for e in serves[:50]:
            strict(AdServeEvent, serde.to_jsonable(e))
        # serve에 요청 맥락이 남는다(시나리오 6: 판 결과별 CTR은 silver가 serve_id로 조인)
        self.assertTrue(all(e.context is not None for e in serves))
        self.assertTrue(any(e.context.outcome is not None for e in serves if e.filled))
        self.assertEqual(serde.to_jsonable(serves[0])["bid_version"], "")
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
                         {"ledger_only": [], "delta_only": [], "duplicate": [], "amount_mismatch": [],
                          "orphan_refund": []})


if __name__ == "__main__":
    unittest.main()


class GatewayReviewRegressions(unittest.TestCase):
    """코드 리뷰 지적 회귀: 시간 초과 호출도 끝날 때까지 동시 상한에 포함, 반열림은 시험 호출 1개."""

    def test_timed_out_call_keeps_slot(self):
        gw = AdGateway(FakeAdProvider(latency_ms=400), total_timeout_ms=50, max_concurrent=1)
        r = AdRequest("r1", AdSlot.REVIEW_SIDE, "s", AdContext())
        self.assertEqual(gw.fetch(r)[1], "timeout")
        self.assertEqual(gw.fetch(AdRequest("r2", AdSlot.REVIEW_SIDE, "s", AdContext()))[1], "busy")
        time.sleep(0.5)
        gw.provider.latency_ms = 0
        self.assertEqual(gw.fetch(AdRequest("r3", AdSlot.REVIEW_SIDE, "s", AdContext()))[1], "ok")

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
