"""운영 조회용 금액·CTR 계산. 원장 상태와 과금 규칙을 바꾸지 않는다."""


def summarize_day(row):
    """캠페인·귀속일 합계에 가용 밀리원, CTR(%), 원장 일치 여부를 붙인다."""
    net = row["charged_mw"] + row["refunded_mw"]
    committed = row["reserved_mw"] + net
    budget = row["daily_budget_mw"]
    return row | {
        "net_mw": net,
        "available_mw": budget - committed if budget is not None else None,
        "ctr_percent": 100 * row["clicks"] / row["impressions"] if row["impressions"] else None,
        "balanced": committed == row["committed_mw"],
    }
