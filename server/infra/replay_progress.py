"""판 문서의 발행·미발행 이벤트를 같은 멱등 키로 진척에 반영한다."""
import argparse
import json
import os
import re

from engine.domain.events import EventEnvelope
from engine.domain.progress import PROGRESS_EVENT_TYPES
from engine.domain.serde import from_jsonable
from server.app.progress import apply_progress
from server.infra.content_store import MongoContentSource
from server.infra.mongo_repo import majority
from server.infra.progress_store import MongoProgressRepository


def replay_games(collection, progress, content_source, query):
    """선택된 판을 생성 순서로 읽고 각 판에 고정된 콘텐츠 버전을 사용한다."""
    query = query | {"outbox.envelope.event_type": {"$in": sorted(PROGRESS_EVENT_TYPES)},
                     "state.meta.scenario_id": None}   # 칸이 없는 옛 판도 None과 일치한다
    counts = {"games": 0, "events": 0, "clues_added": 0, "endings_unlocked": 0}
    bundles = {}
    projection = {"state.meta": 1, "outbox.envelope": 1}
    with collection.find(query, projection).sort([("state.meta.created_at", 1), ("_id", 1)]) as cursor:
        for game in cursor:
            meta = game["state"]["meta"]
            player = meta["player_pseudonym"]
            if not player or meta.get("scenario_id"):
                continue   # 시나리오(튜토리얼) 판은 진척에 반영하지 않는다(튜토리얼 설계 8절)
            version = meta["content_version"]
            if version not in bundles:
                bundles[version] = content_source.load(version)
            events = [from_jsonable(EventEnvelope, row["envelope"]) for row in game["outbox"]
                      if row["envelope"]["event_type"] in PROGRESS_EVENT_TYPES]
            if any(event.game_id != game["_id"] for event in events):
                raise ValueError("outbox_game_mismatch")
            applied = apply_progress(progress, player, events, bundles[version].grudge_clues)
            counts["games"] += 1
            counts["events"] += len(events)
            counts["clues_added"] += len(applied.clues)
            counts["endings_unlocked"] += len(applied.endings)
    return counts


def main(argv=None):
    """범위를 명시한 경우에만 쓰고 집계 건수만 출력한다."""
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--game-id", action="append")
    scope.add_argument("--player")
    scope.add_argument("--all", action="store_true")
    parser.add_argument("--prefix", default="")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_]{0,80}", args.prefix):
        parser.error("invalid_collection_prefix")
    if args.game_id:
        query = {"_id": {"$in": args.game_id}}
    elif args.player:
        query = {"state.meta.player_pseudonym": args.player}
    else:
        query = {}
    uri = os.environ.get("PALDO_MONGO_URI")
    if not uri:
        parser.error("PALDO_MONGO_URI not set")
    try:
        with MongoClient(uri, timeoutMS=15000, serverSelectionTimeoutMS=5000) as client:
            database = client.get_default_database("paldo_web")
            counts = replay_games(majority(database[args.prefix + "games"]),
                                  MongoProgressRepository(database[args.prefix + "progress"]),
                                  MongoContentSource(database[args.prefix + "content"]), query)
    except (PyMongoError, KeyError, ValueError) as error:
        parser.exit(1, f"progress_replay_failed: {type(error).__name__}\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
