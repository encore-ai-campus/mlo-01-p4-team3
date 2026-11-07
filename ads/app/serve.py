"""광고 요청 흐름: 캠페인 필터·소재 선택·경매·예산 예약."""
from __future__ import annotations

import hashlib
import random
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from ads.domain.events import ServeCandidate
from ads.domain.model import (
    AdCreativeView, AdRequest, AdResponse, Bidder, BillingType, ServeClaims, ServeRecord,
    slot_image_path,
)
from ads.domain.rules import (
    BudgetBook, TOKEN_TTL_MS, ValidityState, charge_amount_mw, ctr_posterior,
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

    def rank_ctr(self, campaign, creative, policy, rng):
        """현재는 지면 사전분포를 톰슨 추출한다. 관측 CTR 갱신은 D-08-02 범위다."""
        return thompson_ctr(ctr_posterior(policy, 0, 0), rng)

    def serve(self, req: AdRequest) -> AdResponse:
        """동일 입력은 동일 응답을 반환하고 예산을 한 번만 예약한다."""
        if self.store is not None:
            return self.store.serve(self, req)
        return self._serve(req)

    def _serve(self, req, frequency=None):
        with self.lock:
            if req.request_id in self.responses:
                if self.requests[req.request_id] != asdict(req):
                    raise RequestConflict("request_id reused with different input")
                return self.responses[req.request_id]
            now = self.clock()
            if self.store is None:
                self.book.release_expired(now)
            day = datetime.fromtimestamp(now / 1000, ATTRIBUTION_TZ).date().isoformat()
            policy = self.policies[req.slot]
            request_hash = hashlib.sha256(req.request_id.encode())
            rng = random.Random(int.from_bytes(request_hash.digest(), "big"))
            campaigns, creatives, bidders = {}, {}, []
            candidates = []
            self.candidates[req.request_id] = candidates
            for c in self.campaigns():
                synthetic = req.source == "synthetic"
                if c.campaign_id.startswith("syn:") != synthetic:
                    continue
                if not targeting_ok(c, req.slot, req.context, day):
                    continue
                choices = []
                for cr in c.creatives:
                    if cr.creative_id.startswith("syn:") != synthetic:
                        continue
                    count = (frequency(cr.creative_id) if frequency is not None
                             else self.frequencies.get((req.session_pseudonym, cr.creative_id), 0))
                    if frequency_ok(count, policy):
                        choices.append((self.rank_ctr(c, cr, policy, rng), cr))
                if not choices:
                    continue
                rank, cr = max(choices, key=lambda item: (item[0], item[1].creative_id))
                bidder = Bidder(c.campaign_id, cr.creative_id, c.billing, c.max_bid_mw, rank)
                candidate = ServeCandidate(c.campaign_id, cr.creative_id, c.billing, c.max_bid_mw,
                                          rank, policy.prior_ctr, ecpm_mw(bidder))
                candidates.append(candidate)
                if not self.book.can_reserve(c, day, charge_amount_mw(c.billing, c.max_bid_mw)):
                    candidate.filtered = "budget"
                    continue
                if candidate.ecpm_mw < policy.reserve_ecpm_mw:
                    candidate.filtered = "reserve"
                campaigns[c.campaign_id], creatives[cr.creative_id] = c, cr
                bidders.append(bidder)
            auction = run_auction(bidders, policy.reserve_ecpm_mw, req.request_id)
            self.auctions[req.request_id] = auction
            out = AdResponse(req.request_id, False, policy_version=POLICY_VERSION, ctr_version=CTR_VERSION)
            if auction.winner:
                winner = auction.winner
                c, cr = campaigns[winner.campaign_id], creatives[winner.creative_id]
                # Synthetic callers must never choose an existing human serve UUID.
                serve_id = (f"syn:{req.request_id.removeprefix('syn:')[:80]}:{request_hash.hexdigest()[:32]}"
                            if req.source == "synthetic" else str(uuid.uuid4()))
                record = ServeRecord(serve_id, req.request_id, c.campaign_id, cr.creative_id,
                    req.slot, c.billing, auction.price_mw, winner.rank_ctr, req.session_pseudonym, now, day,
                    req.source)
                if self.book.reserve(c, record):
                    claims = ServeClaims(record.serve_id, req.session_pseudonym, req.slot, now, now + TOKEN_TTL_MS)
                    token = sign_token(claims, self.key)
                    self.state.serves[record.serve_id] = record
                    self.load_tests[record.serve_id] = req.load_test
                    key = (req.session_pseudonym, cr.creative_id)
                    self.frequencies[key] = self.frequencies.get(key, 0) + 1
                    out = AdResponse(req.request_id, True,
                        AdCreativeView(cr.creative_id, cr.title, cr.body, slot_image_path(cr.image_path, req.slot)),
                        token, "/ads/v1/impression", f"/ads/v1/click?t={token}", POLICY_VERSION, CTR_VERSION)
                else:
                    next(candidate for candidate in candidates
                         if candidate.campaign_id == c.campaign_id).filtered = "budget"
            self.requests[req.request_id] = asdict(req)
            self.responses[req.request_id] = out
            return out
