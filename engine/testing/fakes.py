"""모든 포트의 가짜 구현. 결정적(시드 고정)이며 외부 의존이 없다.

용도: 각 흐름이 다른 흐름의 완성을 기다리지 않고 개발·테스트한다(docs/contracts/parallel_dev.md).
- A(엔진): FakeSuspicionModel, FixtureContentSource
- B(웹·API): ScriptedGameEngine, InMemoryGameRepository, FakeIntentParser, FakeLineRenderer
- C(인프라/파이프라인): ListEventSink, InMemoryGameRepository를 기준 동작으로 비교
- D·E: 계약 테스트의 기대 동작 기준
"""
from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import replace
from pathlib import Path
from typing import Optional

from ..domain import serde
from ..domain.content import ContentBundle
from ..domain.events import DomainEvent, EventEnvelope, EventType
from ..domain.model import (
    Command, CommandEnvelope, CommandKind, EvidenceGroup, EvidenceRef, EvidenceType, GameMeta,
    GameState, GameTruth, InterrogationKind, ObservationSnapshot, Phase, PlayerProgress, RejectCode,
    missing_slots,
)
from ..ports import (
    CommandCandidate, EngineDeps, ParseContext, ParseFailure, ParseResult, PlayerView,
    RenderRequest, RenderResult, RenderStatus, StoredCommand, SuspicionEstimate, TransitionResult,
    UtteranceForm, VersionConflict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"


# ---------------------------------------------------------------- 의심 모델
class FakeSuspicionModel:
    """관찰자의 규칙 의심도(beliefs.suspicion)를 확률로 바꾼다. 창귀 2명이면 쌍 조합 주변확률."""
    feature_version = "fake-1"
    model_version = "fake-suspicion-1"

    def __init__(self, fail: bool = False):
        self.fail = fail

    def estimate(self, obs: ObservationSnapshot) -> SuspicionEstimate:
        from ..ports import ModelUnavailable
        if self.fail:
            raise ModelUnavailable("fake failure")
        cands = list(obs.candidates)
        score = {c: obs.beliefs.suspicion.get(c, 50.0) / 25.0 for c in cands}
        k = obs.n_possessed_remaining
        contributions = {c: {EvidenceGroup.SOCIAL: score[c]} for c in cands}
        if k <= 0 or not cands:
            return SuspicionEstimate(obs.observer, cands, {c: 0.0 for c in cands}, contributions,
                                     self.model_version, self.feature_version)
        if k == 1:
            z = sum(math.exp(s) for s in score.values())
            probs = {c: math.exp(score[c]) / z for c in cands}
            return SuspicionEstimate(obs.observer, cands, probs, contributions,
                                     self.model_version, self.feature_version)
        pairs = {}
        for i, a in enumerate(cands):
            for b in cands[i + 1:]:
                key = "|".join(sorted((a, b)))
                pairs[key] = math.exp(score[a] + score[b])
        z = sum(pairs.values())
        pairs = {kk: vv / z for kk, vv in pairs.items()}
        probs = {c: sum(p for kk, p in pairs.items() if c in kk.split("|")) for c in cands}
        return SuspicionEstimate(obs.observer, cands, probs, contributions,
                                 self.model_version, self.feature_version, pair_probs=pairs)


# ---------------------------------------------------------------- 의도 해석기
_KEYWORDS: list[tuple[str, CommandKind, Optional[InterrogationKind]]] = [
    ("수상하다고?", CommandKind.INTERROGATE, InterrogationKind.SUSPICION_REASON),   # 인용 질문형
    ("왜 다르", CommandKind.INTERROGATE, InterrogationKind.CONTRADICTION),
    ("어디 있었", CommandKind.INTERROGATE, InterrogationKind.WHEREABOUTS),
    ("왜 의심", CommandKind.INTERROGATE, InterrogationKind.SUSPICION_REASON),
    ("안 맞", CommandKind.POINT_CONTRADICTION, None),
    ("점괘", CommandKind.REVEAL_DIVINATION, None),
    ("결백", CommandKind.DEFEND, None),
    ("아니야", CommandKind.DEFEND, None),
    ("수상", CommandKind.SUSPECT, None),
    ("지켜보", CommandKind.SILENCE, None),
    ("상관없", CommandKind.EXPLAIN, None),
]


class FakeIntentParser:
    """키워드 + 별칭 사전 매칭. 실제 모델이 아니며 계약 형태만 맞춘다."""
    versions = {"model": "fake-intent-1", "tokenizer": "none", "alias_dict": "fixture",
                "matcher": "fake-1", "threshold": "always-confirm"}

    def parse(self, text: str, ctx: ParseContext) -> ParseResult:
        t = text.strip()
        if not t:
            return ParseResult([], ParseFailure.EMPTY_INPUT, True, dict(self.versions))
        # 인물 위치: 별칭이 처음 나오는 위치. 의도 키워드마다 그 앞에서 가장 가까운 인물을 대상으로 한다.
        pos = {npc: min(t.find(a) for a in al if a in t) for npc, al in ctx.aliases.items()
               if any(a in t for a in al)}
        form = UtteranceForm.QUESTION if t.endswith("?") or t.endswith("오?") else UtteranceForm.ASSERTION
        cands: list[CommandCandidate] = []
        used: list[tuple[int, int]] = []
        for kw, kind, ik in _KEYWORDS:
            kpos = t.find(kw)
            if kpos < 0 or any(a <= kpos < b for a, b in used):
                continue
            used.append((kpos, kpos + len(kw)))
            before = [n for n, p_ in pos.items() if p_ < kpos]
            near = max(before, key=lambda n: pos[n]) if before else (min(pos, key=pos.get) if pos else None)
            target = near or ctx.selected_npc
            cmd = Command(kind=kind, actor="mudang")
            if kind is CommandKind.INTERROGATE:
                cmd.addressee = target or ctx.last_addressee
                cmd.interrogation_kind = ik
                if ik is InterrogationKind.CONTRADICTION:
                    cmd.evidence = list(ctx.selected_cards)
            elif kind in (CommandKind.SUSPECT, CommandKind.DEFEND):
                cmd.target = target
            elif kind is CommandKind.POINT_CONTRADICTION:
                cmd.evidence = list(ctx.selected_cards)
            cands.append(CommandCandidate(cmd, 0.6, form, missing_slots(cmd)))
        if not cands:
            return ParseResult([], ParseFailure.UNSUPPORTED_INTENT, True, dict(self.versions))
        failure = ParseFailure.MISSING_TARGET if all(c.missing_slots for c in cands) else None
        return ParseResult(cands, failure, True, dict(self.versions))


# ---------------------------------------------------------------- 대사 표현기
class FakeLineRenderer:
    """mode: "done"(즉시 완료) | "timeout"(마감 초과 → 폴백) | "fail"."""

    def __init__(self, mode: str = "done"):
        self.mode = mode
        self.jobs: dict[str, RenderRequest] = {}
        self.cancelled: set[str] = set()

    def submit(self, req: RenderRequest) -> None:
        self.jobs[req.job_id] = req

    def poll(self, job_id: str) -> RenderResult:
        req = self.jobs[job_id]
        if job_id in self.cancelled:
            return RenderResult(job_id, RenderStatus.CANCELLED, None, fallback=True)
        if self.mode == "timeout":
            return RenderResult(job_id, RenderStatus.TIMEOUT, None, failure_reason="deadline", fallback=True)
        if self.mode == "fail":
            return RenderResult(job_id, RenderStatus.FAILED, None, failure_reason="fake", fallback=True)
        return RenderResult(job_id, RenderStatus.DONE, f"{req.fact_sentence} …(가짜 표현)",
                            latency_ms=1, queue_ms=0, model_version="fake-llm", prompt_version="fake",
                            quantization="fake", fallback=False)

    def cancel(self, job_id: str) -> None:
        self.cancelled.add(job_id)


# ---------------------------------------------------------------- 저장소
class InMemoryGameRepository:
    """C의 Mongo 구현이 따라야 할 기준 동작."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def create(self, state: GameState, truth: GameTruth, outbox: list[EventEnvelope]) -> None:
        if state.meta.game_id in self.docs:
            raise ValueError("이미 있는 판")
        self.docs[state.meta.game_id] = {
            "state": copy.deepcopy(state), "truth": copy.deepcopy(truth),
            "outbox": [], "commands": {}, "dedup": set(), "next_seq": 0,
        }
        self._append(self.docs[state.meta.game_id], outbox)

    @staticmethod
    def _append(d: dict, outbox: list[EventEnvelope]) -> None:
        for e in outbox:
            e = replace(e, seq=d["next_seq"], event_id=f"{e.game_id}:{d['next_seq']:05d}")
            d["outbox"].append(e)
            d["next_seq"] += 1

    def load(self, game_id: str) -> tuple[GameState, GameTruth]:
        d = self.docs[game_id]
        return copy.deepcopy(d["state"]), copy.deepcopy(d["truth"])

    def find_command(self, game_id: str, command_id: str) -> Optional[StoredCommand]:
        return self.docs[game_id]["commands"].get(command_id)

    def commit(self, game_id: str, expected_version: int, state: GameState, truth: GameTruth,
               outbox: list[EventEnvelope], command: StoredCommand) -> None:
        d = self.docs[game_id]
        actual = d["state"].state_version
        if actual != expected_version:
            raise VersionConflict(expected_version, actual)
        if state.state_version != expected_version + 1:
            raise ValueError("새 상태 버전은 expected + 1")
        d["state"], d["truth"] = copy.deepcopy(state), copy.deepcopy(truth)
        self._append(d, outbox)
        d["commands"][command.command_id] = command

    def append_events(self, game_id: str, outbox: list[EventEnvelope], dedup_key: str) -> bool:
        d = self.docs[game_id]
        if dedup_key in d["dedup"]:
            return False
        d["dedup"].add(dedup_key)
        self._append(d, outbox)
        return True

    def unpublished(self, game_id: str) -> list[EventEnvelope]:
        return list(self.docs[game_id]["outbox"])

    def events(self, game_id: str) -> list[EventEnvelope]:
        """아웃박스 전체를 seq 순으로(복기용 읽기 전용)."""
        return copy.deepcopy(sorted(self.docs[game_id]["outbox"], key=lambda e: e.seq))


class InMemoryProgressRepository:
    def __init__(self) -> None:
        self.items: dict[str, PlayerProgress] = {}
        self.applied: dict[str, set[str]] = {}

    def load(self, player_pseudonym: str) -> PlayerProgress:
        return copy.deepcopy(self.items.get(player_pseudonym, PlayerProgress(player_pseudonym)))

    def _get(self, pid: str) -> PlayerProgress:
        return self.items.setdefault(pid, PlayerProgress(pid))

    def _once(self, pid: str, source_event_id: str) -> bool:
        seen = self.applied.setdefault(pid, set())
        if source_event_id in seen:
            return False
        seen.add(source_event_id)
        return True

    def add_clue(self, player_pseudonym: str, clue_id: str, game_id: str, day: int, source_event_id: str) -> bool:
        if not self._once(player_pseudonym, source_event_id):
            return False
        return self._get(player_pseudonym).add_clue(clue_id, game_id, day)

    def unlock_ending(self, player_pseudonym: str, ending: str, source_event_id: str) -> bool:
        p = self._get(player_pseudonym)
        if not self._once(player_pseudonym, source_event_id) or ending in p.endings_unlocked:
            return False
        p.endings_unlocked.append(ending)
        return True

    def add_game(self, player_pseudonym: str, game_id: str) -> None:
        p = self._get(player_pseudonym)
        if game_id not in p.game_ids:
            p.game_ids.append(game_id)


class FixtureContentSource:
    def __init__(self, root: Path = FIXTURES / "content_min"):
        self.root = root

    def load(self, content_version: str) -> ContentBundle:
        data = json.loads((self.root / "bundle.json").read_text(encoding="utf-8"))
        bundle = serde.from_jsonable(ContentBundle, data)
        if content_version not in ("*", bundle.content_version):
            raise KeyError(f"콘텐츠 버전 없음: {content_version}")
        return bundle


class ListEventSink:
    def __init__(self) -> None:
        self.items: list[EventEnvelope] = []

    def append(self, envelopes: list[EventEnvelope]) -> None:
        seen = {e.event_id for e in self.items}
        for e in envelopes:
            if e.event_id not in seen:
                seen.add(e.event_id)
                self.items.append(e)


class FakeModelSelector:
    def __init__(self, canary_every: int = 0):
        self.canary_every = canary_every
        self.count = 0

    def select(self, game_id: str, player_pseudonym: Optional[str]) -> tuple[dict[str, str], bool]:
        self.count += 1
        canary = bool(self.canary_every) and self.count % self.canary_every == 0
        return {"suspicion": "fake-suspicion-1", "intent": "fake-intent-1"}, canary

    def suspicion_model(self, version: str) -> FakeSuspicionModel:
        return FakeSuspicionModel()

    def intent_parser(self, version: str) -> "FakeIntentParser":
        return FakeIntentParser()


# ---------------------------------------------------------------- 녹화 판 재생 엔진(B용)
class ScriptedGameEngine:
    """fixtures/api/recorded_game_base6.json을 재생한다. 기대한 명령 종류가 오면 다음 화면으로,
    아니면 ILLEGAL_PHASE로 거부한다. A의 실제 엔진으로 바꿔도 API 계층 코드는 그대로여야 한다."""

    def __init__(self, recording: Optional[Path] = None):
        path = recording or FIXTURES / "api" / "recorded_game_base6.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.steps = raw["steps"]
        self.views = [serde.from_jsonable(PlayerView, s["view"]) for s in self.steps]

    def _view(self, game_id: str, idx: int) -> PlayerView:
        return replace(copy.deepcopy(self.views[idx]), game_id=game_id)

    def _render_requests(self, game_id: str, idx: int) -> list[RenderRequest]:
        v = self.views[idx]
        out = []
        for s in v.public.speeches:
            if s.render_job_id in v.pending_render_jobs:
                out.append(RenderRequest(s.render_job_id, game_id, idx, s.speech_id, s.speaker,
                                         v.names.get(s.speaker, s.speaker), s.reaction, s.fact_sentence or "",
                                         list(v.names.values()), s.line_text or s.fact_sentence or ""))
        return out

    def _state(self, base: GameMeta, idx: int) -> GameState:
        v = self.views[idx]
        return GameState(meta=base, state_version=v.state_version, day=v.day, phase=v.phase,
                         active=list(v.active), locked=list(v.locked), missing=list(v.missing),
                         public=copy.deepcopy(v.public), player=copy.deepcopy(v.player),
                         result=v.result)

    def new_game(self, meta: GameMeta, deps: EngineDeps) -> TransitionResult:
        st = self._state(meta, 0)
        truth = GameTruth(meta.game_id, roles={})
        return TransitionResult(True, st, truth, [DomainEvent(EventType.GAME_STARTED, 1, st.phase,
                                                              {"preset_id": meta.preset.preset_id})])

    def apply(self, state: GameState, truth: GameTruth, env: CommandEnvelope,
              deps: EngineDeps) -> TransitionResult:
        if env.expected_state_version != state.state_version:
            return TransitionResult(False, None, None, [], reject=RejectCode.STATE_VERSION_CONFLICT)
        idx = state.state_version
        if idx + 1 >= len(self.steps):
            return TransitionResult(False, None, None, [], reject=RejectCode.GAME_ENDED)
        expected_kind = self.steps[idx + 1]["request"]["command"]["kind"]
        if env.command.kind.value != expected_kind:
            return TransitionResult(False, None, None, [], reject=RejectCode.ILLEGAL_PHASE,
                                    reject_detail=f"녹화 판은 {expected_kind}를 기대함")
        miss = missing_slots(env.command)
        if miss:
            return TransitionResult(False, None, None, [], reject=RejectCode.MISSING_SLOT,
                                    reject_detail=",".join(miss))
        nxt = self._state(state.meta, idx + 1)
        events = [DomainEvent(EventType(e["event_type"]), e["day"], Phase(e["phase"]), e["payload"])
                  for e in self.steps[idx + 1].get("events", [])]
        return TransitionResult(True, nxt, truth, events,
                                render_requests=self._render_requests(state.meta.game_id, idx + 1))

    def player_view(self, state: GameState, content: ContentBundle) -> PlayerView:
        return self._view(state.meta.game_id, state.state_version)

    def observe(self, state: GameState, truth: GameTruth, npc_id: str) -> ObservationSnapshot:
        raise NotImplementedError("녹화 판은 NPC 관측을 제공하지 않음(D는 fixtures/ml 사용)")


def seeded_rng(seed: int) -> random.Random:
    return random.Random(seed)
