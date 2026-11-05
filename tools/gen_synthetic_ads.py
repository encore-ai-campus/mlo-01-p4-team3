"""합성 광고 트래픽(표준 라이브러리만). gen_synthetic.py의 --ads 옵션이 호출한다. 기준: 기획서 14.3~14.6

- 숨긴 실제 CTR(소재 × 지면 × 판 결과)로 클릭하는 가상 사용자.
- 경매·예산 예약/확정/해제·페이싱·판정은 ads.domain.rules 기준 구현을 쓴다.
- source="synthetic". 캠페인은 "syn:" 접두어 복제본을 써서 예산·사전분포·페이싱 상태를 실제와 격리한다.
- 무효 공격(토큰 재사용 반복 클릭, 위조 토큰)과 클릭 먼저 도착, 비콘 손실을 섞는다.
학습용 생성기다. 평가용(CTR 변화, 피로, 지연 클릭 등)은 D가 따로 만든다(기획서 14.6).
"""
from __future__ import annotations

import copy
import random

from ads.domain import rules
from ads.domain.events import AdClickEvent, AdImpressionEvent, AdServeEvent, ServeCandidate
from ads.domain.model import (
    AD_CONTRACT_VERSION, AdContext, AdSlot, Bidder, BillingType, GameOutcome, JudgeStatus, ServeClaims,
    ServeRecord,
)
from tools.build_ads_fixtures import campaigns, slot_policies

SYNTHETIC_KEY = b"synthetic-only-key"   # 합성 전용. 실제 서명 키는 인프라 비밀 저장소에서 받는다
DAY = "2026-11-06"
OUTCOME_LIFT = {None: 1.0, GameOutcome.WIN: 0.7, GameOutcome.LOSE: 1.6}


def synthetic_campaigns():
    out = []
    for c in campaigns():
        c = copy.deepcopy(c)
        c.campaign_id = "syn:" + c.campaign_id
        for cr in c.creatives:
            cr.campaign_id = c.campaign_id
            cr.creative_id = "syn:" + cr.creative_id     # CTR·빈도 키도 실제와 섞이지 않게
        out.append(c)
    return out


class _Sim:
    def __init__(self, seed: int, invalid_rate: float):
        self.seed = seed
        self.rng = random.Random(seed)
        self.invalid_rate = invalid_rate
        self.camps = synthetic_campaigns()
        self.policies = {p.slot: p for p in slot_policies()}
        self.hidden = {}
        for c in self.camps:
            for cr in c.creatives:
                for sl in c.slots:
                    base = self.rng.uniform(0.005, 0.08)
                    for oc, lift in OUTCOME_LIFT.items():
                        self.hidden[(cr.creative_id, sl, oc)] = min(base * lift, 0.5)
        self.counts: dict[tuple, list[int]] = {}        # (creative, slot) -> [유효 노출, 클릭된 노출]
        self.pacing = {c.campaign_id: 1.0 for c in self.camps}
        self.freq: dict[tuple, int] = {}
        self.book = rules.BudgetBook()
        self.st = rules.ValidityState()
        self.serves: list[AdServeEvent] = []
        self.imps: list[AdImpressionEvent] = []
        self.clicks: list[AdClickEvent] = []
        self.n_ev = 0
        self.last_pacing_minute = 0

    def base(self, rec: ServeRecord, t: int, recv: int) -> dict:
        self.n_ev += 1
        return dict(event_id=f"ad_syn_{self.seed}_{self.n_ev:07d}", request_id=rec.request_id, serve_id=rec.serve_id,
                    occurred_at_ms=t, received_at_ms=recv, slot=rec.slot, campaign_id=rec.campaign_id,
                    creative_id=rec.creative_id, session_pseudonym=rec.session_pseudonym, source="synthetic",
                    policy_version="ads-policy-0.2", ctr_version="beta-0.2", contract_version=AD_CONTRACT_VERSION,
                    billing=rec.billing, price_mw=rec.price_mw)

    def update_pacing(self, minute: int, now_ms: int) -> None:
        """집계 주기(5분)마다 예약 해제와 페이싱 조정."""
        if minute - self.last_pacing_minute < 5:
            return
        self.last_pacing_minute = minute
        self.book.release_expired(now_ms)
        for c in self.camps:
            spent = self.book.committed_mw(c.campaign_id, DAY)
            self.pacing[c.campaign_id] = rules.next_pacing_probability(
                self.pacing[c.campaign_id], spent, c.daily_budget_mw, minute)

    def request(self, i: int, n: int) -> None:
        rng = self.rng
        minute = i * 1440 // max(n, 1)
        t = minute * 60_000 + i
        self.update_pacing(minute, t)
        slot = rng.choice(list(AdSlot))
        sess = f"syn_s{i // 40}_{rng.randrange(8)}"      # 세션은 시간이 지나며 새로 생긴다
        outcome = rng.choice([GameOutcome.WIN, GameOutcome.LOSE]) if slot in (AdSlot.RESULT_BANNER, AdSlot.REVIEW_SIDE) else None
        ctx = AdContext(rng.choice(["base6", "large8"]), outcome, rng.random() < 0.5, minute // 60)
        pol = self.policies[slot]
        req_id = f"req_{self.seed}_{i:07d}"
        cands, bidders = [], []
        for c in self.camps:
            if slot not in c.slots:
                continue
            # 캠페인 안 소재 선택도 톰슨 샘플링(CPM 탐색 규칙과 같음)
            sampled = []
            for cr in c.creatives:
                n_i, n_c = self.counts.get((cr.creative_id, slot), [0, 0])
                est = rules.ctr_posterior(pol, n_i, n_c)
                sampled.append((rules.thompson_ctr(est, rng), est.mean, cr))
            ctr, mean, cr = max(sampled, key=lambda x: x[0])
            b = Bidder(c.campaign_id, cr.creative_id, c.billing, c.max_bid_mw, ctr)
            score = rules.ecpm_mw(b)
            max_charge = rules.charge_amount_mw(c.billing, c.max_bid_mw)
            if not rules.targeting_ok(c, slot, ctx, DAY):
                why = "targeting"
            elif not self.book.can_reserve(c, DAY, max_charge):
                why = "budget"
            elif not rules.frequency_ok(self.freq.get((sess, cr.creative_id), 0), pol):
                why = "frequency"
            elif rng.random() > self.pacing[c.campaign_id]:
                why = "pacing"
            elif score < pol.reserve_ecpm_mw:
                why = "reserve"
            else:
                why = None
                bidders.append(b)
            cands.append(ServeCandidate(c.campaign_id, cr.creative_id, c.billing, c.max_bid_mw, ctr, mean, score, why))
        res = rules.run_auction(bidders, pol.reserve_ecpm_mw, req_id)
        w = res.winner
        rec = ServeRecord(f"sv_{self.seed}_{i:07d}", req_id, w.campaign_id if w else None, w.creative_id if w else None,
                          slot, w.billing if w else None, res.price_mw, w.rank_ctr if w else 0.0, sess, t, DAY, "synthetic")
        camp = next((c for c in self.camps if w and c.campaign_id == w.campaign_id), None)
        reserved = bool(w) and self.book.reserve(camp, rec)
        filled = reserved
        self.serves.append(AdServeEvent(
            **{**self.base(rec, t, t), "serve_id": rec.serve_id if filled else None},
            filled=filled, candidates=cands, threshold_ecpm_mw=res.threshold_ecpm_mw, rank_ctr=rec.rank_ctr,
            capped=res.capped, reserved_charge_id=rules.charge_id(rec.serve_id, rec.billing) if filled else None,
            reserved_mw=rules.charge_amount_mw(rec.billing, rec.price_mw) if filled else 0,
            latency_ms=rng.randrange(3, 30), context=ctx))
        if not filled:
            return
        self.st.serves[rec.serve_id] = rec
        self.freq[(sess, rec.creative_id)] = self.freq.get((sess, rec.creative_id), 0) + 1
        token = rules.sign_token(ServeClaims(rec.serve_id, sess, slot, t, t + rules.TOKEN_TTL_MS), SYNTHETIC_KEY)
        cid = rules.charge_id(rec.serve_id, rec.billing)

        def confirm_if(billing_event, j, ev):
            if j.status is JudgeStatus.VALID and rec.billing is billing_event and self.book.confirm(cid, ev["event_id"]):
                return cid, self.book.rows[cid].amount_mw
            return None, 0

        def impression(at, recv, ratio, ms, tok=token, implied=False):
            cl, why0 = rules.verify_token(tok, SYNTHETIC_KEY, recv)
            j = rules.judge_impression(self.st, cl, why0, ratio, ms, at, recv) if not implied else \
                rules.Judgement(JudgeStatus.VALID)
            b = self.base(rec, at, recv)
            ch_id, ch = confirm_if(BillingType.CPM, j, b)
            self.imps.append(AdImpressionEvent(**b, status=j.status, invalid_reason=j.reason,
                                               judge_version=rules.JUDGE_VERSION, charge_id=ch_id, charge_mw=ch,
                                               visible_ratio=ratio, visible_ms=ms, implied_by_click=implied))
            if j.status is JudgeStatus.VALID:
                self.counts.setdefault((rec.creative_id, slot), [0, 0])[0] += 1
            return j

        def click(at, recv, tok=token):
            cl, why0 = rules.verify_token(tok, SYNTHETIC_KEY, recv)
            j = rules.judge_click(self.st, cl, why0, at, recv)
            b = self.base(rec, at, recv)
            ch_id, ch = confirm_if(BillingType.CPC, j, b)
            self.clicks.append(AdClickEvent(**b, status=j.status, invalid_reason=j.reason,
                                            judge_version=rules.JUDGE_VERSION, charge_id=ch_id, charge_mw=ch,
                                            suspect=j.suspect, redirect_path="/ads/landing"))
            if j.implied_impression:
                impression(at, recv, 1.0, rules.VIEWABLE_MS, implied=True)
            if j.status is JudgeStatus.VALID:
                self.counts.setdefault((rec.creative_id, slot), [0, 0])[1] += 1
            return j

        will_click = rng.random() < self.hidden[(rec.creative_id, slot, outcome)]
        viewable = rng.random() < 0.85
        beacon_lost = rng.random() < 0.05
        if will_click and rng.random() < 0.2:                 # 클릭이 비콘보다 먼저 도착
            click(t + 800, t + 850)
            if not beacon_lost:
                impression(t + 1200, t + 1600, 1.0, 1200)
        else:
            j = None if beacon_lost else impression(t + 1500, t + 1550, 0.9 if viewable else 0.2, 1500)
            if will_click and (j is None or j.status is JudgeStatus.VALID):
                click(t + 4000, t + 4050)
        if rec.serve_id in self.st.clicked and rng.random() < self.invalid_rate:   # 무효 공격: 같은 토큰 반복 클릭
            click(t + 4300, t + 4310)
        if rng.random() < self.invalid_rate / 3:              # 무효 공격: 위조 토큰
            click(t + 4400, t + 4410, tok="forged." + token.split(".")[1])


def generate_ads(n_requests: int, seed: int, invalid_rate: float = 0.03):
    """반환: (serve, impression, click 이벤트, 숨긴 CTR {"소재|지면|결과": ctr}, 예산 원장 BudgetBook)."""
    sim = _Sim(seed, invalid_rate)
    for i in range(n_requests):
        sim.request(i, n_requests)
    sim.book.release_expired(10 ** 12)
    truth = {f"{k[0]}|{k[1].value}|{k[2].value if k[2] else '-'}": v for k, v in sim.hidden.items()}
    return sim.serves, sim.imps, sim.clicks, truth, sim.book
