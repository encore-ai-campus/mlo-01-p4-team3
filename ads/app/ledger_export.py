"""광고 원장 스냅숏을 Spark gold 입력(`--ledger`·`--budgets`)으로 만든다. 저장소 접근은 인자로 받는다."""


def budgets_by_day(ledger_rows, daily_budgets):
    """원장에 나온 (귀속일, 캠페인)마다 캠페인의 일 예산을 둔다. 캠페인이 지워졌으면 넣지 않는다.

    예산 기록 이력이 없으므로 현재 일 예산을 그날 예산으로 쓴다(지난날 예산을 바꿨다면 소진율만 달라진다)."""
    out = {}
    for row in ledger_rows:
        budget = daily_budgets.get(row["campaign_id"])
        if budget is not None:
            out.setdefault(row["attribution_day"], {})[row["campaign_id"]] = budget
    return {day: dict(sorted(campaigns.items())) for day, campaigns in sorted(out.items())}


def export_snapshot(read_ledger, read_budgets):
    """원장 행과 날짜별 예산을 한 번씩 읽어 (ledger, budgets, 요약)을 돌려준다."""
    ledger = read_ledger()
    budgets = budgets_by_day(ledger, read_budgets())
    summary = dict(ledger_rows=len(ledger), confirmed_rows=sum(r["state"] == "confirmed" for r in ledger),
                   confirmed_net_mw=sum(r["amount_mw"] for r in ledger if r["state"] == "confirmed"),
                   budget_days=len(budgets))
    return ledger, budgets, summary
