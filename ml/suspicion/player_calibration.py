"""Fit the player stand-in's vote rule from the frozen notebook and prior ballots."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from time import perf_counter

from engine.domain.model import PLAYER_ID, CommandKind
from engine.sim.bots import LogicBot, legal_commands
from pipeline.spark.privacy_filter import load_tombstones
from tools.gitrepo import GitRepo
from tools.ut_freeze import DEFAULT_CUTOFF, DEFAULT_OPEN, snapshot, split_users

FEATURE_VERSION = "notebook-prior-ballot-v1"


def vote_target(notebook, prior, weight):
    """Rank legal NPCs by a convex blend, breaking ties by ID."""
    return max(sorted(notebook), key=lambda npc: weight * notebook[npc] + (1 - weight) * prior.get(npc, 0.))


def fit_vote_weight(samples):
    """Select the smallest grid weight with the most exact votes on fit users."""
    if not samples:
        raise ValueError("no calibration votes")
    return max((i / 10 for i in range(11)), key=lambda w: sum(
        vote_target(notes, prior, w) == target for target, notes, prior in samples))


def _prior(ballots):
    counts = Counter(ballots)
    return {npc: count / sum(counts.values()) for npc, count in counts.items()}


def vote_samples(games, consent, deleted):
    """Use only consented human votes and notebook values recorded before each vote."""
    games = snapshot(games, datetime.fromisoformat(DEFAULT_CUTOFF), datetime.fromisoformat(DEFAULT_OPEN))
    samples, seen = defaultdict(list), set()
    for game in games:
        user = game["player_pseudonym"]
        events = game["events"]
        if (not user or user not in consent or user in deleted or game["source"] != "human"
                or any(event.get("load_test") for event in events)
                or not any(event["event_type"] == "game_ended" and event.get("occurred_at")
                           and event["payload"].get("completed", True) is True for event in events)):
            continue
        notes, previous, runoff = None, {}, []
        for event in sorted(events, key=lambda e: e["seq"]):
            if event["event_id"] in seen or not event["occurred_at"]:
                continue
            seen.add(event["event_id"])
            payload = event["payload"]
            if event["event_type"] == "notebook_updated":
                entries = payload["entries"]
                if any(type(value) is not int or not 0 <= value <= 100 for value in entries.values()):
                    raise ValueError("invalid notebook entries")
                notes = {npc: value / 100 for npc, value in entries.items()}
            elif event["event_type"] == "votes_revealed":
                ballots = payload["votes"]
                target = ballots.get(PLAYER_ID)
                allowed = set(runoff if payload["round"] == "runoff" else ballots) - {PLAYER_ID}
                if notes and target in allowed:
                    samples[user].append((target, {npc: notes.get(npc, 0.) for npc in allowed}, previous))
                previous = _prior(ballots.values())
                runoff = payload["runoff_candidates"]
    return samples


class CalibratedPlayerBot(LogicBot):
    """Keep the existing player bot behavior and apply calibrated weights at voting."""

    def __init__(self, weight):
        """Keep only aggregate parameters, with no participant history."""
        if not 0 <= weight <= 1:
            raise ValueError("invalid vote weight")
        self.weight = weight
        self.version = f"player-{FEATURE_VERSION}-{weight:.1f}"

    def choose(self, view, rng):
        """Use a public-score proxy for the notebook in simulation."""
        votes = {c.target: c for c in legal_commands(view) if c.kind is CommandKind.VOTE}
        if not votes:
            return super().choose(view, rng)
        scores = self._scores(view)
        high = max(1, max(scores.values()))
        # shortcut: simulated notebook uses the existing logic scores, replace when A provides its notebook policy.
        notes = {npc: max(0, scores[npc]) / high for npc in votes}
        last = view.public.votes[-1] if view.public.votes else None
        prior = _prior(v.target for v in view.public.votes if (v.day, v.round) == (last.day, last.round)) if last else {}
        return votes[vote_target(notes, prior, self.weight)]


def main(argv=None):
    """Publish aggregate fit settings and held-out vote recall from a single snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("games", "consented-users", "tombstones", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--data-version", required=True)
    parser.add_argument("--seed", type=int, default=20261110)
    args = parser.parse_args(argv)
    started = perf_counter()
    raw = args.games.read_bytes()
    consent = json.loads(args.consented_users.read_text(encoding="utf-8"))
    if not isinstance(consent, list) or any(not isinstance(user, str) or not user for user in consent):
        parser.error("invalid consent allowlist")
    if not args.data_version.startswith(("delta:", "fixture:")):
        parser.error("data version must identify the Delta snapshot or the local fixture")
    deleted = load_tombstones(args.tombstones)["players"]
    samples = vote_samples(json.loads(raw)["games"], set(consent), deleted)
    test, fit, _ = split_users(samples, args.seed)
    if not test:
        raise ValueError("need votes with notebooks from at least two eligible users")
    training = [sample for user in fit for sample in samples[user]]
    heldout = [sample for user in test for sample in samples[user]]
    weight = fit_vote_weight(training)
    if load_tombstones(args.tombstones)["players"] != deleted:
        raise ValueError("tombstones changed during calibration")
    report = dict(data_version=args.data_version, feature_version=FEATURE_VERSION, code_commit=GitRepo().head(),
                  cutoff=DEFAULT_CUTOFF, source_sha256=hashlib.sha256(raw).hexdigest(), seed=args.seed,
                  fit_users=len(fit), test_users=len(test), fit_votes=len(training), test_votes=len(heldout),
                  baseline_hits=sum(vote_target(notes, prior, 0.) == t for t, notes, prior in heldout),
                  calibrated_hits=sum(vote_target(notes, prior, weight) == t for t, notes, prior in heldout),
                  baseline_weight=0., calibrated_weight=weight, bot_version=CalibratedPlayerBot(weight).version,
                  deleted_users_remaining=len(deleted & set(samples)), seconds=round(perf_counter() - started, 3))
    output = json.dumps(report, ensure_ascii=False)
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(output + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
