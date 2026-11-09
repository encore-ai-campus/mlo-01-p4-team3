"""D-09-02 offline event-export batch and majority/CAS Mongo bandit state.

The caller supplies a full frozen paldo.game/player export and consent allowlist.
No game web request calls this batch. Publication uses one document so posterior,
ratios, version and applied IDs cannot diverge on retries or concurrent writers.
"""
import argparse
import json
import os
from pathlib import Path

from ml.bandit import games_from_events, initial_state, propose_update, forget_games
from server.infra.mongo_repo import majority
from tools.jsonlog import log


class MongoBanditStore:
    """Append immutable snapshots via revision CAS; all game IDs are dedup keys.

    shortcut: history shares Mongo's 16 MiB document limit, split into a
    transactional version collection before extending beyond the classroom pilot.
    """
    def __init__(self, collection, arms=None):
        from pymongo.errors import DuplicateKeyError
        self.collection = majority(collection)
        if self.collection.find_one({'_id': 'bandit'}) is None:
            try:
                self.collection.insert_one(initial_state(arms))
            except DuplicateKeyError:
                pass
        if arms is not None and self.load()['arms'] != initial_state(arms)['arms']:
            raise ValueError('arm configuration is immutable')

    def load(self):
        """Return aggregate state and all pinned historical snapshots."""
        return self.collection.find_one({'_id': 'bandit'})

    def update(self, games, *, seed=7):
        """Publish at most once; an unknown write result propagates for safe retry."""
        games = list(games)
        for _ in range(8):
            state = self.load()
            updated = propose_update(state, games, seed=seed)
            if updated is None:
                return None
            result = self.collection.replace_one({'_id': 'bandit', 'revision': state['revision']}, updated)
            if result.matched_count:
                return updated['versions'][-1]
        raise RuntimeError('bandit publication contention; retry full batch')

    def forget_games(self, game_ids):
        """C deletion hook, atomic against concurrent batch publishers and idempotent."""
        game_ids = set(game_ids)
        for _ in range(8):
            state = self.load()
            cleaned = forget_games(state, game_ids)
            if cleaned['revision'] == state['revision']:
                return
            if self.collection.replace_one({'_id': 'bandit', 'revision': state['revision']}, cleaned).matched_count:
                return
        raise RuntimeError('bandit deletion contention; retry deletion')


def main(argv=None):
    """Publish from private JSONL exports; URI and consent IDs never reach logs."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--events', required=True, type=Path)
    ap.add_argument('--consented-users', required=True, type=Path, help='private JSON array of pseudonyms')
    ap.add_argument('--arms', type=Path, help='initial arm ID -> StrategyMix JSON, immutable afterwards')
    ap.add_argument('--collection', default='bandit_state')
    ap.add_argument('--seed', type=int, default=7)
    args = ap.parse_args(argv)
    client = None
    try:
        from pymongo import MongoClient
        consent = json.loads(args.consented_users.read_text(encoding='utf-8'))
        if not isinstance(consent, list) or any(not isinstance(u, str) or not u for u in consent):
            raise ValueError('invalid consent allowlist')
        events = [json.loads(line) for line in args.events.read_text(encoding='utf-8').splitlines() if line.strip()]
        games = games_from_events(events, set(consent))
        arms = json.loads(args.arms.read_text(encoding='utf-8')) if args.arms else None
        client = MongoClient(os.environ['PALDO_MONGO_URI'], timeoutMS=15000, serverSelectionTimeoutMS=5000)
        store = MongoBanditStore(client.get_default_database()[args.collection], arms)
        version = store.update(games, seed=args.seed)
        state = store.load()
        published = state['versions'][-1]['version'] if state['versions'] else 'bandit-0'
        log('bandit', 'info', 'batch_finished', updated=version is not None,
            version=version['version'] if version else published,
            response_games=len(version['reward_game_ids']) if version else 0)
        return 0
    except Exception as exc:
        # Driver/parser exceptions can include URI or private records; log only type.
        log('bandit', 'error', 'batch_failed', error_type=type(exc).__name__)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
