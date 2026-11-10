"""Five-minute pacing updates reuse the authoritative reservation ledger."""
from ads.domain.rules import next_pacing_probability


def build_pacing(campaigns, book, day, minute, previous, curve=None, *, gain=8.0):
    """Calculate one bounded step from reserved plus net confirmed milliwon.

    previous maps campaign IDs to this day's last probability. A new day
    starts at 1.0; old-day probabilities must not be supplied. The default
    gain tracks the scenario-one target with less intraday error.
    """
    return [dict(campaign_id=c.campaign_id, attribution_day=day,
                 probability=next_pacing_probability(previous.get(c.campaign_id, 1.),
                     book.committed_mw(c.campaign_id, day), c.daily_budget_mw, minute, curve, gain))
            for c in sorted(campaigns, key=lambda c: c.campaign_id)]
