"""계정·광고에서 확인한 보호 계획을 Delta 삭제 입력으로 합친다."""
import argparse
import json
import os
from pathlib import Path
from uuid import UUID


def read_plan(path):
    """다른 계정이 읽을 수 있는 계획과 심볼릭 링크를 거절한다."""
    if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
        raise ValueError("privacy_protected_plan_required")
    return json.loads(path.read_text(encoding="utf-8"))


def combine(game, ads):
    """같은 광고 세션에 해당하는 두 계획을 검증하고 합친다."""
    if set(game["ad_sessions"]) != set(ads["ad_sessions"]):
        raise ValueError("privacy_ad_scope_mismatch")
    keys = ("ad_sessions", "ad_serve_ids", "ad_request_ids", "ad_event_ids", "ad_charge_ids")
    return {"players": [str(UUID(game["player"]))], "game_ids": game["game_ids"]} | {
        key: ads[key] for key in keys
    }


def main(argv=None):
    """합친 계획을 기존 파일을 덮지 않는 보호 파일에 기록한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--ads", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    scope = combine(read_plan(args.game), read_plan(args.ads))
    fd = os.open(args.out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(scope, output)
    print(json.dumps({key: len(values) for key, values in scope.items()}))


if __name__ == "__main__":
    main()
