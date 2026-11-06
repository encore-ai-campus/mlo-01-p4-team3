"""D-06-01: 봇별 실제 엔진 200판·동일 시드 재실행·지표 표·처리량을 기록한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from engine.domain import params
from engine.domain.engine import BaselinePolicy, PaldoEngine
from engine.domain.model import CONTRACT_VERSION, PRESETS, DataSource, GameMeta
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from engine.sim.bots import BOT_TYPES
from engine.sim.report import markdown_table, summarize
from engine.sim.runner import run_game
from tools.evidence_result import write_result
from tools.gitrepo import GitRepo
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = "sim_bench"


def main(argv=None) -> int:
    """콘텐츠·NPC 기준 정책은 고정하고 플레이어 정책 4종을 비교한다."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=200, help="정책마다 실행할 판 수")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--preset", choices=("short5", "base6", "base7", "large8"), default="base6")
    ap.add_argument("--out", default="out/sim/D-06-01")
    ap.add_argument("--no-write", action="store_true", help="증빙 결과 JSON을 쓰지 않는 시험 실행")
    args = ap.parse_args(argv)
    if not 1 <= args.games < 1_000_000 or args.seed < 0:
        ap.error("games는 1 이상 1000000 미만, seed는 0 이상")
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    preset = PRESETS[args.preset]
    runs, metrics = [], {}
    elapsed_total = 0.
    mismatches = 0
    log(COMPONENT, "info", "시뮬레이션 시작", source="bot", preset=args.preset,
        games_per_policy=args.games, seed=args.seed, npc_policy_version=BaselinePolicy.version)
    for name, bot_type in BOT_TYPES.items():
        def run(index):
            seed = args.seed * 1_000_000 + index
            meta = GameMeta(f"sim_{args.preset}_{name}_{args.seed}_{index:05d}", preset, seed,
                DataSource.BOT, params.RULE_PARAMS_VERSION, "paldo-engine", content.content_version,
                model_versions={"suspicion": FakeSuspicionModel.model_version, "player_bot": bot_type.version})
            return run_game(PaldoEngine(), deps, meta, bot_type())

        started = time.perf_counter()
        try:
            first = [run(i) for i in range(args.games)]
            elapsed = time.perf_counter() - started
            log(COMPONENT, "info", "정책 실행 완료", policy=bot_type.version,
                games=len(first), illegal_commands=sum(r.illegal_commands for r in first),
                duration_ms=round(elapsed * 1000, 3))
            replay = [run(i) for i in range(args.games)]
        except Exception as exc:
            log(COMPONENT, "error", "시뮬레이션 실패", policy=bot_type.version, error_type=type(exc).__name__)
            raise
        bad = sum(a != b for a, b in zip(first, replay))
        mismatches += bad
        runs.extend(first)
        elapsed_total += elapsed
        row = summarize(first)[0]
        metrics[name + "_games"] = {"value": len(first), "unit": "판"}
        metrics[name + "_illegal_commands"] = {"value": row["illegal_commands"], "unit": "건"}
        metrics[name + "_games_per_sec"] = {"value": len(first) / elapsed, "unit": "판/초"}
        for key in ("village_win_rate", "changgwi_win_rate", "no_lock_rate", "confession_rate"):
            if row[key] is not None:
                metrics[name + "_" + key] = {"value": row[key] * 100, "unit": "%"}
        metrics[name + "_days_mean"] = {"value": row["days_mean"], "unit": "낮"}
        metrics[name + "_meaningful_choices_mean"] = {"value": row["meaningful_choices_mean"], "unit": "회/판"}
        log(COMPONENT, "info", "동일 시드 재현 검증", policy=bot_type.version, games=len(replay), mismatches=bad)
    if mismatches:
        raise RuntimeError(f"동일 시드 결과 불일치: {mismatches}")
    metrics.update(completed_games={"value": len(runs), "unit": "판"},
        replayed_games={"value": len(runs), "unit": "판"},
        replay_mismatches={"value": mismatches, "unit": "건"},
        elapsed_s={"value": elapsed_total, "unit": "s"},
        games_per_sec={"value": len(runs) / elapsed_total, "unit": "판/초"})
    rows = summarize(runs)
    table = markdown_table(rows)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps([asdict(r) for r in runs], ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    (out / "report.md").write_text(table, encoding="utf-8")
    digest = hashlib.sha256()
    for path in sorted((ROOT / "engine/sim").glob("*.py")) + [Path(__file__).resolve()]:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    source = f"python -m tools.perf.sim_bench --games {args.games} --seed {args.seed} --preset {args.preset}"
    manifest = dict(command=source, commit=GitRepo(ROOT).head(), source="bot", preset=args.preset,
        seed=args.seed, content_version=content.content_version, contract_version=CONTRACT_VERSION,
        rule_version=params.RULE_PARAMS_VERSION, npc_policy_version=BaselinePolicy.version,
        suspicion_model_version=FakeSuspicionModel.model_version, player_bot_versions={
            name: bot.version for name, bot in BOT_TYPES.items()}, source_sha256=digest.hexdigest(),
        metrics=metrics, report=rows)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(table)
    if not args.no_write:
        result = write_result("D-06-01", "simulation", metrics, source)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(result.relative_to(ROOT)),
            source_sha256=digest.hexdigest(), completed_games=len(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
