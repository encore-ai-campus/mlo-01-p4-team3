"""광고 도메인 규칙(표준 라이브러리만): 후보 필터, eCPM, 2등 가격 경매, 예산 예약·확정·해제,
페이싱, CTR 추정, serve 토큰, 유효 판정, 건별 대사. 상세: docs/contracts/ads.md, 기획서 14.3~14.5.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import random
import threading
from dataclasses import asdict, dataclass, field
from typing import Optional

from .model import (
    AdContext, AdSlot, AuctionResult, Bidder, BillingType, Campaign, CampaignStatus, ChargeState,
    MIN_RESERVE_ECPM_MW, InvalidReason, JudgeStatus, LedgerRow, ServeClaims, ServeRecord, SlotPolicy,
)

JUDGE_VERSION = "judge-0.2"
TOKEN_TTL_MS = 30 * 60 * 1000          # serve 토큰 만료 = 클릭 인정 기한
GRACE_MS = 2 * 60 * 1000               # 짝 맞추기 유예
RELEASE_AFTER_MS = TOKEN_TTL_MS + GRACE_MS   # 예약 해제 시각(기한 직전 판정·확정과 경합하지 않도록 유예를 더함)
VIEWABLE_RATIO = 0.5
VIEWABLE_MS = 1000
SUSPECT_CLICK_MS = 1000                # 같은 세션의 직전 클릭과 이 간격 미만이면 의심 표시(무효 근거 아님)
MIN_RANK_CTR = 0.0001
REFUND_SUFFIX = ":refund"            # 환입 행 charge_id = 원과금 charge_id + 이 접미사
PACING_MIN_P, PACING_MAX_P, PACING_STEP = 0.05, 1.0, 0.2


# ---------------------------------------------------------------- 후보 필터
def targeting_ok(c: Campaign, slot: AdSlot, ctx: AdContext, today: str) -> bool:
    t = c.targeting
    return (c.status is CampaignStatus.ACTIVE and slot in c.slots and c.start_date <= today <= c.end_date
            and (not t.preset_ids or ctx.preset_id in t.preset_ids)
            and (not t.outcomes or ctx.outcome in t.outcomes)
            and (t.returning is None or t.returning == ctx.returning)
            and (not t.hours or ctx.hour in t.hours))


def frequency_ok(serves_in_session: int, policy: SlotPolicy) -> bool:
    return serves_in_session < policy.frequency_cap


# ---------------------------------------------------------------- CTR(베타 + 톰슨)
@dataclass
class CtrEstimate:
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


def ctr_posterior(policy: SlotPolicy, valid_impressions: int, clicked_impressions: int) -> CtrEstimate:
    """Beta(μκ + C, (1−μ)κ + N − C). N·C는 확정분 전체로 다시 계산한 값(누적 덧셈 금지)."""
    mu, k = policy.prior_ctr, policy.prior_strength
    return CtrEstimate(mu * k + clicked_impressions, (1 - mu) * k + max(valid_impressions - clicked_impressions, 0))


def thompson_ctr(est: CtrEstimate, rng: random.Random) -> float:
    return rng.betavariate(est.alpha, est.beta)


# ---------------------------------------------------------------- 경매(기획서 14.3)
def ecpm_mw(b: Bidder) -> int:
    """CPC = 입찰가 × 순위 CTR × 1000, CPM = 입찰가."""
    if b.billing is BillingType.CPM:
        return b.bid_mw
    return math.floor(b.bid_mw * max(b.rank_ctr, MIN_RANK_CTR) * 1000)


def _tiebreak(request_id: str, campaign_id: str) -> str:
    return hashlib.sha256(f"{request_id}|{campaign_id}".encode()).hexdigest()


def run_auction(bidders: list[Bidder], reserve_ecpm_mw: int, request_id: str) -> AuctionResult:
    """단일 슬롯 2등 가격.

    - eCPM < 지면 최저 eCPM이면 탈락. 남은 후보가 없으면 광고 없음.
    - 동점: sha256(request_id|campaign_id) 오름차순.
    - T = max(2등 eCPM, 지면 최저 eCPM). 후보 1개면 T = 지면 최저 eCPM.
    - 청구 단가: CPC = floor(T / (1000 × 순위 CTR)), CPM = T. 둘 다 최대 입찰가를 넘지 않는다.
    """
    ids = [b.campaign_id for b in bidders]
    if len(ids) != len(set(ids)):
        raise ValueError("캠페인당 후보는 하나(소재는 캠페인 안에서 먼저 고른다)")
    scores = {b.campaign_id: ecpm_mw(b) for b in bidders}
    eligible = sorted((b for b in bidders if scores[b.campaign_id] >= reserve_ecpm_mw),
                      key=lambda b: (-scores[b.campaign_id], _tiebreak(request_id, b.campaign_id)))
    if not eligible:
        return AuctionResult(None, scores=scores)
    w = eligible[0]
    second = scores[eligible[1].campaign_id] if len(eligible) > 1 else 0
    t = max(second, reserve_ecpm_mw)
    if w.billing is BillingType.CPM:
        raw = t
    else:
        raw = math.floor(t / (1000 * max(w.rank_ctr, MIN_RANK_CTR)))
    price = min(raw, w.bid_mw)
    return AuctionResult(w, scores[w.campaign_id], t, price, raw > w.bid_mw, scores)


def charge_amount_mw(billing: BillingType, price_mw: int) -> int:
    """단가 기준 1건 금액. CPC=단가, CPM=floor(단가/1000).
    등록·필터는 최대 입찰가를, 예약·확정은 실제 낙찰 단가를 넣는다. 1건 금액은 그 건의 단가만으로 정해진다
    (11/7 PM 결정: 나머지 이월 폐지. 밀리원 미만은 건마다 버린다. 손실은 CPM 노출 1건당 1밀리원 미만)."""
    return price_mw if billing is BillingType.CPC else price_mw // 1000


def validate_campaign_budget(billing: BillingType, max_bid_mw: int, daily_budget_mw: int) -> None:
    """등록 예산이 최대 입찰가 기준 1건 최대 청구액보다 작으면 거부한다."""
    minimum = charge_amount_mw(billing, max_bid_mw)
    if daily_budget_mw < minimum:
        raise ValueError(f"일 예산은 1건 최대 청구액 {minimum}밀리원 이상이어야 합니다.")


def charge_id(serve_id: str, billing: BillingType) -> str:
    return f"{serve_id}:{'click' if billing is BillingType.CPC else 'impression'}"


def reservation_amount(c: Campaign, serve: ServeRecord) -> int:
    """serve의 청구 단가와 캠페인을 검증하고 예약·확정 공통 금액을 반환한다."""
    if serve.campaign_id != c.campaign_id or serve.billing is not c.billing:
        raise ValueError("serve와 캠페인 불일치")
    if not (0 <= serve.price_mw <= c.max_bid_mw):
        raise ValueError("청구 단가는 0 이상, 최대 입찰가 이하")
    if serve.billing is BillingType.CPM and serve.price_mw < MIN_RESERVE_ECPM_MW:
        raise ValueError(f"CPM 청구 단가는 {MIN_RESERVE_ECPM_MW}밀리원(지면 최저 eCPM 하한) 이상")
    amount = charge_amount_mw(serve.billing, serve.price_mw)
    if amount <= 0:
        raise ValueError("1건 금액은 0보다 커야 한다(0원 과금 금지)")
    return amount


# ---------------------------------------------------------------- 예산: 예약 → 확정 → 해제
@dataclass
class BudgetBook:
    """MySQL 원장의 기준 동작(메모리). 각 연산은 원자적이다(여기서는 잠금).

    MySQL 구현: Primary 한 트랜잭션에서 (캠페인, 귀속일) 예산 행을 잠그고(SELECT … FOR UPDATE 또는
    조건부 UPDATE … WHERE committed + :amt <= budget) 원장 행(charge_id 유일)과 아웃박스 행을 함께 쓴다.
    판정 결과 저장·확정·아웃박스도 한 트랜잭션이다(판정만 되고 확정이 빠지는 경합 방지).
    """
    rows: dict[str, LedgerRow] = field(default_factory=dict)
    totals: dict[tuple[str, str], int] = field(default_factory=dict)   # (캠페인, 귀속일) -> 예약+확정(해제 제외, 환입 포함)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    def _add(self, r: LedgerRow, sign: int) -> None:
        k = (r.campaign_id, r.attribution_day)
        self.totals[k] = self.totals.get(k, 0) + sign * r.amount_mw

    def committed_mw(self, campaign_id: str, day: str) -> int:
        return self.totals.get((campaign_id, day), 0)

    def can_reserve(self, c: Campaign, day: str, amount_mw: int) -> bool:
        return self.committed_mw(c.campaign_id, day) + amount_mw <= c.daily_budget_mw

    def reserve(self, c: Campaign, serve: ServeRecord) -> bool:
        """1건 금액을 원자적으로 예약한다. 불변조건 위반은 ValueError, 중복·예산 초과는 False.

        불변조건: 청구 단가 0 이상·최대 입찰가 이하, CPM 단가 ≥ 최저 eCPM 하한, 1건 금액 > 0, 캠페인·과금 방식 일치."""
        amt = reservation_amount(c, serve)
        cid = charge_id(serve.serve_id, serve.billing)
        with self._lock:
            if cid in self.rows or not self.can_reserve(c, serve.attribution_day, amt):
                return False
            self.rows[cid] = LedgerRow(cid, c.campaign_id, amt, ChargeState.RESERVED, serve.attribution_day,
                                       billing=serve.billing, price_mw=serve.price_mw,
                                       release_after_ms=serve.served_at_ms + RELEASE_AFTER_MS)
            self._add(self.rows[cid], 1)
            return True

    def confirm(self, cid: str, source_event_id: str) -> bool:
        """확정하면 True(확정액 = 예약액 = rows[cid].amount_mw). 이미 확정·해제·없음이면 False."""
        with self._lock:
            r = self.rows.get(cid)
            if r is None or r.state is not ChargeState.RESERVED:
                return False
            r.state, r.source_event_id = ChargeState.CONFIRMED, source_event_id
            return True

    def release_expired(self, now_ms: int) -> list[str]:
        """해제 시각(serve + 클릭 인정 기한 + 판정 유예)이 지난 예약을 해제한다. serve 기록 없이 원장만으로 판단."""
        out = []
        with self._lock:
            for r in self.rows.values():
                if r.state is ChargeState.RESERVED and now_ms > r.release_after_ms:
                    r.state = ChargeState.RELEASED
                    self._add(r, -1)
                    out.append(r.charge_id)
        return out

    def refund(self, cid: str, reason_event_id: str) -> Optional[LedgerRow]:
        """확정 뒤 무효로 다시 판정된 건: 원래 행은 두고 음수 환입 행(원래 금액의 부호 반전)을 추가한다.

        원과금(kind=charge)만 대상이고 원과금당 1회다. 환입 행은 원과금의 귀속일에 기록된다.
        MySQL은 환입 charge_id(`<charge_id>:refund`) 유일 제약과 예산 행 잠금을 같은 트랜잭션에 둔다."""
        with self._lock:
            r = self.rows.get(cid)
            rid = f"{cid}{REFUND_SUFFIX}"
            if r is None or r.kind != "charge" or r.state is not ChargeState.CONFIRMED or rid in self.rows:
                return None
            row = LedgerRow(rid, r.campaign_id, -r.amount_mw, ChargeState.CONFIRMED, r.attribution_day,
                            reason_event_id, "refund", r.billing, r.price_mw)
            self.rows[rid] = row
            self._add(row, 1)
            return row


# ---------------------------------------------------------------- 페이싱
def target_fraction(minute_of_day: int, curve: Optional[list[float]] = None) -> float:
    """목표 소진 비율. curve=None이면 균등. curve는 시간(24칸)별 트래픽 비중(트래픽 비례 곡선)."""
    m = min(max(minute_of_day, 0), 1440)
    if curve is None:
        return m / 1440
    total = sum(curve)
    h, rest = divmod(m, 60)
    done = sum(curve[:h]) + (curve[h] * rest / 60 if h < 24 else 0)
    return done / total


def next_pacing_probability(prev_p: float, spent_incl_reserved_mw: int, daily_budget_mw: int,
                            minute_of_day: int, curve: Optional[list[float]] = None, gain: float = 5.0) -> float:
    """목표보다 빠르면 낮추고 느리면 올린다. 한 번 조정 폭 ±0.2, 범위 0.05~1.0."""
    if daily_budget_mw <= 0:
        return PACING_MIN_P
    err = spent_incl_reserved_mw / daily_budget_mw - target_fraction(minute_of_day, curve)
    delta = max(-PACING_STEP, min(PACING_STEP, -gain * err))
    return max(PACING_MIN_P, min(PACING_MAX_P, prev_p + delta))


# ---------------------------------------------------------------- serve 토큰(HMAC-SHA256)
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(claims: ServeClaims, key: bytes) -> str:
    """키는 인프라 비밀 저장소에서 받는다(저장소에 넣지 않는다)."""
    body = _b64(json.dumps(asdict(claims), sort_keys=True, default=str).encode())
    return f"{body}.{_b64(hmac.new(key, body.encode(), hashlib.sha256).digest())}"


def verify_token(token: str, key: bytes, now_ms: int) -> tuple[Optional[ServeClaims], Optional[InvalidReason]]:
    try:
        body, sig = token.split(".")
        if not hmac.compare_digest(sig, _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())):
            return None, InvalidReason.BAD_SIGNATURE
        d = json.loads(_unb64(body))
        claims = ServeClaims(**{**d, "slot": AdSlot(d["slot"])})
    except (ValueError, KeyError, TypeError):
        return None, InvalidReason.BAD_SIGNATURE
    if now_ms > claims.expires_at_ms:
        return claims, InvalidReason.EXPIRED
    return claims, None


# ---------------------------------------------------------------- 유효 판정(pending → valid / invalid)
@dataclass
class Judgement:
    status: JudgeStatus
    reason: Optional[InvalidReason] = None
    suspect: bool = False                   # 빠른 연속 클릭(표시만)
    implied_impression: bool = False        # 클릭이 노출 증거가 되어 노출을 함께 인정함


@dataclass
class ValidityState:
    """serve_id 단위 판정 상태. 광고 서버(온라인)와 silver(배치)가 같은 규칙을 쓴다."""
    serves: dict[str, ServeRecord] = field(default_factory=dict)
    impressed: set[str] = field(default_factory=set)
    clicked: set[str] = field(default_factory=set)
    last_click_ms: dict[str, int] = field(default_factory=dict)   # 세션 -> 마지막 클릭 도착 시각


def _common(st: ValidityState, claims: Optional[ServeClaims], token_reason: Optional[InvalidReason],
            first_received_ms: int, now_ms: int, load_test: bool) -> Optional[Judgement]:
    """token_reason은 verify_token(token, key, first_received_ms)의 결과여야 한다(재판정해도 만료 판단 불변).
    유예는 서버가 처음 받은 시각 기준이다(클라이언트가 보낸 발생 시각은 판정에 쓰지 않는다)."""
    if token_reason:
        return Judgement(JudgeStatus.INVALID, token_reason)
    if load_test:
        return Judgement(JudgeStatus.INVALID, InvalidReason.LOAD_TEST)
    if claims.serve_id not in st.serves:
        if now_ms - first_received_ms < GRACE_MS:
            return Judgement(JudgeStatus.PENDING)
        return Judgement(JudgeStatus.INVALID, InvalidReason.NO_SERVE)
    return None


def judge_impression(st: ValidityState, claims, token_reason, visible_ratio: float, visible_ms: int,
                     first_received_ms: int, now_ms: int, load_test: bool = False) -> Judgement:
    j = _common(st, claims, token_reason, first_received_ms, now_ms, load_test)
    if j:
        return j
    if claims.serve_id in st.impressed:
        return Judgement(JudgeStatus.INVALID, InvalidReason.DUPLICATE)     # 클릭으로 이미 인정된 경우 포함
    if visible_ratio < VIEWABLE_RATIO or visible_ms < VIEWABLE_MS:
        return Judgement(JudgeStatus.INVALID, InvalidReason.NOT_VIEWABLE)
    st.impressed.add(claims.serve_id)
    return Judgement(JudgeStatus.VALID)


def judge_click(st: ValidityState, claims, token_reason, first_received_ms: int, now_ms: int,
                load_test: bool = False) -> Judgement:
    """클릭을 판정하고 처리 순서가 달라도 세션의 마지막 수신 시각은 후퇴시키지 않는다."""
    j = _common(st, claims, token_reason, first_received_ms, now_ms, load_test)
    if j:
        return j
    sess = claims.session_pseudonym
    prev = st.last_click_ms.get(sess)
    st.last_click_ms[sess] = max(first_received_ms, prev if prev is not None else first_received_ms)
    suspect = prev is not None and 0 <= first_received_ms - prev < SUSPECT_CLICK_MS
    if claims.serve_id in st.clicked:
        return Judgement(JudgeStatus.INVALID, InvalidReason.DUPLICATE, suspect)
    st.clicked.add(claims.serve_id)
    implied = claims.serve_id not in st.impressed
    st.impressed.add(claims.serve_id)          # 클릭 = 가시 노출 증거
    return Judgement(JudgeStatus.VALID, None, suspect, implied)


# ---------------------------------------------------------------- 건별 대사(기획서 14.4)
def reconcile(ledger: dict[str, int], delta: dict[str, list[int]]) -> dict[str, list[str]]:
    """ledger: 확정 원장 행 charge_id -> 금액(환입 행 `<charge_id>:refund` 포함).
    delta: charge_id -> 금액 목록(Delta의 과금 대상 유효 건, 중복이면 여러 개).

    원장은 원래 charge_id로 묶은 순액(원과금 + 환입)으로 비교한다(11/7 PM 결정). 순액 0이고 Delta에 없으면
    환입으로 상계된 건이라 일치다(0원 과금은 금지라 발행 누락과 구분된다).
    반환: ledger_only(발행 누락), delta_only, duplicate, amount_mismatch, orphan_refund(원과금 없는 환입 행)
    각 charge_id 목록. 모두 비면 일치.
    """
    out = {"ledger_only": [], "delta_only": [], "duplicate": [], "amount_mismatch": [], "orphan_refund": []}
    net: dict[str, int] = {}
    for cid, amount in ledger.items():
        if cid.endswith(REFUND_SUFFIX):
            base = cid[:-len(REFUND_SUFFIX)]
            if base not in ledger:
                out["orphan_refund"].append(cid)
                continue
        else:
            base = cid
        net[base] = net.get(base, 0) + amount
    out["orphan_refund"].sort()
    for cid in sorted(set(net) | set(delta)):
        amounts = delta.get(cid, [])          # 빈 목록 = Delta에 없음
        if len(amounts) > 1:
            out["duplicate"].append(cid)      # 존재 여부와 독립적으로 센다
        if cid in net and not amounts:
            if net[cid] != 0:                 # 순액 0 = 환입으로 상계
                out["ledger_only"].append(cid)
        elif cid not in net and amounts:
            out["delta_only"].append(cid)
        elif cid in net and any(a != net[cid] for a in amounts):
            out["amount_mismatch"].append(cid)
    return out
