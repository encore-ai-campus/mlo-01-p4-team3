"""봇 시뮬레이션의 진영·길이·무감금·고백·선택 지표를 집계한다."""
from collections import defaultdict
from statistics import mean


def summarize(runs):
    """정책별 비율과 분모를 반환한다. 기회가 0이면 비율은 None으로 둔다."""
    groups = defaultdict(list)
    for run in runs:
        groups[run.policy].append(run)
    rows = []
    for policy, items in sorted(groups.items()):
        vote_days = sum(r.vote_days for r in items)
        secret_holders = sum(r.confession_opportunities for r in items)
        no_locks = sum(r.no_lock_days for r in items)
        confessions = sum(r.confessions for r in items)
        village = sum(r.winner == "village" for r in items)
        rows.append(dict(policy=policy, games=len(items), village_win_rate=village / len(items),
            changgwi_win_rate=1 - village / len(items), days_mean=mean(r.days for r in items),
            no_lock_rate=no_locks / vote_days if vote_days else None, no_lock_days=no_locks,
            vote_days=vote_days, confession_rate=confessions / secret_holders if secret_holders else None,
            confessions=confessions, confession_opportunities=secret_holders,
            meaningful_choices_mean=mean(r.meaningful_choices for r in items),
            illegal_commands=sum(r.illegal_commands for r in items)))
    return rows


def markdown_table(rows):
    """분모가 없는 비율은 —로 표시하는 비교 표를 생성한다."""
    def pct(value):
        return "—" if value is None else f"{100 * value:.2f}%"
    lines = ["| 정책 | 판 수 | 마을 승률 | 창귀 승률 | 판 길이(낮) | 무감금률 | 고백률 | 의미 있는 선택/판 |",
             "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['policy']} | {row['games']} | {pct(row['village_win_rate'])} | "
            f"{pct(row['changgwi_win_rate'])} | {row['days_mean']:.3f} | {pct(row['no_lock_rate'])} | "
            f"{pct(row['confession_rate'])} | {row['meaningful_choices_mean']:.3f} |")
    return "\n".join(lines) + "\n"
