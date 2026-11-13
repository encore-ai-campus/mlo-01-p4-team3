"""합성 데이터 생성기(표준 라이브러리만). 실제 엔진이 나오기 전 C(파이프라인)·D(학습)가 쓸 데이터.

실행: paldo/ 에서 `python -m tools.gen_synthetic --games 50 --seed 7 --out out/synthetic`
출력: events.jsonl(EventEnvelope), training_rows.jsonl(TrainingRow)
광고(--ads N): ad_serve.jsonl, ad_impression.jsonl, ad_click.jsonl, ad_hidden_ctr.json(평가 전용 정답). tools/gen_synthetic_ads.py
- 모든 행의 source는 "synthetic"이다. 사람 평가 세트(test_human)에 섞지 않는다(events.md).
- 같은 시드면 같은 출력(결정적). 판 규칙은 엔진 기준 함수(resolve_vote, check_end)를 쓴다.
- 특징값은 진실과 약하게 상관되도록 뽑은 가짜다. 모델 성능 판단에 쓰지 않고 파이프라인·형식 검증에만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from contracts.datasets import SplitKey, TrainingRow
from engine.domain import serde
from engine.domain.events import EventEnvelope, EventType, topic_of
from engine.domain.model import (
    CONTRACT_VERSION, PLAYER_ID, PRESETS, DataSource, Phase, check_end, resolve_vote, winner_of,
)

FEATURE_VERSION = "feat-0.1"
POOL = ["hunjang", "jumo", "blacksmith", "meosum", "yakcho", "gwabu",
        "ferryman", "gisaeng", "hunter", "monk", "boy"]


class _Game:
    def __init__(self, idx: int, preset_id: str, rng: random.Random, seed: int):
        self.preset = PRESETS[preset_id]
        self.game_id = f"syn_{seed}_{idx:05d}"
        self.rng = rng
        self.seed = seed
        self.seq = 0
        self.ver = 0
        self.events: list[EventEnvelope] = []
        self.rows: list[TrainingRow] = []
        residents = rng.sample(POOL, self.preset.n_active)
        self.possessed = set(rng.sample(residents, self.preset.n_possessed))
        victim = rng.choice([r for r in residents if r not in self.possessed])
        self.active = [r for r in residents if r != victim]
        self.missing = [victim]
        self.locked: list[str] = []
        self.mudang_active = True

    def emit(self, etype: EventType, day: int, phase: Phase, payload: dict) -> None:
        self.events.append(EventEnvelope(
            event_id=f"{self.game_id}:{self.seq:05d}", game_id=self.game_id, seq=self.seq,
            state_version=self.ver, event_type=etype.value, topic=topic_of(etype),
            occurred_at=f"2026-11-05T00:{self.seq // 60 % 60:02d}:{self.seq % 60:02d}Z", day=day, phase=phase.value,
            source=DataSource.SYNTHETIC, contract_version=CONTRACT_VERSION, preset_id=self.preset.preset_id,
            seed=self.seed, rule_version="rule-0.1", engine_version="synthetic",
            content_version="content-min-0.1", npc_policy_versions={}, model_versions={},
            payload=payload))
        self.seq += 1

    def g(self) -> int:
        return sum(1 for a in self.active if a in self.possessed)

    def v(self) -> int:
        return sum(1 for a in self.active if a not in self.possessed)

    def observe(self, day: int) -> None:
        """투표 직전 각 주민 관찰자의 후보별 특징 행. 라벨은 판 종료 후 join_labels에서 채운다."""
        k_left = self.g()
        for obs in self.active:
            cands = sorted(c for c in self.active if c != obs)
            snap = f"{self.game_id}:d{day}:{obs}"
            self.emit(EventType.OBSERVATION, day, Phase.VOTE, {"snapshot_id": snap, "observer": obs})
            for c in cands:
                bad = c in self.possessed
                f = {
                    "contradiction_count": float(self.rng.random() < (0.45 if bad else 0.2)),
                    "tell_count": float(self.rng.random() < (0.35 if bad else 0.15)),
                    "unverifiable_alibi": float(self.rng.random() < (0.7 if bad else 0.4)),
                    "suspected_by_victim": float(self.rng.random() < 0.1),
                    "day": float(day), "n_candidates": float(len(cands)),
                }
                self.rows.append(TrainingRow(
                    f"{snap}:{c}", self.game_id, snap, obs, c, day, "vote", self.preset.preset_id,
                    len(self.active) + 1, self.preset.n_possessed, k_left, len(cands), FEATURE_VERSION, f,
                    "synthetic", SplitKey(None, self.game_id, "2026-11-05")))

    def vote(self, day: int):
        voters = self.active + ([PLAYER_ID] if self.mudang_active else [])
        targets = self.active + ([PLAYER_ID] if self.mudang_active else [])

        def pick(voter):
            opts = [t for t in targets if t != voter]
            w = [3.0 if (t in self.possessed and voter not in self.possessed) else 1.0 for t in opts]
            return self.rng.choices(opts, w)[0]

        main = {vt: pick(vt) for vt in voters}
        out = resolve_vote(main)
        if out.locked is None and out.runoff_candidates:
            runoff = {vt: self.rng.choice([c for c in out.runoff_candidates if c != vt] or [None])
                      for vt in voters}
            runoff = {k: t for k, t in runoff.items() if t is not None}
            out = resolve_vote(main, runoff)
        self.emit(EventType.VOTES_REVEALED, day, Phase.VOTE, {"round": "main"})
        return out.locked

    def run(self) -> None:
        self.emit(EventType.GAME_STARTED, 1, Phase.MORNING, {"preset_id": self.preset.preset_id})
        for day in range(1, 20):
            self.ver += 1
            self.observe(day)
            locked = self.vote(day)
            mudang_locked = locked == PLAYER_ID
            if locked == PLAYER_ID:
                self.mudang_active = False
            elif locked:
                self.active.remove(locked)
                self.locked.append(locked)
            end = check_end(self.g(), self.v(), self.mudang_active, mudang_locked)
            self.emit(EventType.LOCK_RESOLVED, day, Phase.VOTE,
                      {"locked": locked, "revealed_possessed": None if locked in (None, PLAYER_ID)
                       else locked in self.possessed, "end": end.value if end else None})
            if end is None:  # 밤: 씐 자가 무당·씐 자 아닌 주민 하나를 데려간다
                self.ver += 1
                prey = [a for a in self.active if a not in self.possessed]
                victim = self.rng.choice(prey)
                self.active.remove(victim)
                self.missing.append(victim)
                end = check_end(self.g(), self.v(), self.mudang_active, False)
                self.emit(EventType.NIGHT_RESOLVED, day, Phase.NIGHT, {"victim": victim, "end": end.value if end else None})
            if end is not None:
                self.emit(EventType.GAME_ENDED, day, Phase.ENDED, {"winner": winner_of(end).value, "reason": end.value})
                self.emit(EventType.TRUTH_REVEALED, day, Phase.ENDED, {"possessed": sorted(self.possessed)})
                self.join_labels()
                return
        raise RuntimeError("판이 끝나지 않음")

    def join_labels(self) -> None:
        """진실 라벨은 판 종료 후에만 붙인다(events.md 학습-진실 경계)."""
        at = self.events[-1].occurred_at
        for r in self.rows:
            r.label = int(r.candidate in self.possessed)
            r.label_joined_at = at


def generate(n_games: int, seed: int, presets: list[str]) -> tuple[list[EventEnvelope], list[TrainingRow]]:
    rng = random.Random(seed)
    events, rows = [], []
    for i in range(n_games):
        g = _Game(i, presets[i % len(presets)], rng, seed)
        g.run()
        events += g.events
        rows += g.rows
    return events, rows


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--presets", default="base6,duo9")
    ap.add_argument("--out", default="out/synthetic")
    ap.add_argument("--ads", type=int, default=0, help="합성 광고 요청 수(0이면 생략)")
    a = ap.parse_args(argv)
    events, rows = generate(a.games, a.seed, a.presets.split(","))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, items in (("events.jsonl", events), ("training_rows.jsonl", rows)):
        (out / name).write_text("".join(json.dumps(serde.to_jsonable(x), ensure_ascii=False) + "\n" for x in items),
                                encoding="utf-8")
    print(f"{a.games} games, {len(events)} events, {len(rows)} rows -> {out}")
    if a.ads:
        from tools.gen_synthetic_ads import generate_ads
        serves, imps, clicks, truth, ledger = generate_ads(a.ads, a.seed)
        for name, items in (("ad_serve.jsonl", serves), ("ad_impression.jsonl", imps), ("ad_click.jsonl", clicks)):
            (out / name).write_text("".join(json.dumps(serde.to_jsonable(x), ensure_ascii=False) + "\n" for x in items),
                                    encoding="utf-8")
        (out / "ad_hidden_ctr.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
        bad = sum(c.status.value == "invalid" for c in clicks)
        won = sum(r.amount_mw for r in ledger.rows.values() if r.state.value == "confirmed") // 1000
        print(f"ads: {len(serves)} serves, {len(imps)} impressions, {len(clicks)} clicks ({bad} invalid), charged {won} won")


if __name__ == "__main__":
    main()
