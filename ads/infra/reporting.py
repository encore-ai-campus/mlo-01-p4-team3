"""MySQL 원장과 온라인 판정을 읽어 캠페인·서울 귀속일별로 합친다."""
from datetime import datetime, time, timedelta, timezone

from django.db.models import Q, Sum

from ads.web.models import AdDailyBudget, AdStoredRequest, Campaign

SEOUL = timezone(timedelta(hours=9))


def read_report_rows(start_day, end_day, source, campaign_id=""):
    """금액은 한 SELECT에서 합쳐 committed와 대조하고, 유효 serve 플래그를 센다."""
    if source not in {"human", "synthetic"}:
        raise ValueError("unknown ad source")
    campaigns = Campaign.objects.all()
    budgets = AdDailyBudget.objects.filter(attribution_day__range=(start_day, end_day))
    if source == "synthetic":
        campaigns = campaigns.filter(pk__startswith="syn:")
        budgets = budgets.filter(campaign_id__startswith="syn:")
    else:
        campaigns = campaigns.exclude(pk__startswith="syn:")
        budgets = budgets.exclude(campaign_id__startswith="syn:")
    if campaign_id:
        campaigns = campaigns.filter(pk=campaign_id)
        budgets = budgets.filter(campaign_id=campaign_id)
    campaign_rows = {row["campaign_id"]: row for row in
                     campaigns.values("campaign_id", "name", "advertiser_id", "daily_budget_mw")}
    rows = {}
    ledger_source = Q(adledgerentry__source=source)
    for budget in budgets.values("campaign_id", "attribution_day", "committed_mw").annotate(
        reserved_mw=Sum("adledgerentry__amount_mw", default=0,
                        filter=ledger_source & Q(adledgerentry__state="reserved")),
        charged_mw=Sum("adledgerentry__amount_mw", default=0,
                       filter=ledger_source & Q(adledgerentry__state="confirmed", adledgerentry__kind="charge")),
        refunded_mw=Sum("adledgerentry__amount_mw", default=0,
                        filter=ledger_source & Q(adledgerentry__state="confirmed", adledgerentry__kind="refund")),
    ):
        rows[(budget["attribution_day"], budget["campaign_id"])] = budget

    start_ms = int(datetime.combine(start_day, time.min, SEOUL).timestamp() * 1000)
    end_ms = int(datetime.combine(end_day + timedelta(days=1), time.min, SEOUL).timestamp() * 1000)
    requests = AdStoredRequest.objects.filter(
        serve_data__record__source=source,
        serve_data__record__served_at_ms__gte=start_ms,
        serve_data__record__served_at_ms__lt=end_ms,
    ).filter(Q(impressed=True) | Q(clicked=True)).filter(
        Q(serve_data__load_test=False) | Q(serve_data__load_test__isnull=True))
    if campaign_id:
        requests = requests.filter(serve_data__record__campaign_id=campaign_id)
    for saved in requests.values("serve_data", "impressed", "clicked").iterator():
        record = saved["serve_data"]["record"]
        cid = record["campaign_id"]
        if cid.startswith("syn:") != (source == "synthetic"):
            continue
        day = datetime.fromtimestamp(record["served_at_ms"] / 1000, SEOUL).date()
        row = rows.setdefault((day, cid), {"campaign_id": cid, "attribution_day": day})
        row["impressions"] = row.get("impressions", 0) + int(saved["impressed"])
        row["clicks"] = row.get("clicks", 0) + int(saved["clicked"])
    for cid in campaign_rows:
        rows.setdefault((end_day, cid), {"campaign_id": cid, "attribution_day": end_day})
    result = []
    for key in sorted(rows, reverse=True):
        row = rows[key]
        metadata = campaign_rows.get(row["campaign_id"], {
            "name": "삭제된 캠페인", "advertiser_id": "—", "daily_budget_mw": None})
        result.append(dict(reserved_mw=0, charged_mw=0, refunded_mw=0, committed_mw=0,
                           impressions=0, clicks=0, source=source) | metadata | row)
    return result
