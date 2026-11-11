"""응용: NPC 대사 LLM(E-11-13, `npc_llm_design.md` 2·4절). 회의 발언 다듬기와 캐묻기 답 실시간 생성의 작업 흐름.

- 다듬기(polish): 화면 요청(회의 화면·선조회) 때 현재 날의 NPC 회의 발언 중 4종을 작업으로 등록한다(멱등, 저장 뒤
  등록이 빠져도 다음 화면 요청이 메운다). 선조회는 고정하지 않고, 대사창이 실제로 띄운 문장을 `shown`으로 한 번만
  고정한다(첫 확정이 이김). 날·단계가 바뀌면 이전 단계의 미고정 줄은 템플릿으로 고정한다.
- 실시간(live): `LineRenderer` 구현(`server/infra/kanana_line_renderer.py`)이 같은 작업 저장소에 줄을 넣고,
  작업 프로세스가 마감 안에 끝낸 결과만 인정한다.
- 작업 프로세스(`process_next`): 실시간 > 다듬기 순으로 하나 꺼내 HTTP 1회 생성 → 검증 → 조건부 저장.
  꺼내기 직전과 저장 직전에 모드를 다시 읽고, 꺼졌으면 템플릿으로 닫는다.

모드는 `off | polish | live`(live는 polish 포함). 설정 읽기는 `OpsSettings` 포트(인프라 `ops_settings.py`)가 한다.
생성 요청은 공개 원문·말투·종류만으로 만든다(도메인 `llm/npc_polish.py`). 본문은 로그·이벤트에 남기지 않는다.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from engine.domain.model import PLAYER_ID
from llm import npc_polish
from llm.validator import Lexicon, lexicon_from_bundle
from .review_narration import Narrator, NarratorError, NarratorTimeout

MODES = ("off", "polish", "live")
POLISH, LIVE = "polish", "live"
POLISH_DEADLINE_S = 300.0     # 다듬기 작업 유효 시간. 넘으면 꺼내지 않는다(이미 지난 줄)
POLISH_TIMEOUT_S = 10.0       # 다듬기 HTTP 1회 시간 제한
LEASE_S = 30.0


@dataclass(frozen=True)
class NpcLlmConfig:
    """현재 모드와 조정값. 코드 기본값은 off(설계 D4)."""
    mode: str = "off"
    live_deadline_ms: int = 3000
    polish_queue_max: int = 40

    @property
    def polish_on(self) -> bool:
        return self.mode in (POLISH, LIVE)

    @property
    def live_on(self) -> bool:
        return self.mode == LIVE


def resolve_mode(url: str, doc_mode: Optional[str], env_mode: str) -> str:
    """주소(상한 스위치)가 비면 off, 설정 문서 값이 있으면 그것, 없으면 env 값. 모르는 값은 off."""
    if not url:
        return "off"
    mode = doc_mode if doc_mode is not None else env_mode
    return mode if mode in MODES else "off"


class OpsSettings(Protocol):
    """운영 설정 읽기. `current`는 웹용 캐시 값, `fresh`는 작업 프로세스용 즉시 읽기."""

    def current(self) -> NpcLlmConfig: ...

    def fresh(self) -> NpcLlmConfig: ...


class NpcLineStore(Protocol):
    """작업 저장소(`server/infra/npc_line_jobs.py`)."""

    def insert_if_absent(self, doc: dict) -> bool: ...

    def get(self, job_id: str) -> Optional[dict]: ...

    def list_day(self, game_id: str, day: int, kind: str) -> list[dict]: ...

    def count_queued(self, kind: str, now: float) -> int: ...

    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]: ...

    def finish(self, job_id: str, token: str, now: float, fields: dict) -> bool: ...

    def close(self, job_id: str, status: str, fields: dict, token: Optional[str] = None) -> bool: ...

    def fix(self, job_id: str, final: dict) -> Optional[dict]: ...

    def fix_stale(self, game_id: str, day: int, phase: str, finals: dict) -> int: ...

    def delete(self, game_ids: list) -> int: ...


def polish_id(game_id: str, speech_id: str) -> str:
    return f"{game_id}:{speech_id}"


def lexicon_of(content) -> Lexicon:
    """콘텐츠 묶음 객체에서 검증 어휘를 만든다(인물 이름·별칭, 장소 이름)."""
    return lexicon_from_bundle({"places": [{"name_ko": p.name_ko} for p in content.places],
                                "characters": [{"name_ko": c.name_ko, "aliases": list(c.aliases)}
                                               for c in content.characters]})


@dataclass
class JobOutcome:
    """작업 하나의 처리 결과(로그용, 본문 없음)."""
    kind: str
    status: str
    queue_ms: int
    gen_ms: int
    reason: Optional[str] = None
    saved: bool = False


class NpcLineService:
    """NPC 대사 LLM 응용 서비스. `narrator`가 None이면 주소가 없는 것과 같아 모두 off다."""

    def __init__(self, game, store: NpcLineStore, narrator: Optional[Narrator], settings: OpsSettings,
                 clock: Callable[[], float] = time.time):
        self.game, self.store, self.narrator, self.settings, self.clock = game, store, narrator, settings, clock
        self._lexicons: dict[str, Lexicon] = {}

    # ---- 모드
    def config(self) -> NpcLlmConfig:
        if self.narrator is None:
            return NpcLlmConfig()
        return self.settings.current()

    def polish_on(self) -> bool:
        return self.config().polish_on

    def live_on(self) -> bool:
        return self.config().live_on

    def _lexicon(self, content) -> Lexicon:
        key = content.content_version
        if key not in self._lexicons:
            self._lexicons[key] = lexicon_of(content)
        return self._lexicons[key]

    # ---- 다듬기: 등록·정리(화면 요청 때)
    def prepare(self, game_id: str, player: str, display: Callable[[str], str] = lambda s: s) -> bool:
        """다듬기가 켜져 있으면 현재 날의 대상 발언을 등록하고 이전 단계 미고정 줄을 템플릿으로 고정한다. 켜짐 여부."""
        cfg = self.config()
        if not cfg.polish_on:
            return False
        state, _ = self.game.owned(game_id, player)
        content = self.game.dependencies(state.meta).content
        styles = {c.npc_id: c.speech_style for c in content.characters}
        lexicon = self._lexicon(content)
        phase = state.phase.value
        full = None
        now = self.clock()
        for sp in state.public.speeches:
            if sp.day != state.day or sp.speaker == PLAYER_ID or sp.reaction is not None:
                continue
            text = display(sp.line_text or "")
            if not npc_polish.eligible(sp.kind.value, text):
                continue
            job = polish_id(game_id, sp.speech_id)
            if full is None:
                full = self.store.count_queued(POLISH, now) >= cfg.polish_queue_max
            req = npc_polish.build_request(sp.speech_id, sp.kind.value, text, styles.get(sp.speaker, ""), lexicon)
            doc = {"_id": job, "game_id": game_id, "speech_id": sp.speech_id, "kind": POLISH, "priority": 1,
                   "day": sp.day, "phase": sp.phase.value, "status": "skipped" if full else "queued",
                   "request": req.as_dict(), "created_at": now, "deadline_at": now + POLISH_DEADLINE_S,
                   "content_version": state.meta.content_version, "attempts": 0, "claim_token": None, "lease_until": 0.0, "final": None, "text": None,
                   "reason": "queue_full" if full else None, "prompt_version": npc_polish.PROMPT_VERSION,
                   "validator_version": npc_polish.VALIDATOR_VERSION}
            if self.store.insert_if_absent(doc) and not full:
                full = None   # 하나 넣었으면 다음 줄에서 대기 수를 다시 센다
        self._fix_stale(game_id, state.day, phase)
        return True

    def _fix_stale(self, game_id: str, day: int, phase: str) -> None:
        stale = {d["_id"]: {"variant": npc_polish.TEMPLATE, "text": None, "at": self.clock(), "by": "phase"}
                 for d in self.store.list_day(game_id, day, POLISH) if d.get("final") is None and d["phase"] != phase}
        if stale:
            self.store.fix_stale(game_id, day, phase, stale)

    def lines(self, game_id: str, player: str, display: Callable[[str], str] = lambda s: s) -> dict:
        """선조회(고정하지 않음). 줄마다 상태·다듬은 문장·고정값. off면 빈 목록."""
        if not self.prepare(game_id, player, display):
            return {"mode": "off", "lines": []}
        state, _ = self.game.owned(game_id, player)
        out = []
        for d in self.store.list_day(game_id, state.day, POLISH):
            ready = d["status"] == "done" and bool(d.get("text"))
            final = d.get("final")
            out.append({"speech_id": d["speech_id"],
                        "status": "ready" if ready else ("pending" if d["status"] in ("queued", "running") else "template"),
                        "text": d["text"] if ready else None,
                        "final": {"variant": final["variant"],
                                  "text": final.get("text")} if final else None,
                        "job_rev": 1 if ready else 0})
        return {"mode": self.config().mode, "lines": out}

    def fixed_polished(self, game_id: str, day: int) -> dict:
        """그날 다듬은 문장으로 고정된 줄 {발언 ID: 문장}. 재접속 때 화면이 첫 줄부터 고정값을 쓰게 한다."""
        return {d["speech_id"]: d["final"]["text"] for d in self.store.list_day(game_id, day, POLISH)
                if d.get("final") and d["final"].get("variant") == npc_polish.POLISHED and d["final"].get("text")}

    def shown(self, game_id: str, player: str, speech_id: str, variant: str, job_rev: int) -> dict:
        """대사창이 띄운 변형을 첫 확정 1회로 고정한다. 이미 고정됐으면 고정값을 돌려준다."""
        if variant not in (npc_polish.POLISHED, npc_polish.TEMPLATE):
            raise ValueError("invalid_variant")
        self.game.owned(game_id, player)
        job = polish_id(game_id, speech_id)
        doc = self.store.get(job)
        if doc is None:
            return {"speech_id": speech_id, "variant": npc_polish.TEMPLATE, "text": None}
        if doc.get("final") is None:
            ready = doc["text"] if doc["status"] == "done" and job_rev >= 1 else None
            chosen = npc_polish.fix_variant(variant, self.config().polish_on, ready)
            doc = self.store.fix(job, {"variant": chosen, "text": ready if chosen == npc_polish.POLISHED else None,
                                       "at": self.clock(), "by": "shown"}) or doc
            if doc["status"] in ("queued", "running"):
                self.store.close(job, "closed", {"reason": "fixed_before_done"})
        final = doc["final"]
        return {"speech_id": speech_id, "variant": final["variant"], "text": final.get("text")}

    def delete(self, game_ids: list) -> int:
        return self.store.delete(list(game_ids))

    # ---- 작업 프로세스
    def process_next(self) -> Optional[JobOutcome]:
        """대기 작업 하나를 처리한다. 없으면 None."""
        if self.narrator is None:
            return None
        cfg = self.settings.fresh()
        now = self.clock()
        token = secrets.token_hex(8)
        doc = self.store.claim_next(now, now + LEASE_S, token)
        if doc is None:
            return None
        kind = doc["kind"]
        queue_ms = int((now - doc["created_at"]) * 1000)
        if not (cfg.live_on if kind == LIVE else cfg.polish_on):
            self.store.close(doc["_id"], "closed", {"reason": "mode_off"}, token)
            return JobOutcome(kind, "closed", queue_ms, 0, "mode_off")
        timeout = max(0.1, doc["deadline_at"] - now) if kind == LIVE else POLISH_TIMEOUT_S
        body, check = self._job_parts(doc)
        start = self.clock()
        try:
            reply = self.narrator.complete(body, timeout)
        except NarratorTimeout:
            self.store.close(doc["_id"], "failed", {"reason": "timeout"}, token)
            return JobOutcome(kind, "failed", queue_ms, int((self.clock() - start) * 1000), "timeout")
        except NarratorError as exc:
            self.store.close(doc["_id"], "failed", {"reason": str(exc) or "error"}, token)
            return JobOutcome(kind, "failed", queue_ms, int((self.clock() - start) * 1000), "error")
        gen_ms = int((self.clock() - start) * 1000)
        text, result = check(reply.text or "")
        if not result.ok:
            self.store.close(doc["_id"], "rejected", {"reason": result.reason, "reasons": list(result.reasons),
                                                     "latency_ms": gen_ms, "model_version": reply.model_version}, token)
            return JobOutcome(kind, "rejected", queue_ms, gen_ms, result.reason)
        again = self.settings.fresh()
        if not (again.live_on if kind == LIVE else again.polish_on):
            self.store.close(doc["_id"], "closed", {"reason": "mode_off"}, token)
            return JobOutcome(kind, "closed", queue_ms, gen_ms, "mode_off")
        saved = self.store.finish(doc["_id"], token, self.clock(),
                                  {"text": text, "latency_ms": gen_ms, "queue_ms": queue_ms,
                                   "model_version": reply.model_version, "tokens_out": reply.tokens_out})
        if not saved:
            self.store.close(doc["_id"], "discarded", {"reason": "late"}, token)
        return JobOutcome(kind, "done" if saved else "discarded", queue_ms, gen_ms, None if saved else "late", saved)

    def _job_parts(self, doc: dict):
        """작업 종류별 요청 본문과 검사 함수. 어휘는 요청에 담긴 허용 이름과 판 콘텐츠에서 얻는다."""
        lexicon = self._job_lexicon(doc)
        if doc["kind"] == LIVE:
            from llm.prompt import GEN_SETTINGS, STOP, build_messages
            from llm.validator import normalize_output, validate
            req = doc["request"]
            body = {"messages": build_messages(req), "temperature": GEN_SETTINGS["temperature"],
                    "top_p": GEN_SETTINGS["top_p"], "top_k": GEN_SETTINGS["top_k"],
                    "max_tokens": GEN_SETTINGS["max_tokens"], "stop": STOP, "cache_prompt": True}

            def check(raw):
                text = normalize_output(raw)
                return text, validate(text, req, lexicon)
            return body, check
        req = npc_polish.PolishRequest(doc["request"]["speech_id"], doc["request"]["kind"], doc["request"]["text"],
                                       doc["request"]["persona"], tuple(doc["request"]["allowed_names"]))
        return npc_polish.request_body(req), lambda raw: npc_polish.check(raw, req, lexicon)

    def _job_lexicon(self, doc: dict) -> Lexicon:
        version = doc.get("content_version")
        if version in self._lexicons:
            return self._lexicons[version]
        try:
            state, _ = self.game.repository.load(doc["game_id"])
            content = self.game.dependencies(state.meta).content
            return self._lexicon(content)
        except (KeyError, AttributeError):
            return Lexicon(places={}, names={})
