"""광고 도메인 데이터 모델(표준 라이브러리만). 상세: docs/contracts/ads.md, 기획서 14장.

계층: 도메인. ads/app, ads/infra, ads/web과 게임 쪽 server/app(요청·응답 DTO만)이 이것을 쓴다.
게임 엔진(engine/)과 서로 import하지 않는다.
금액은 모두 정수 밀리원(_mw, 1원 = 1000)이다. 누적은 밀리원으로 하고 청구서는 원 단위로 내림한다.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

AD_CONTRACT_VERSION = "ads-0.5"   # 0.5(11/6): 지면 llm_wait 삭제(지면 3곳). 0.4(11/5): serve 이벤트 context, 공통 bid_version, ad.rejudge 형식
MW_PER_WON = 1000


def won(x: float) -> int:
    """원 → 밀리원."""
    return round(x * MW_PER_WON)


class AdSlot(str, enum.Enum):
    LOBBY_BANNER = "lobby_banner"
    RESULT_BANNER = "result_banner"
    REVIEW_SIDE = "review_side"


# 지면 규격(P-19, 11/5 PM 잠정 확정. 11/6 PM 결정으로 llm_wait 삭제): 지면 → (가로, 세로) 픽셀. 소재 이미지는 지면이 아니라 **규격마다 1장**이다.
SLOT_SIZES: dict[AdSlot, tuple[int, int]] = {
    AdSlot.LOBBY_BANNER: (728, 90),
    AdSlot.RESULT_BANNER: (728, 90),
    AdSlot.REVIEW_SIDE: (300, 250),
}


def slot_image_path(image_path: str, slot: AdSlot) -> str:
    """소재 기본 경로(`Creative.image_path`) → 그 지면 규격의 실제 이미지 경로.

    예: `/static/ads/gat.png`, `lobby_banner` → `/static/ads/gat__728x90.png`.
    광고 서버는 `AdCreativeView.image_path`에 이 값을 넣고, 에셋(E)은 이 이름으로 파일을 만든다.
    """
    w, h = SLOT_SIZES[AdSlot(slot)]
    head, dot, ext = image_path.rpartition(".")
    if not dot or "/" in ext:
        return f"{image_path}__{w}x{h}"
    return f"{head}__{w}x{h}.{ext}"


class BillingType(str, enum.Enum):
    CPC = "cpc"
    CPM = "cpm"


class CampaignStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class GameOutcome(str, enum.Enum):
    WIN = "win"
    LOSE = "lose"


class JudgeStatus(str, enum.Enum):
    PENDING = "pending"             # 짝(서빙)이 아직 안 보임. 유예 시간 안
    VALID = "valid"
    INVALID = "invalid"


class InvalidReason(str, enum.Enum):
    BAD_SIGNATURE = "bad_signature"   # 토큰 위조
    EXPIRED = "expired"               # 토큰 만료(30분)
    NO_SERVE = "no_serve"             # 유예 시간이 지나도 서빙 기록 없음
    NOT_VIEWABLE = "not_viewable"     # 노출: 50% 이상 1초 이상 기준 미달
    DUPLICATE = "duplicate"           # 같은 서빙의 두 번째 노출 / 두 번째 클릭(토큰 재사용)
    LOAD_TEST = "load_test"


# ---------------------------------------------------------------- 캠페인·소재
@dataclass
class Targeting:
    """비어 있는 목록 = 제한 없음."""
    preset_ids: list[str] = field(default_factory=list)
    outcomes: list[GameOutcome] = field(default_factory=list)
    returning: Optional[bool] = None          # True=재방문만, False=신규만, None=무관
    hours: list[int] = field(default_factory=list)   # 0~23


@dataclass
class Creative:
    creative_id: str
    campaign_id: str
    title: str
    body: str
    image_path: str                 # 사이트 안 정적 기본 경로. 지면별 실제 파일은 slot_image_path(image_path, slot)
    landing_path: str               # 사이트 안 가상 랜딩 페이지(외부 URL 금지)


@dataclass
class Campaign:
    campaign_id: str                # 합성 트래픽 전용 복제본은 "syn:" 접두어(실제 상태와 격리)
    advertiser_id: str
    name: str
    slots: list[AdSlot]
    billing: BillingType
    max_bid_mw: int                 # CPC: 클릭당 상한, CPM: 1000회 노출당 상한
    daily_budget_mw: int
    targeting: Targeting
    start_date: str                 # YYYY-MM-DD
    end_date: str
    status: CampaignStatus
    creatives: list[Creative]
    target_cpc_mw: Optional[int] = None   # 자동 입찰(CPC만, 선택)


@dataclass
class SlotPolicy:
    slot: AdSlot
    reserve_ecpm_mw: int            # 지면 최저 eCPM
    prior_ctr: float                # μ: 지면 평균 CTR
    prior_strength: float = 200.0   # κ: 사전분포 가상 노출 수
    frequency_cap: int = 3          # 같은 사용자·같은 소재, 세션당 최대 serve


# ---------------------------------------------------------------- 요청·응답(게임 서버 → 광고 서버 내부 HTTP)
@dataclass
class AdContext:
    preset_id: Optional[str] = None
    outcome: Optional[GameOutcome] = None
    returning: bool = False
    hour: int = 0


@dataclass
class AdRequest:
    request_id: str                 # 게임 서버가 만든 UUID(멱등 키, 동점 처리 씨앗)
    slot: AdSlot
    session_pseudonym: str          # 세션 가명 ID(privacy.md)
    context: AdContext
    source: str = "human"           # human | synthetic
    load_test: bool = False


@dataclass
class AdCreativeView:
    """화면에 그릴 것만. 입찰가·점수는 넣지 않는다."""
    creative_id: str
    title: str
    body: str
    image_path: str                 # 요청 지면 규격의 실제 경로(slot_image_path 결과). 크기는 SLOT_SIZES[slot]
    label: str = "광고"


@dataclass
class AdResponse:
    request_id: str
    filled: bool
    creative: Optional[AdCreativeView] = None
    serve_token: Optional[str] = None         # 서명된 serve 토큰(비콘·클릭에 붙인다)
    beacon_path: Optional[str] = None         # POST, 가시 노출 비콘
    click_path: Optional[str] = None          # GET, 클릭 리다이렉트(?t=토큰)
    policy_version: str = ""
    ctr_version: str = ""


# ---------------------------------------------------------------- 경매
@dataclass
class Bidder:
    """경매 입력 1행(필터를 통과한 소재). rank_ctr = 순위에 쓴 CTR(톰슨 샘플). CPM이면 순위에 쓰지 않는다."""
    campaign_id: str
    creative_id: str
    billing: BillingType
    bid_mw: int
    rank_ctr: float


@dataclass
class AuctionResult:
    winner: Optional[Bidder]
    winner_ecpm_mw: int = 0
    threshold_ecpm_mw: int = 0      # T = max(2등 eCPM, 지면 최저 eCPM)
    price_mw: int = 0               # 청구 단가: CPC=클릭당, CPM=1000회 노출당
    capped: bool = False            # 최대 입찰가 상한에 걸렸는가
    scores: dict[str, int] = field(default_factory=dict)   # campaign_id -> eCPM


# ---------------------------------------------------------------- serve 토큰
@dataclass
class ServeClaims:
    """서명된 serve 토큰의 내용(기획서 14.4). 서버가 발급했다는 것만 증명한다."""
    serve_id: str
    session_pseudonym: str
    slot: AdSlot
    issued_at_ms: int
    expires_at_ms: int


# ---------------------------------------------------------------- 예산 원장(예약 → 확정 → 해제)
class ChargeState(str, enum.Enum):
    RESERVED = "reserved"
    CONFIRMED = "confirmed"
    RELEASED = "released"


@dataclass
class ServeRecord:
    """경매 당시 가격·과금 방식을 serve 단위로 보관한다(이벤트와 원장이 참조)."""
    serve_id: str
    request_id: str
    campaign_id: str
    creative_id: str
    slot: AdSlot
    billing: BillingType
    price_mw: int
    rank_ctr: float
    session_pseudonym: str
    served_at_ms: int
    attribution_day: str            # 비용 귀속일 = serve 시각 기준
    source: str = "human"
    bid_version: str = ""           # 경매 시점의 입찰가 버전. 후속 노출·클릭 이벤트는 이 값을 복사한다(ads-0.4)


@dataclass
class LedgerRow:
    """원장 행. charge_id = serve_id + ":" + ("click"|"impression") 에 유일 제약. 정정은 음수 환입 행으로 추가."""
    charge_id: str
    campaign_id: str
    amount_mw: int
    state: ChargeState
    attribution_day: str
    source_event_id: Optional[str] = None   # 확정 근거 이벤트
    kind: str = "charge"                    # charge | refund
    billing: Optional[BillingType] = None
    price_mw: int = 0                       # 경매 당시 청구 단가
    release_after_ms: int = 0               # 이 시각까지 확정되지 않으면 예약 해제
