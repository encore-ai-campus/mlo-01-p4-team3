"""Recompute Beta posteriors from a complete, finalized observation snapshot."""
from collections import defaultdict
from datetime import date

from ads.domain.model import AdSlot
from ads.domain.rules import ctr_posterior


def build_ctr(snapshot, policies):
    """Fold final day/hour cells; reject duplicates, invalid counts and mixed namespaces.

    Input is an explicit adapter boundary, not a claim that C-08-02 gold has
    this schema. Never pass previous alpha/beta or evaluation truth here.
    """
    if (not isinstance(snapshot, dict) or snapshot.get("finalized") is not True or not isinstance(snapshot.get("source_version"), str)
            or not snapshot["source_version"] or type(snapshot.get("as_of_ms")) is not int
            or snapshot["as_of_ms"] < 0 or not isinstance(snapshot.get("rows"), list)):
        raise ValueError("complete finalized snapshot metadata required")
    totals, seen = defaultdict(lambda: [0, 0]), set()
    for row in snapshot["rows"]:
        required = {"campaign_id", "creative_id", "slot", "source", "attribution_day", "hour",
                    "valid_impressions", "clicked_impressions"}
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("invalid aggregate cell")
        campaign, creative, source = row["campaign_id"], row["creative_id"], row["source"]
        if (source not in ("human", "synthetic") or not isinstance(campaign, str) or not isinstance(creative, str)
                or not campaign or not creative
                or any(v.startswith("syn:") != (source == "synthetic") for v in (campaign, creative))):
            raise ValueError("campaign/creative/source namespace mismatch")
        slot = AdSlot(row["slot"])
        if (not isinstance(row["attribution_day"], str)
                or date.fromisoformat(row["attribution_day"]).isoformat() != row["attribution_day"]):
            raise ValueError("invalid attribution day")
        hour, n, c = row["hour"], row["valid_impressions"], row["clicked_impressions"]
        if (type(hour) is not int or not 0 <= hour < 24 or type(n) is not int or type(c) is not int
                or not 0 <= c <= n):
            raise ValueError("invalid hour or final counts")
        cell = (campaign, creative, slot, source, row["attribution_day"], hour)
        if cell in seen:
            raise ValueError("duplicate aggregate cell")
        seen.add(cell)
        totals[creative, slot, source][0] += n
        totals[creative, slot, source][1] += c
    result = []
    for (creative, slot, source), (n, c) in sorted(totals.items()):
        estimate = ctr_posterior(policies[slot], n, c)
        result.append(dict(creative_id=creative, slot=slot.value,
                           source=source, alpha=estimate.alpha, beta=estimate.beta,
                           valid_impressions=n, clicked_impressions=c))
    return result
