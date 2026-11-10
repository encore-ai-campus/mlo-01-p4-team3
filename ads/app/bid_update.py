"""Compute the next CPC bid from finalized campaign gold and current bids."""
from collections import defaultdict
from datetime import date

from ads.app.ctr_update import finalized_rows
from ads.domain.model import AdSlot, BillingType


def effective_bid(campaign, row):
    """Use a saved bid only while its opt-in settings still match the campaign."""
    if (campaign.billing is BillingType.CPC and campaign.target_cpc_mw is not None and row
            and (row["target_cpc_mw"], row["min_bid_mw"], row["max_bid_mw"])
            == (campaign.target_cpc_mw, campaign.min_bid_mw, campaign.max_bid_mw)
            and campaign.min_bid_mw <= row["bid_mw"] <= campaign.max_bid_mw):
        return row["bid_mw"]
    return None


def build_bids(snapshot, campaigns, previous):
    """Update from each campaign's latest finalized day once per distinct observation."""
    totals, seen, latest_day = defaultdict(lambda: [0, 0]), set(), {}
    for row in finalized_rows(snapshot):
        cid, source, day = row["campaign_id"], row["source"], row["attribution_day"]
        hour, load = row["hour"], row["load_test"]
        clicks, cost = row["clicks"], row["cost_mw"]
        if (not isinstance(cid, str) or not cid or source not in ("human", "synthetic")
                or cid.startswith("syn:") != (source == "synthetic")
                or row["billing"] != "cpc"
                or date.fromisoformat(day).isoformat() != day
                or type(hour) is not int or not 0 <= hour < 24 or type(load) is not bool
                or type(clicks) is not int or clicks < 0 or type(cost) is not int or cost < 0):
            raise ValueError("invalid campaign gold cell")
        key = cid, day, AdSlot(row["slot"]), hour, load
        if key in seen:
            raise ValueError("duplicate campaign gold cell")
        seen.add(key)
        if not load:
            latest_day[cid] = max(day, latest_day.get(cid, day))
            totals[cid, day][0] += clicks
            totals[cid, day][1] += cost

    bids = []
    for campaign in sorted(campaigns, key=lambda c: c.campaign_id):
        target = campaign.target_cpc_mw
        if campaign.billing is not BillingType.CPC or target is None:
            continue
        cid, low, high = campaign.campaign_id, campaign.min_bid_mw, campaign.max_bid_mw
        if not 1 <= low <= high or target <= 0:
            raise ValueError("invalid CPC bid settings")
        day = latest_day.get(cid)
        clicks, cost = totals[cid, day]
        old = previous.get(cid)
        current = effective_bid(campaign, old)
        if current is not None and (day is None or (old["attribution_day"] or "") > day
                                    or (day, clicks, cost) == (old["attribution_day"], old["clicks"], old["cost_mw"])):
            bids.append(old)
            continue
        if not clicks and current is None:
            continue
        current = high if current is None else current
        # Round half up in integer milliwon before clamping.
        proposed = (2 * current * target * clicks + cost) // (2 * cost) if clicks and cost else current
        bids.append(dict(campaign_id=cid, bid_mw=max(low, min(high, proposed)), target_cpc_mw=target,
                         min_bid_mw=low, max_bid_mw=high, attribution_day=day, clicks=clicks, cost_mw=cost,
                         realized_cpc_mw=cost / clicks if clicks else None))
    return bids
