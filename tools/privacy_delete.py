"""적재·재학습에서 제외할 삭제 식별자를 보호된 디렉터리에 등록한다."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid4

from tools.privacy_scope import read_plan


FIELDS = ("players", "ad_sessions", "ad_event_ids", "ad_charge_ids")


def register(directory, player, ad_sessions, ad_event_ids, ad_charge_ids, model_version):
    """완전한 요청 파일을 한 번에 게시한다. 재등록해도 필터는 집합을 사용한다."""
    directory = Path(directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("protected_tombstone_directory_required")
    if os.name != "nt" and directory.stat().st_mode & 0o022:
        raise ValueError("tombstone_directory_must_not_be_group_or_world_writable")
    row = {"players": [str(UUID(player))], "ad_sessions": sorted(set(ad_sessions)),
           "ad_event_ids": sorted(set(ad_event_ids)), "ad_charge_ids": sorted(set(ad_charge_ids)),
           "registered_at": datetime.now(timezone.utc).isoformat(), "model_version": model_version}
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(row, stream)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temporary.unlink()
            raise
    destination = directory / f"request-{uuid4().hex}.json"
    temporary.replace(destination)
    return {field: len(row[field]) for field in FIELDS}


def main(argv=None):
    """동일 파일시스템의 tombstone을 기록하고 식별자 원문 대신 개수를 출력한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    subject = parser.add_mutually_exclusive_group(required=True)
    subject.add_argument("--player")
    subject.add_argument("--scope", type=Path)
    parser.add_argument("--ad-session", action="append", default=[])
    parser.add_argument("--ad-event-id", action="append", default=[])
    parser.add_argument("--ad-charge-id", action="append", default=[])
    parser.add_argument("--model-version", required=True)
    args = parser.parse_args(argv)
    if args.scope:
        if args.ad_session or args.ad_event_id or args.ad_charge_id:
            parser.error("privacy_scope_cannot_be_combined_with_manual_ids")
        scope = read_plan(args.scope)
        if len(scope["players"]) != 1:
            parser.error("privacy_single_player_required")
        counts = register(args.directory, scope["players"][0], scope["ad_sessions"], scope["ad_event_ids"],
                          scope["ad_charge_ids"], args.model_version)
    else:
        counts = register(args.directory, args.player, args.ad_session, args.ad_event_id,
                          args.ad_charge_id, args.model_version)
    print(json.dumps({"tombstone_registered": True, **counts}))


if __name__ == "__main__":
    main()
