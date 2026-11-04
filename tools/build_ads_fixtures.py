"""광고 픽스처 생성(build_fixtures.py가 호출). 결과: fixtures/ads/ — 기준: 기획서 14.3~14.7

- campaigns.json: 가상 광고주 캠페인 4개 + 지면 정책
- auction_cases.json: 경매 입력과 기대 결과(2등 가격 T, 청구 단가, 경계 조건)
- validity_cases.json: serve·노출·클릭 도착 순서와 기대 판정·과금(클릭 먼저 도착, 무효 공격 포함)
- budget_cases.json: 예산 예약 → 확정 → 해제, 환입, 건별 대사
"""
from __future__ import annotations

import json
from pathlib import Path

from ads.domain.model import (
    AdSlot, BillingType, Campaign, CampaignStatus, Creative, GameOutcome, SlotPolicy, Targeting, won,
)
from engine.domain import serde

S = AdSlot


def campaigns() -> list[Campaign]:
    return [
        Campaign("cp_gukbap", "adv_jumak", "주막 국밥", [S.LOBBY_BANNER, S.LLM_WAIT], BillingType.CPC,
                 won(300), won(30_000), Targeting(), "2026-11-05", "2026-11-12", CampaignStatus.ACTIVE,
                 [Creative("cr_gukbap_a", "cp_gukbap", "뜨끈한 국밥", "주막에서 쉬어 가시오", "/static/ads/gukbap_a.png", "/ads/landing/gukbap"),
                  Creative("cr_gukbap_b", "cp_gukbap", "국밥 한 그릇", "기다리는 동안 한 술", "/static/ads/gukbap_b.png", "/ads/landing/gukbap")]),
        Campaign("cp_bujeok", "adv_shrine", "서낭당 부적", [S.RESULT_BANNER, S.REVIEW_SIDE], BillingType.CPM,
                 won(4_000), won(20_000), Targeting(outcomes=[GameOutcome.LOSE]), "2026-11-05", "2026-11-12",
                 CampaignStatus.ACTIVE,
                 [Creative("cr_bujeok", "cp_bujeok", "액막이 부적", "다음 판엔 창귀를 막으시오", "/static/ads/bujeok.png", "/ads/landing/bujeok")]),
        Campaign("cp_yakcho", "adv_herb", "약초방", [S.LOBBY_BANNER, S.RESULT_BANNER], BillingType.CPC,
                 won(150), won(10_000), Targeting(returning=True), "2026-11-05", "2026-11-12",
                 CampaignStatus.ACTIVE,
                 [Creative("cr_yakcho", "cp_yakcho", "산삼 한 뿌리", "기운이 탁할 땐 약초방", "/static/ads/yakcho.png", "/ads/landing/yakcho")]),
        Campaign("cp_gat", "adv_gat", "갓 공방", [S.REVIEW_SIDE, S.LLM_WAIT], BillingType.CPM,
                 won(2_500), won(5_000), Targeting(hours=list(range(18, 24))), "2026-11-05", "2026-11-12",
                 CampaignStatus.PAUSED,
                 [Creative("cr_gat", "cp_gat", "새 갓", "무당 어른도 갓 하나", "/static/ads/gat.png", "/ads/landing/gat")]),
    ]


def slot_policies() -> list[SlotPolicy]:
    return [SlotPolicy(S.LOBBY_BANNER, won(500), 0.02), SlotPolicy(S.LLM_WAIT, won(800), 0.03),
            SlotPolicy(S.RESULT_BANNER, won(600), 0.025), SlotPolicy(S.REVIEW_SIDE, won(400), 0.015)]


def _b(cid, crid, billing, bid_won, ctr):
    return {"campaign_id": cid, "creative_id": crid, "billing": billing, "bid_mw": won(bid_won), "rank_ctr": ctr}


def auction_cases() -> list[dict]:
    return [
        {"case_id": "design_example_ctr2_t1000", "note": "기획서 예: 승자 CTR 2%, T=1000원 → 청구 CPC 50원",
         "request_id": "req_a1", "reserve_ecpm_mw": won(500),
         "bidders": [_b("cp_gukbap", "cr_gukbap_a", "cpc", 300, 0.02), _b("cp_bujeok", "cr_bujeok", "cpm", 1000, 0.0)],
         "expect": {"winner": "cp_gukbap", "winner_ecpm_mw": won(6000), "threshold_ecpm_mw": won(1000),
                    "price_mw": won(50), "capped": False}},
        {"case_id": "high_ctr_low_bid_wins", "note": "시나리오 2: 150원×8%=eCPM 12000원이 300원×2%=6000원을 이김. CPC = 6000/(1000×0.08) = 75원",
         "request_id": "req_a2", "reserve_ecpm_mw": won(500),
         "bidders": [_b("cp_gukbap", "cr_gukbap_b", "cpc", 300, 0.02), _b("cp_yakcho", "cr_yakcho", "cpc", 150, 0.08)],
         "expect": {"winner": "cp_yakcho", "winner_ecpm_mw": won(12000), "threshold_ecpm_mw": won(6000),
                    "price_mw": won(75), "capped": False}},
        {"case_id": "cpm_winner_pays_t", "note": "CPM 승자 청구 CPM = T",
         "request_id": "req_a3", "reserve_ecpm_mw": won(600),
         "bidders": [_b("cp_bujeok", "cr_bujeok", "cpm", 4000, 0.0), _b("cp_yakcho", "cr_yakcho", "cpc", 150, 0.01)],
         "expect": {"winner": "cp_bujeok", "winner_ecpm_mw": won(4000), "threshold_ecpm_mw": won(1500),
                    "price_mw": won(1500), "capped": False}},
        {"case_id": "single_bidder_uses_reserve", "note": "후보 1개면 T = 지면 최저 eCPM",
         "request_id": "req_a4", "reserve_ecpm_mw": won(600),
         "bidders": [_b("cp_bujeok", "cr_bujeok", "cpm", 4000, 0.0)],
         "expect": {"winner": "cp_bujeok", "winner_ecpm_mw": won(4000), "threshold_ecpm_mw": won(600),
                    "price_mw": won(600), "capped": False}},
        {"case_id": "below_reserve_no_fill", "note": "모두 최저 eCPM 미만이면 광고 없음",
         "request_id": "req_a5", "reserve_ecpm_mw": won(800),
         "bidders": [_b("cp_yakcho", "cr_yakcho", "cpc", 150, 0.005)],
         "expect": {"winner": None}},
        {"case_id": "zero_ctr_floor_below_reserve", "note": "CTR 0은 하한 0.0001로 계산 → eCPM 30원 < 최저 eCPM → 광고 없음",
         "request_id": "req_a6", "reserve_ecpm_mw": won(500),
         "bidders": [_b("cp_gukbap", "cr_gukbap_a", "cpc", 300, 0.0)],
         "expect": {"winner": None}},
        {"case_id": "cap_when_second_close", "note": "동점이면 T=1등 eCPM → 청구 단가 = 입찰가(상한 경계). 승자는 요청 ID 해시로 정해진다",
         "request_id": "req_a7", "reserve_ecpm_mw": won(500),
         "bidders": [_b("cp_gukbap", "cr_gukbap_a", "cpc", 300, 0.02), _b("cp_bujeok", "cr_bujeok", "cpm", 6000, 0.0)],
         "expect_any_winner": ["cp_gukbap", "cp_bujeok"],
         "expect": {"threshold_ecpm_mw": won(6000), "capped": False}},
    ]


def validity_cases() -> list[dict]:
    """op: serve | impression | click | release. token: serve 이름 | "forged:<이름>".
    at_ms = 발생 시각, recv_ms = 도착 시각(생략하면 at_ms). serve는 at_ms=0에 발급, 만료 30분."""
    return [
        {"case_id": "click_arrives_before_impression", "note": "클릭이 비콘보다 먼저 도착 → 클릭 유효, 노출도 인정. 늦게 온 비콘은 중복",
         "billing": "cpc", "price_mw": won(50),
         "steps": [
             {"op": "serve", "token": "t1", "session": "s1"},
             {"op": "click", "token": "t1", "at_ms": 800, "expect": "valid", "expect_implied": True},
             {"op": "impression", "token": "t1", "at_ms": 1200, "recv_ms": 1500, "visible_ratio": 1.0, "visible_ms": 1200, "expect": "invalid:duplicate"},
         ],
         "expect_confirmed_mw": won(50)},
        {"case_id": "cpc_attacks", "note": "시나리오 5: 토큰 재사용·위조·만료는 무효, 빠른 연속 클릭은 의심 표시만",
         "billing": "cpc", "price_mw": won(100),
         "steps": [
             {"op": "serve", "token": "t1", "session": "s1"},
             {"op": "serve", "token": "t2", "session": "s1"},
             {"op": "impression", "token": "t1", "at_ms": 1500, "visible_ratio": 0.3, "visible_ms": 2000, "expect": "invalid:not_viewable"},
             {"op": "impression", "token": "t1", "at_ms": 2500, "visible_ratio": 0.8, "visible_ms": 1200, "expect": "valid"},
             {"op": "click", "token": "t1", "at_ms": 5000, "expect": "valid"},
             {"op": "click", "token": "t2", "at_ms": 5300, "expect": "valid", "expect_suspect": True, "expect_implied": True},
             {"op": "click", "token": "t1", "at_ms": 9000, "expect": "invalid:duplicate"},
             {"op": "click", "token": "forged:t1", "at_ms": 9500, "expect": "invalid:bad_signature"},
             {"op": "click", "token": "t2", "at_ms": 31 * 60 * 1000, "expect": "invalid:expired"},
         ],
         "expect_confirmed_mw": 2 * won(100)},
        {"case_id": "unknown_serve_pending_then_invalid", "note": "서빙 기록이 안 보이면 최초 수신 후 유예 2분 동안 pending, 재판정 시점(judge_ms)에 지났으면 no_serve",
         "billing": "cpm", "price_mw": won(1500),
         "steps": [
             {"op": "impression", "token": "t_unserved", "at_ms": 100, "recv_ms": 200, "visible_ratio": 1.0, "visible_ms": 2000, "expect": "pending"},
             {"op": "impression", "token": "t_unserved", "at_ms": 100, "recv_ms": 200, "judge_ms": 200_000, "visible_ratio": 1.0, "visible_ms": 2000, "expect": "invalid:no_serve"},
         ],
         "expect_confirmed_mw": 0},
        {"case_id": "cpm_charges_per_valid_impression", "note": "CPM은 유효 노출 1건마다 청구 CPM/1000. 클릭은 과금 없음",
         "billing": "cpm", "price_mw": won(1500),
         "steps": [
             {"op": "serve", "token": "t1", "session": "s2"},
             {"op": "serve", "token": "t2", "session": "s2"},
             {"op": "impression", "token": "t1", "at_ms": 1000, "visible_ratio": 1.0, "visible_ms": 1500, "expect": "valid"},
             {"op": "click", "token": "t2", "at_ms": 3000, "expect": "valid", "expect_implied": True},
             {"op": "impression", "token": "t2", "at_ms": 2000, "recv_ms": 3500, "visible_ratio": 1.0, "visible_ms": 1500, "expect": "invalid:duplicate"},
         ],
         "expect_confirmed_mw": 2 * 1500},
    ]


def budget_cases() -> list[dict]:
    return [
        {"case_id": "reserve_blocks_overspend", "note": "예산 120원, CPC 50원: 예약 2건(100원) 뒤 세 번째 예약 거부 → 1건 확정·1건 해제 시각(serve+30분+유예 2분) 경과로 해제 → 다시 예약 가능",
         "daily_budget_mw": won(120), "billing": "cpc", "price_mw": won(50),
         "steps": [
             {"op": "reserve", "serve": "s1", "at_ms": 0, "expect": True},
             {"op": "reserve", "serve": "s2", "at_ms": 10, "expect": True},
             {"op": "reserve", "serve": "s3", "at_ms": 20, "expect": False},
             {"op": "confirm", "serve": "s1", "event": "e1", "expect": True},
             {"op": "confirm", "serve": "s1", "event": "e1", "expect": False},
             {"op": "release", "now_ms": 31 * 60 * 1000, "expect_released": []},
             {"op": "release", "now_ms": 33 * 60 * 1000, "expect_released": ["s2:click"]},
             {"op": "reserve", "serve": "s4", "at_ms": 33 * 60 * 1000, "expect": True},
             {"op": "refund", "serve": "s1", "event": "e1_rejudged", "expect_amount_mw": -won(50)},
         ],
         "expect_committed_mw": won(50) + won(50) - won(50)},
        {"case_id": "reconcile_itemized", "note": "건별 대사: 합계가 같아도 건별 불일치를 찾는다. Delta에만 있는 중복도 중복으로 센다",
         "ledger": {"a:click": won(50), "b:click": won(75), "c:click": won(30), "e:click": won(40)},
         "delta": {"a:click": [won(50)], "b:click": [won(75), won(75)], "d:click": [won(30), won(30)],
                   "c:click": [], "e:click": [won(45)]},
         "expect": {"ledger_only": ["c:click"], "delta_only": ["d:click"], "duplicate": ["b:click", "d:click"],
                    "amount_mismatch": ["e:click"]}},
    ]


def main(fx: Path) -> None:
    d = fx / "ads"
    d.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dump("campaigns.json", {"campaigns": serde.to_jsonable(campaigns()), "slot_policies": serde.to_jsonable(slot_policies())})
    dump("auction_cases.json", auction_cases())
    dump("validity_cases.json", validity_cases())
    dump("budget_cases.json", budget_cases())
