"""광고 요청 흐름: 캠페인 필터·소재 선택·경매·예산 예약."""
from __future__ import annotations

import hashlib
import math
import random
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from ads.app.bid_update import effective_bid
from ads.domain.events import ServeCandidate
from ads.domain.model import (
    AdCreativeView, AdRequest, AdResponse, Bidder, BillingType, ServeClaims, ServeRecord,
    slot_image_path,
)
from ads.domain.rules import (
    BudgetBook, CtrEstimate, MIN_RANK_CTR, TOKEN_TTL_MS, ValidityState, charge_amount_mw, ctr_posterior,
    ecpm_mw, frequency_ok, run_auction, sign_token, targeting_ok, thompson_ctr,
)

POLICY_VERSION = "auction-v0.1"
CTR_VERSION = "beta-prior-v0.1"
ATTRIBUTION_TZ = timezone(timedelta(hours=9), "Asia/Seoul")


class RequestConflict(ValueError):
    """같은 요청 ID를 다른 입력으로 재사용했다."""


class AdService:
    """공통 광고 규칙을 실행한다. 운영은 영속 저장소, 격리 시험은 메모리를 사용한다."""

    def __init__(self, campaigns, policies, key: bytes, clock=None, *, book=None, store=None):
        self.campaigns, self.policies, self.key = campaigns, policies, key
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self.book, self.state = book if book is not None else BudgetBook(), ValidityState()
        self.store = store
        self.lock = threading.RLock()
        self.responses, self.auctions, self.requests, self.candidates = {}, {}, {}, {}
        self.frequencies, self.load_tests, self.pending = {}, {}, {}
        self.last_result = None
        self.ctr, self.pacing, self.exposure_counts = {}, {}, {}
        self.ctr_version, self.policy_version = CTR_VERSION, POLICY_VERSION
        self.ctr_canary, self.ctr_canary_version, self.ctr_canary_source = {}, "", ""
        self.ctr_baseline = ("", "", {})
        self.ctr_lookup_ns = self.ctr_lookup_count = self.ctr_lookup_misses = 0
        self.bids, self.bid_version = {}, ""
        self.auction_bid_version = ""

    def estimate(self, creative, policy, posterior=None):
        """Return a loaded posterior or the slot prior for an unseen creative."""
        params = (self.ctr if posterior is None else posterior).get((creative.creative_id, policy.slot))
        return CtrEstimate(*params) if params else ctr_posterior(policy, 0, 0)

    def rank_ctr(self, campaign, creative, policy, rng, estimate=None):
        """Sample learned CTR for CPC ranking and within-campaign CPM selection."""
        sample = thompson_ctr(estimate or self.estimate(creative, policy), rng)
        return max(sample, MIN_RANK_CTR) if campaign.billing is BillingType.CPC else sample

    def in_ctr_canary(self, req):
        """Assign 20% of one source's non-load-test requests to the contextual model."""
        if req.load_test or req.source != self.ctr_canary_source:
            return False
        return int.from_bytes(hashlib.sha256(req.request_id.encode()).digest()[:8], "big") % 5 == 0

    def serve(self, req: AdRequest) -> AdResponse:
        """동일 입력은 동일 응답을 반환하고 예산을 한 번만 예약한다."""
        if self.store is not None:
            return self.store.serve(self, req)
        return self._serve(req)

    def _serve(self, req, frequency=None, exposure_counts=None):
        with self.lock:
            if req.request_id in self.responses:
                if self.requests[req.request_id] != asdict(req):
                    raise RequestConflict("request_id reused with different input")
                return self.responses[req.request_id]
            now = self.clock()
            if self.store is None:
                self.book.release_expired(now)
            attributed = datetime.fromtimestamp(now / 1000, ATTRIBUTION_TZ)
            day = attributed.date().isoformat()
            policy = self.policies[req.slot]
            request_hash = hashlib.sha256(req.request_id.encode())
            rng = random.Random(int.from_bytes(request_hash.digest(), "big"))
            ctr_version = self.ctr_version
            posterior = self.ctr
            pinned = req.source == self.ctr_baseline[0]
            if pinned:
                _, ctr_version, posterior = self.ctr_baseline
            contextual = self.ctr_canary if self.in_ctr_canary(req) else {}
            self.ctr_lookup_ns = self.ctr_lookup_count = self.ctr_lookup_misses = 0
            self.auction_bid_version = ""
            campaigns, creatives, bidders, exploring = {}, {}, [], {}
            candidates = []
            self.candidates[req.request_id] = candidates
            for c in sorted(self.campaigns(), key=lambda c: c.campaign_id):
                synthetic = req.source == "synthetic"
                if c.campaign_id.startswith("syn:") != synthetic:
                    continue
                if not targeting_ok(c, req.slot, req.context, day):
                    continue
                counts = (exposure_counts(c.campaign_id, day) if exposure_counts else
                          self.exposure_counts.get((c.campaign_id, day), {}))
                explore = counts.get("explorations", 0) < math.ceil(.05 * (counts.get("impressions", 0) + 1))
                choices = []
                for cr in c.creatives:
                    if cr.creative_id.startswith("syn:") != synthetic:
                        continue
                    count = (frequency(cr.creative_id) if frequency is not None
                             else self.frequencies.get((req.session_pseudonym, cr.creative_id), 0))
                    if frequency_ok(count, policy):
                        p = None
                        if contextual:
                            started = time.perf_counter_ns()
                            p = contextual.get((req.slot.value, cr.creative_id, req.context.outcome,
                                                req.context.preset_id, attributed.hour))
                            self.ctr_lookup_ns += time.perf_counter_ns() - started
                            self.ctr_lookup_count += 1
                            self.ctr_lookup_misses += p is None
                        estimate = self.estimate(cr, policy, posterior) if pinned else self.estimate(cr, policy)
                        if p is not None:
                            strength = estimate.alpha + estimate.beta
                            estimate = CtrEstimate(p * strength, (1 - p) * strength)
                            ctr_version = self.ctr_canary_version
                        rank = (self.rank_ctr(c, cr, policy, rng, estimate) if pinned or p is not None
                                else self.rank_ctr(c, cr, policy, rng))
                        choices.append((rank, cr, estimate.mean))
                if not choices:
                    continue
                if explore:
                    rank, cr, mean = min(choices, key=lambda item: (
                        counts.get("creatives", {}).get(item[1].creative_id, 0), item[1].creative_id))
                else:
                    rank, cr, mean = max(choices, key=lambda item: (item[0], item[1].creative_id))
                exploring[c.campaign_id] = explore
                updated_bid = effective_bid(c, self.bids.get(c.campaign_id))
                if updated_bid is not None:
                    self.auction_bid_version = self.bid_version
                bid = c.max_bid_mw if updated_bid is None else updated_bid
                bidder = Bidder(c.campaign_id, cr.creative_id, c.billing, bid, rank)
                candidate = ServeCandidate(c.campaign_id, cr.creative_id, c.billing, bid, rank, mean, ecpm_mw(bidder))
                candidates.append(candidate)
                if not self.book.can_reserve(c, day, charge_amount_mw(c.billing, c.max_bid_mw)):
                    candidate.filtered = "budget"
                    continue
                if candidate.ecpm_mw < policy.reserve_ecpm_mw:
                    candidate.filtered = "reserve"
                if rng.random() >= self.pacing.get((c.campaign_id, day), 1.):
                    candidate.filtered = "pacing"
                    continue
                campaigns[c.campaign_id], creatives[cr.creative_id] = c, cr
                bidders.append(bidder)
            auction = run_auction(bidders, policy.reserve_ecpm_mw, req.request_id)
            self.auctions[req.request_id] = auction
            out = AdResponse(req.request_id, False, policy_version=self.policy_version, ctr_version=ctr_version)
            if auction.winner:
                winner = auction.winner
                c, cr = campaigns[winner.campaign_id], creatives[winner.creative_id]
                # Synthetic callers must never choose an existing human serve UUID.
                serve_id = (f"syn:{req.request_id.removeprefix('syn:')[:80]}:{request_hash.hexdigest()[:32]}"
                            if req.source == "synthetic" else str(uuid.uuid4()))
                record = ServeRecord(serve_id, req.request_id, c.campaign_id, cr.creative_id,
                    req.slot, c.billing, auction.price_mw, winner.rank_ctr, req.session_pseudonym, now, day,
                    req.source, bid_version=self.auction_bid_version,
                    exploration=exploring[c.campaign_id])
                if self.book.reserve(c, record):
                    claims = ServeClaims(record.serve_id, req.session_pseudonym, req.slot, now, now + TOKEN_TTL_MS)
                    token = sign_token(claims, self.key)
                    self.state.serves[record.serve_id] = record
                    self.load_tests[record.serve_id] = req.load_test
                    key = (req.session_pseudonym, cr.creative_id)
                    self.frequencies[key] = self.frequencies.get(key, 0) + 1
                    out = AdResponse(req.request_id, True,
                        AdCreativeView(cr.creative_id, cr.title, cr.body, slot_image_path(cr.image_path, req.slot)),
                        token, "/ads/v1/impression", f"/ads/v1/click?t={token}", self.policy_version, ctr_version)
                else:
                    next(candidate for candidate in candidates
                         if candidate.campaign_id == c.campaign_id).filtered = "budget"
            self.requests[req.request_id] = asdict(req)
            self.responses[req.request_id] = out
            return out
