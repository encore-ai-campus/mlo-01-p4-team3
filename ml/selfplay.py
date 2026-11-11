"""D-10-03 offline tabular self-play of the NPC voting decision layer."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import ENGINE_VERSION, PLAYER_ID, PRESETS, DataSource, GameMeta, Role
from engine.domain.npc import RuleV0Policy
from engine.domain.npc import vote as vt
from engine.domain.observation import Decision, ReasonCode
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.sim.runner import run_game
from engine.testing.fakes import FakeSuspicionModel
from tools.check_content import content_problems, load_bundle
from tools.evidence_result import write_result
from tools.jsonlog import log
from tools.perf.challenge_balance import wilson

ACTIONS = ("highest", "second", "lowest")


class SelfPlayPolicy(RuleV0Policy):
    """두 진영이 자기 관측에서 안전한 투표 후보의 순위를 학습하는 시험 정책."""
    version = "selfplay-tabular-v1"

    def __init__(self, content):
        """기존 규칙 포트와 빈 학습 표·에피소드 보상을 준비한다."""
        super().__init__(content)
        self.values = {}
        self.counts = {}
        self.episode = []
        self.training = False

    def vote_with_model(self, obs, candidates, rng, model, clock=None):
        """Keep the inference and safety boundaries, then select a learned rank."""
        beliefs = self.update_beliefs(obs)
        if obs.private.role is Role.VILLAGER:
            estimate, failure = vt.estimate(model, obs) if clock is None else vt.estimate(model, obs, clock)
            if estimate is None:
                decision = self.vote(obs, candidates, rng)
                decision.reason_codes.append(vt.FALLBACK_CODE[failure])
                return vt.VoteOutcome(decision, failure)
            scores = vt.combined_suspicion(
                vt.weighted_probs(estimate, self.villager.temperament(obs.observer)), beliefs.trust_in_mudang)
        else:
            scores = dict(beliefs.suspicion)
            scores[PLAYER_ID] = 100 - beliefs.trust_in_mudang
        legal = [candidate for candidate in candidates if candidate != obs.observer]
        pool, _ = vt.safe_pool(obs, beliefs, legal)
        if not pool:
            target = max(sorted(legal), key=lambda candidate: scores.get(candidate, 0))
            return vt.VoteOutcome(Decision(target, [ReasonCode.SCRIPTED, ReasonCode.FALLBACK_MAX_SUSPICION]))
        partner = obs.private.partner_id
        if obs.private.role is Role.POSSESSED and partner in pool and len(pool) > 1:
            pool = [candidate for candidate in pool if candidate != partner]
        ranked = sorted(pool, key=lambda candidate: (-scores.get(candidate, 0), candidate))
        key = (obs.private.role.value, min(obs.day, 3), obs.phase.value,
               min(2, int(beliefs.trust_in_mudang // 34)), min(len(ranked), 3))
        values = self.values.get(key, [0.0] * len(ACTIONS))
        action = (rng.randrange(len(ACTIONS)) if self.training and rng.random() < .2
                  else max(range(len(ACTIONS)), key=values.__getitem__))
        target = ranked[min(action, len(ranked) - 1)] if action < 2 else ranked[-1]
        if self.training:
            self.episode.append((key, action))
        return vt.VoteOutcome(Decision(target, [ReasonCode.SCRIPTED]))

    def learn(self, winner):
        """First-visit Monte Carlo update using each faction's terminal reward."""
        for key, action in set(self.episode):
            reward = float((key[0] == Role.VILLAGER.value) == (winner == "village"))
            values = self.values.setdefault(key, [0.0] * len(ACTIONS))
            counts = self.counts.setdefault(key, [0] * len(ACTIONS))
            counts[action] += 1
            values[action] += (reward - values[action]) / counts[action]
        self.episode.clear()


def episode(policy, content, seed, game_id, preset="base6"):
    """Run a completed bot game through the existing simulation entry point."""
    meta = GameMeta(game_id, PRESETS[preset], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    ENGINE_VERSION, content.content_version,
                    model_versions={"suspicion": FakeSuspicionModel.model_version})
    return run_game(PaldoEngine(policy), EngineDeps(content, FakeSuspicionModel()), meta, LogicBot())


def summary(runs):
    """Return win rate, Wilson interval and duration range of accepted games."""
    wins = sum(run.winner == "village" for run in runs)
    return dict(games=len(runs), village_win_rate=wins / len(runs), win_rate_95=wilson(wins, len(runs)),
                days_range=[min(run.days for run in runs), max(run.days for run in runs)],
                illegal_commands=sum(run.illegal_commands for run in runs))


def main(argv=None):
    """Train both factions, freeze the table, then compare on disjoint identical seeds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-games", type=int, default=1000)
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1111)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.train_games <= 100000 or not 1 <= args.games <= 100000 or args.seed < 0:
        parser.error("positive game counts up to 100000 and nonnegative seed required")
    content = load_bundle(Path("content/bundle_v1.json"))
    if content_problems(content):
        raise ValueError("invalid content")
    args.out.mkdir(parents=True, exist_ok=False)
    candidate = SelfPlayPolicy(content)
    candidate.training = True
    start = args.seed * 1000000
    for i in range(args.train_games):
        run = episode(candidate, content, start + i, f"selfplay_train_{i}")
        candidate.learn(run.winner)
        if (i + 1) % 100 == 0:
            log("selfplay", "info", "training", games=i + 1)
    candidate.training = False
    baseline = RuleV0Policy(content)
    runs = {"baseline": [], "selfplay": []}
    for i in range(args.games):
        seed = start + args.train_games + i
        for name, policy in (("baseline", baseline), ("selfplay", candidate)):
            runs[name].append(episode(policy, content, seed, f"selfplay_eval_{i}"))
        if (i + 1) % 100 == 0:
            log("selfplay", "info", "paired_evaluation", games=i + 1)
    report = dict(rule_version=params.RULE_PARAMS_VERSION, content_version=content.content_version,
                  player_bot=LogicBot.version, suspicion_model=FakeSuspicionModel.model_version,
                  seed=args.seed, train_seed_range=[start, start + args.train_games - 1],
                  evaluation_seed_range=[start + args.train_games, start + args.train_games + args.games - 1],
                  train_games=args.train_games, states=len(candidate.values),
                  policies={name: summary(items) for name, items in runs.items()},
                  same_outcomes=sum(a.winner == b.winner for a, b in zip(runs["baseline"], runs["selfplay"])),
                  adopted=False)
    table = [dict(state=list(key), values=candidate.values[key], visits=candidate.counts[key])
             for key in sorted(candidate.values)]
    outputs = (("report", report), ("policy", table),
               ("games", {name: [asdict(run) for run in items] for name, items in runs.items()}))
    for name, value in outputs:
        (args.out / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    if args.record:
        metrics = {f"{name}_{key}": {"value": result[key], "unit": "ratio" if key == "village_win_rate" else "count"}
                   for name, result in report["policies"].items()
                   for key in ("games", "village_win_rate", "illegal_commands")}
        write_result("D-10-03", "selfplay", metrics,
                     f"python -m ml.selfplay --train-games {args.train_games} --games {args.games} "
                     f"--seed {args.seed} --out <new directory> --record")
    log("selfplay", "info", "comparison_finished", **report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
