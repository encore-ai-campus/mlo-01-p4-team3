"""보존 기한 작업에 넘길 참가자 범위를 보호된 파일로 확정한다."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import UUID
from tools.ut_metrics import DEFAULT_CUTOFF, DEFAULT_OPEN, parse_time, scope_games


RETENTION_AT = datetime(2026, 12, 12, tzinfo=timezone(timedelta(hours=9)))


def retention_subjects(accounts, games):
    """정식 테스트 중 가입하거나 시작한 비운영자 계정을 선택한다."""
    if accounts["kind"] != "accounts" or not isinstance(accounts["accounts"], list):
        raise ValueError("accounts_snapshot_required")
    if games["kind"] != "games" or not isinstance(games["games"], list):
        raise ValueError("games_snapshot_required")
    start, end = parse_time(DEFAULT_OPEN), parse_time(DEFAULT_CUTOFF)
    played = {game["player_pseudonym"] for game in scope_games(games["games"], since=start, until=end)}
    selected = set()
    for row in accounts["accounts"]:
        player, joined = row["player_pseudonym"], parse_time(row.get("date_joined"))
        if row["is_staff"] is False and (player in played or (joined and start <= joined <= end)):
            selected.add(str(UUID(player)))
    return sorted(selected)


def main(argv=None):
    """보존 기한 이후에만 보호 파일로 대상 목록을 만든다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if datetime.now(timezone.utc) < RETENTION_AT:
        parser.error("retention_deadline_not_reached")
    subjects = retention_subjects(json.loads(args.accounts.read_text(encoding="utf-8")),
                                  json.loads(args.games.read_text(encoding="utf-8")))
    fd = os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump({"players": subjects}, output)
    print(json.dumps({"retention_subjects": len(subjects)}))


if __name__ == "__main__":
    main()
