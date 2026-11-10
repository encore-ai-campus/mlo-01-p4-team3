"""D-10-01 canary and rollback drill using a local-only MLflow registry."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
from time import perf_counter

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import ENGINE_VERSION, PRESETS, CommandEnvelope, DataSource, GameMeta
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.sim.runner import run_game
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource, InMemoryGameRepository
from server.infra.model_selector import lookup_model
from tools.gitrepo import GitRepo
from tools.perf.engine_bench import bench_player_command, percentile
from tools.ut_freeze import DEFAULT_CUTOFF, DEFAULT_OPEN, KST


def canary_decision(canary, control, canary_win, control_win, fallback, p95):
    """Apply P-17 simulated-game signals. Intent edit/cancel cannot be observed here."""
    if fallback > .05 or p95 > 100:
        return "rollback"
    if min(canary, control) < 30:
        return "wait"
    if abs(canary_win - control_win) > .10 + 1e-12:
        return "hold"
    return "promote"


class _MeasuredRule(FakeSuspicionModel):
    def __init__(self, version):
        super().__init__()
        self.model_version = version
        self.calls, self.failures, self.latency = 0, 0, []

    def estimate(self, obs):
        started = perf_counter()
        self.calls += 1
        self.failures += int(self.fail)
        try:
            return super().estimate(obs)
        finally:
            self.latency.append((perf_counter() - started) * 1000)


class _ReplaySelector:
    """Choose local registry aliases once per simulated game."""

    def __init__(self, client, name, models):
        self.client, self.name, self.models = client, name, models
        self.enabled = True

    def select(self, game_id, player_pseudonym):
        """Apply a stable 20 percent assignment exclusively to bot games."""
        bucket = int.from_bytes(hashlib.sha256(game_id.encode()).digest()[:8], "big") % 100
        canary = self.enabled and player_pseudonym is None and bucket < 20
        model = lookup_model(self.client, self.name, "canary" if canary else "production")
        return {"suspicion": model["version"]}, canary

    def suspicion_model(self, version):
        """Resolve the version stored at game start, independently of current aliases."""
        return self.models[version]


def main(argv=None):
    """Run the P-17 cohort and inject a model failure without touching any live aliases."""
    import mlflow

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if datetime.fromisoformat(DEFAULT_OPEN) <= datetime.now(KST) < datetime.fromisoformat(DEFAULT_CUTOFF):
        parser.error("simulation is paused during the formal user test")
    commit = GitRepo().head()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    uri = "sqlite:///" + (out / "tracking.db").as_posix()
    client = mlflow.MlflowClient(tracking_uri=uri, registry_uri=uri)
    name = "d10-01-drill-suspicion"
    client.create_registered_model(name)
    versions = dict(data_version="fixture:rule-v0", feature_version=FakeSuspicionModel.feature_version,
                    code_commit=commit)
    marker = out / "rule.json"
    marker.write_text(json.dumps(versions), encoding="utf-8")
    previous, probe = [str(client.create_model_version(name, marker.as_uri()).version) for _ in range(2)]
    client.set_registered_model_alias(name, "production", previous)
    client.set_registered_model_alias(name, "previous", previous)
    client.set_registered_model_alias(name, "canary", probe)
    selector = _ReplaySelector(client, name, {v: _MeasuredRule(v) for v in (previous, probe)})
    content = FixtureContentSource().load("*")
    engine = PaldoEngine(RuleV0Policy(content))

    def start(index, seed):
        gid = f"d1001-drill-{index:04d}"
        fixed, canary = selector.select(gid, None)
        meta = GameMeta(gid, PRESETS["base6"], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                        ENGINE_VERSION, content.content_version, model_versions=fixed, canary=canary)
        return meta, EngineDeps(content, selector.suspicion_model(fixed["suspicion"]))

    # Identical seed streams per cohort isolate alias routing from model quality.
    cohorts = defaultdict(list)
    for index in range(1000):
        meta, deps = start(index, 0)
        group = cohorts[meta.canary]
        meta.seed = 100 + len(group)
        group.append(run_game(engine, deps, meta, LogicBot()))
        if min(len(cohorts[True]), len(cohorts[False])) >= 30:
            break
    rates = {k: sum(g.winner == "village" for g in v) / len(v) for k, v in cohorts.items()}
    canary = selector.suspicion_model(probe)
    p95 = percentile(canary.latency, 95)
    verdict = canary_decision(len(cohorts[True]), len(cohorts[False]), rates[True], rates[False],
                              canary.failures / canary.calls, p95)
    # This drill uses the previous rules in both versions, regardless of the cohort verdict.
    active_meta = next(meta for i in range(1000, 2000) if (meta := start(i, 91)[0]).canary)
    active_deps = EngineDeps(content, canary)
    state = engine.new_game(active_meta, active_deps)
    repo = InMemoryGameRepository()
    repo.create(state.state, state.truth, [])
    if verdict == "promote":
        client.set_registered_model_alias(name, "production", probe)
    production_before = lookup_model(client, name, "production")["version"]
    healthy_fallback = canary.failures / canary.calls
    canary.fail = True
    calls_before, failures_before = canary.calls, canary.failures
    adverse = run_game(engine, active_deps, active_meta, LogicBot())
    injected_rate = (canary.failures - failures_before) / (canary.calls - calls_before)
    if canary_decision(1, 0, 0, 0, injected_rate, 0) != "rollback":
        raise RuntimeError("injected fallback did not trigger rollback")
    client.set_model_version_tag(name, probe, "paldo.rollback.reason", "injected_fallback")
    client.set_registered_model_alias(name, "production", lookup_model(client, name, "previous")["version"])
    client.delete_registered_model_alias(name, "canary")
    selector.enabled = False
    next_meta, _ = start(2001, 92)
    saved, truth = repo.load(active_meta.game_id)
    pinned = saved.meta.model_versions["suspicion"]
    view = engine.player_view(saved, content)
    command = bench_player_command(view, random.Random(saved.meta.seed))
    continued = engine.apply(saved, truth, CommandEnvelope(saved.meta.game_id, "after-rollback", saved.state_version, command),
                             EngineDeps(content, selector.suspicion_model(pinned)))
    if not continued.accepted or continued.state.meta.model_versions["suspicion"] != probe or next_meta.model_versions["suspicion"] != previous:
        raise RuntimeError("version pin or rollback failed")
    report = dict(**versions, canary_games=len(cohorts[True]), control_games=len(cohorts[False]),
                  canary_village_win_rate=rates[True], control_village_win_rate=rates[False],
                  canary_fallback_rate=healthy_fallback, canary_p95_ms=p95, cohort_verdict=verdict,
                  intent_edit_cancel="unmeasurable", injected_fallback_rate=injected_rate,
                  adverse_game_completed=bool(adverse.winner), production_before_rollback=production_before,
                  rolled_back_to=previous,
                  running_game_version=pinned, next_game_version=next_meta.model_versions["suspicion"],
                  seconds=round(perf_counter() - started, 3))
    output = json.dumps(report, ensure_ascii=False)
    (out / "report.json").write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
