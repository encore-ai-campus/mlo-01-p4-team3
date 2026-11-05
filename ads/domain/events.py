"""광고 이벤트 계약(Kafka 토픽 ad.serve, ad.impression, ad.click, ad.rejudge). 상세: docs/contracts/events.md 5절.

광고 서버는 원장 변경과 이벤트(아웃박스 행)를 같은 MySQL 트랜잭션에 쓰고, 발행기가 Kafka로 보낸다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import AdContext, AdSlot, BillingType, InvalidReason, JudgeStatus

TOPIC_SERVE = "ad.serve"
TOPIC_IMPRESSION = "ad.impression"
TOPIC_CLICK = "ad.click"
TOPIC_REJUDGE = "ad.rejudge"   # Spark silver 재판정(C-08-02) → 환입 소비자(D-06-02). 키 charge_id


@dataclass
class AdEventBase:
    event_id: str                   # 전역 유일. 소비자·Delta MERGE 키
    request_id: str
    serve_id: Optional[str]         # 토큰을 못 읽은 무효 이벤트면 None
    occurred_at_ms: int             # 발생 시각(브라우저·서버)
    received_at_ms: int             # 광고 서버 도착 시각
    slot: Optional[AdSlot]
    campaign_id: Optional[str]
    creative_id: Optional[str]
    session_pseudonym: Optional[str]
    source: str                     # human | synthetic
    policy_version: str             # 경매·페이싱 규칙 버전
    ctr_version: str                # 순위에 쓴 CTR 추정 버전
    contract_version: str
    # 경매 당시 가격·과금 방식(serve에서 복사. 대사·재판정에 쓴다)
    billing: Optional[BillingType] = None
    price_mw: int = 0
    load_test: bool = False
    bid_version: str = ""           # 경매에 쓴 입찰가 버전(자동 입찰 갱신, D-10-02). 자동 입찰이 없으면 ""


@dataclass
class ServeCandidate:
    campaign_id: str
    creative_id: str
    billing: BillingType
    bid_mw: int
    rank_ctr: float                 # 순위에 쓴 CTR(톰슨 샘플)
    ctr_mean: float
    ecpm_mw: int
    filtered: Optional[str] = None  # targeting|budget|frequency|pacing|reserve. None이면 경매 참여


@dataclass
class AdServeEvent(AdEventBase):
    """경매가 끝나고 응답했을 때. 노출이 아니다. 광고 없음(filled=False)도 기록한다."""
    filled: bool = False
    candidates: list[ServeCandidate] = field(default_factory=list)
    threshold_ecpm_mw: int = 0      # T
    rank_ctr: float = 0.0           # 승자의 순위 CTR(가격 환산에 씀)
    capped: bool = False
    reserved_charge_id: Optional[str] = None
    reserved_mw: int = 0
    latency_ms: int = 0
    # 요청 맥락(AdRequest.context 복사). 노출·클릭에는 싣지 않고 silver가 serve_id로 조인한다(시나리오 6·광고 CTR 학습)
    context: Optional[AdContext] = None


@dataclass
class AdJudgedEvent(AdEventBase):
    status: JudgeStatus = JudgeStatus.PENDING
    invalid_reason: Optional[InvalidReason] = None
    judge_version: str = ""
    charge_id: Optional[str] = None         # 이 이벤트가 확정한 과금. 확정이 없으면 None
    charge_mw: int = 0
    source_event_id: Optional[str] = None   # 재판정·환입이면 원본 이벤트 ID


@dataclass
class AdImpressionEvent(AdJudgedEvent):
    visible_ratio: float = 0.0
    visible_ms: int = 0
    implied_by_click: bool = False          # 클릭이 노출 증거가 되어 서버가 만든 노출


@dataclass
class AdClickEvent(AdJudgedEvent):
    suspect: bool = False                   # 빠른 연속 클릭(무효 근거 아님)
    redirect_path: str = ""


@dataclass
class AdRejudgeEvent:
    """silver 재판정이 확정 과금 건을 무효로 바꿨을 때(토픽 ad.rejudge, 키 charge_id).

    광고 서버 아웃박스가 아니라 Spark 재판정 작업(C-08-02)이 발행한다. event_id는 `rj:<charge_id>:<judge_version>`.

    소비자(환입 소비자)는 원래 원장 행을 두고 음수 환입 행(`<charge_id>:refund`)을 추가한다(ads.md 5절).
    """
    event_id: str                   # 전역 유일. 소비자 멱등 키
    source_event_id: str            # 원래 확정한 노출·클릭 이벤트 ID
    charge_id: str
    invalid_reason: InvalidReason
    judge_version: str
    occurred_at_ms: int             # 재판정 시각
    source: str                     # human | synthetic
    contract_version: str
