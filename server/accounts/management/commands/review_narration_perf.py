"""복기 해설 LLM 요약 실측(E-10-01). 실제 생성 서버에 판마다 요청해 시간·토큰·검사 결과를 잰다.

    python server/manage.py review_narration_perf --url http://127.0.0.1:<port> --games 12 --burst 4 \
        [--samples <저장소 밖 경로.md>] [--no-write]

- 판: 운영 엔진 조립(`ContentPolicyEngine`, `content/bundle_v1.json`)으로 시드 1부터 `LogicBot`이 끝까지 둔다.
- 순차: 판마다 등록 → 처리 1건(대기 0). 동시 종료: `--burst`판을 한꺼번에 등록한 뒤 작업자 1개가 FIFO로 처리.
- 결과 JSON(`docs/evidence/perf/E-10-01/review_narration.json`)에는 집계만 둔다(판 ID·문장 본문 없음).
  문장 표본은 `--samples` 경로(저장소 밖)에만 쓴다.

계층: 인프라(측정 실행: 인자 파싱, 판 조립, 측정, 출력). 규칙은 `llm/review_narration.py`, 흐름은 `server/app/review_narration.py`.
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from pathlib import Path

from engine.domain import params
from engine.domain.model import PRESETS, DataSource, GameMeta
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.testing.fakes import FakeSuspicionModel, InMemoryGameRepository, InMemoryProgressRepository

from django.core.management.base import BaseCommand

ROOT = Path(__file__).resolve().parents[4]


def _service():
    from server.app.usecases import GameService
    from server.infra.game_factory import ContentPolicyEngine
    from server.infra.review_lines import load_review_lines
    from tools.check_content import load_bundle
    content = load_bundle(ROOT / "content" / "bundle_v1.json")
    seeds = {"next": 0}

    def prepare(data, player):
        return GameMeta("", PRESETS[data.preset_id], seeds["next"], DataSource.HUMAN, params.RULE_PARAMS_VERSION,
                        "paldo-engine", content.content_version, player_pseudonym=player)
    service = GameService(ContentPolicyEngine(), InMemoryGameRepository(), InMemoryProgressRepository(),
                          lambda meta: EngineDeps(content, FakeSuspicionModel()), prepare)
    service.review_lines = load_review_lines()
    return service, content, seeds


def play(service, seeds, seed: int, preset: str = "base6"):
    """판 하나를 LogicBot으로 끝까지 두고 (복기 DTO, 공개 기록, 시드)를 돌려준다."""
    from server.app.dto import StartGameInput, SubmitCommandInput
    seeds["next"] = seed
    player = f"perf-{seed}"
    game = service.start(StartGameInput(player, preset, None, "f"), player, True)
    gid = game.view.game_id
    rng, bot = random.Random(seed), LogicBot()
    for step in range(600):
        view = service.detail(gid, player).view
        if view.result is not None:
            break
        cmd = bot.choose(view, rng)
        service.command(SubmitCommandInput(gid, f"c{step:04d}", view.state_version, cmd), player)
    state, _ = service.owned(gid, player)
    return service.review(gid, player), state.public, state.meta.seed


def _server_slots(url: str) -> tuple[int, int]:
    """생성 서버 `/props`에서 (슬롯 수, 슬롯당 문맥 토큰)을 읽는다. 못 읽으면 (0, 0)."""
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=10) as resp:
            props = json.loads(resp.read(1 << 20))
        return (int(props.get("total_slots", 0)),
                int((props.get("default_generation_settings") or {}).get("n_ctx", 0)))
    except (OSError, ValueError):
        return (0, 0)


def _pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def main(argv=None):  # noqa: C901  (측정 순서를 한곳에서 읽히게 둔다)
    """실제 생성 서버로 종료 판을 순차·동시에 만들어 해설 작업을 돌리고 집계 결과 JSON과 저장소 밖 표본을 쓴다."""
    from server.app.review_narration import NarrationSettings, ReviewNarrationService
    from server.infra.kanana_narrator import KananaNarrator
    from server.infra.review_jobs import MemoryReviewJobStore
    from tools.evidence_result import write_result
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--burst", type=int, default=4)
    ap.add_argument("--samples", default="")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    slots_before = _server_slots(args.url)

    service, content, seeds = _service()
    store = MemoryReviewJobStore()
    narration = ReviewNarrationService(store, KananaNarrator(args.url), settings=NarrationSettings())
    rows, samples = [], []

    def run_one(seed):
        output, public, gseed = play(service, seeds, seed)
        narration.ensure(output, content, public, gseed)
        return output

    # 순차
    for seed in range(1, args.games + 1):
        output = run_one(seed)
        outcome = narration.process_next()
        rows.append(("seq", outcome, output))
        print(f"seq {seed}: {outcome.status} gen={outcome.gen_ms}ms in={outcome.tokens_in} out={outcome.tokens_out} "
              f"llm={outcome.llm_npcs} tpl={outcome.template_npcs} rej={outcome.rejects}", flush=True)
    # 동시 종료
    burst_outputs = [run_one(1000 + i) for i in range(args.burst)]
    while (outcome := narration.process_next()) is not None:
        rows.append(("burst", outcome, None))
        print(f"burst: {outcome.status} queue={outcome.queue_ms}ms gen={outcome.gen_ms}ms", flush=True)

    names = {c.npc_id: c.name_ko for c in content.characters}
    for kind, outcome, output in rows:
        doc = store.get(outcome.game_id)
        if output is None or not doc or doc.get("status") != "done":
            continue
        blocks = {b["npc_id"]: b for b in doc["payload"]["blocks"]}
        for npc in doc["npcs"]:
            items = {i["item_id"]: i for i in blocks[npc["npc"]]["items"]}
            for s in npc["sentences"]:
                samples.append((npc["source"], names.get(npc["npc"], npc["npc"]), items[s["cite"]], s["text"]))

    done = [o for _, o, _ in rows if o.status == "done"]
    seq = [o for k, o, _ in rows if k == "seq"]
    burst = [o for k, o, _ in rows if k == "burst"]
    rejects: dict[str, int] = {}
    for o in done:
        for k, v in o.rejects.items():
            rejects[k] = rejects.get(k, 0) + v
    gen_s = [o.gen_ms / 1000 for o in seq if o.status == "done"]
    tps = [o.tokens_out / (o.gen_ms / 1000) for o in seq if o.status == "done" and o.gen_ms]
    def m(value, unit=""):
        return {"value": value, "unit": unit}

    llm_n = sum(o.llm_npcs for o in done)
    tpl_n = sum(o.template_npcs for o in done)
    burst_total = [(o.queue_ms + o.gen_ms) / 1000 for o in burst]
    metrics = {
        "games_sequential": m(len(seq), "판"), "games_burst": m(len(burst), "판"),
        "jobs_done": m(sum(1 for _, o, _ in rows if o.status == "done"), "건"),
        "jobs_failed_or_timeout": m(sum(1 for _, o, _ in rows if o.status in ("failed", "timeout")), "건"),
        "gen_seconds_mean": m(round(statistics.mean(gen_s), 2) if gen_s else 0, "s"),
        "gen_seconds_p50": m(round(_pct(gen_s, 0.5) or 0, 2), "s"),
        "gen_seconds_p95": m(round(_pct(gen_s, 0.95) or 0, 2), "s"),
        "gen_seconds_max": m(round(max(gen_s), 2) if gen_s else 0, "s"),
        "tokens_in_mean": m(round(statistics.mean(o.tokens_in for o in done), 1) if done else 0, "tok/판"),
        "tokens_out_mean": m(round(statistics.mean(o.tokens_out for o in done), 1) if done else 0, "tok/판"),
        "output_tokens_per_second_mean": m(round(statistics.mean(tps), 2) if tps else 0, "tok/s"),
        "npcs_llm": m(llm_n, "명"), "npcs_template": m(tpl_n, "명"),
        "npc_llm_ratio": m(round(100 * llm_n / (llm_n + tpl_n), 1) if llm_n + tpl_n else 0, "%"),
        "whole_game_template": m(sum(1 for o in done if o.rejects.get("parse_failed")), "판"),
        "burst_queue_seconds_max": m(round(max(o.queue_ms for o in burst) / 1000, 1) if burst else 0, "s"),
        "burst_total_seconds_max": m(round(max(burst_total), 1) if burst_total else 0, "s"),
        "burst_within_deadline": m(sum(1 for t in burst_total if t <= NarrationSettings().deadline_s), "판"),
        "deadline_seconds": m(NarrationSettings().deadline_s, "s"),
        "server_total_slots": m(slots_before[0], "개"),
        "server_ctx_per_slot": m(slots_before[1], "tok"),
    }
    if _server_slots(args.url) != slots_before:  # 측정 중 서버 설정이 바뀌면 그 회차는 다시 잰다
        print("server settings changed during the run; measure again", flush=True)
        return 1
    for reason in ("format_length", "cite_mismatch", "forbidden_entity", "action_mismatch", "time_mismatch",
                   "invented_cause", "parse_failed", "timeout"):
        metrics[f"reject_{reason}"] = m(rejects.get(reason, 0), "명")
    for n, t in enumerate(burst_total, 1):
        metrics[f"burst_{n}_total_seconds"] = m(round(t, 1), "s")
    print(metrics)
    if not args.no_write:
        print(write_result("E-10-01", "review_narration", metrics,
                                   "server/accounts/management/commands/review_narration_perf.py"))
    if args.samples:
        lines = ["# 복기 해설 LLM 문장 표본 (E-10-01 실측, 저장소 밖)", "",
                 "| 출처 | 인물 | 항목 | 문장 |", "|---|---|---|---|"]
        for src, name, item, text in samples:
            what = f"{item['day']}일째 {item['kind']} {item.get('target') or item.get('victim') or ''} ({item['code']})"
            lines.append(f"| {src} | {name} | {what.strip()} | {text} |")
        Path(args.samples).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


class Command(BaseCommand):
    """manage.py review_narration_perf: 복기 해설 LLM 실측(E-10-01)."""
    help = "복기 해설 LLM 요약을 실제 생성 서버로 실측한다(E-10-01)."

    def add_arguments(self, parser):
        """측정 인자(--url, --games, --burst, --samples, --no-write)는 위치 인자로 모아 main의 argparse에 맡긴다."""
        parser.add_argument("args", nargs="*")

    def run_from_argv(self, argv):
        """인자를 그대로 `main`에 넘긴다(측정 인자는 argparse가 맡는다)."""
        raise SystemExit(main(argv[2:]))

    def handle(self, *args, **options):
        """인자를 main에 넘긴다."""
        return str(main(list(args)))
