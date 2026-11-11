"""응용: 복기 해설 LLM 요약 작업의 등록·조회·처리 흐름(E-10-01, 설계 `review_llm_design.md` 5장).

- 입력 조립(`narration_input`): 복기 DTO·콘텐츠·공개 기록에서 3.2 허용 필드만 뽑아 작업 문서에 담는다.
  아웃박스 봉투(`EventEnvelope`)·가명·세션·명령 ID는 넣지 않는다.
- 등록(`ensure`): 판당 문서 1개, 멱등. 기능이 꺼져 있으면(생성기 없음) 등록하지 않는다. 대기가 10건을 넘으면
  등록하되 바로 `timeout`으로 닫는다.
- 조회(`status`): 마감이 지나면 문서가 아직 대기·처리 중이어도 템플릿으로 확정해 보여 준다.
- 처리(`process_next`): 작업 프로세스가 부른다. FIFO로 하나를 임대해 생성기를 잠금 밖에서 부르고, 도메인 검사 뒤
  임대 토큰·마감 조건부로 저장한다. 늦게 온 결과는 버린다.

규칙(항목 선정·검사·템플릿 대체)은 도메인 `llm.review_narration`, 저장·HTTP는 인프라 포트 뒤에 있다.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, Protocol

from engine.domain.model import ClaimType, EvidenceType, Polarity, Role
from engine.domain.review import representative_code
from llm.review_narration import (PROMPT_VERSION, SOURCE_LLM, Lexicon, NarrationItem, NarrationResult, NpcBlock,
                                  all_template, merge, narrate, object_josa, request_body, select_items)
from llm.validator import lexicon_from_bundle

from .dto import NarrationNpc, NarrationSentence, ReviewNarrationOutput

QUEUED, RUNNING, DONE, FAILED, TIMEOUT = "queued", "running", "done", "failed", "timeout"
FINAL = frozenset((DONE, FAILED, TIMEOUT))
SLOT_KO = {"evening": "초저녁", "midnight": "한밤"}
RESULT_KO = {
    "all_possessed_locked": "마을 승리, 창귀에 씐 사람을 모두 가뒀다",
    "mudang_locked": "창귀 승리, 무당이 갇혔다",
    "possessed_parity": "창귀 승리, 창귀 편이 마을 사람 수를 따라잡았다",
}


# ---------------------------------------------------------------- 포트
class NarratorError(Exception):
    """생성기 연결 실패(재시도 대상)."""


class NarratorTimeout(Exception):
    """생성기 응답이 제한 시간 안에 오지 않음."""


@dataclass
class NarratorReply:
    """생성 서버 응답 한 건(본문, 끝난 이유, 토큰 수, 모델 버전)."""
    text: str
    finish_reason: Optional[str]
    tokens_in: int = 0
    tokens_out: int = 0
    model_version: str = ""


class Narrator(Protocol):
    """생성 서버 포트. 인프라 KananaNarrator가 구현한다."""
    def complete(self, body: dict, timeout: float) -> NarratorReply:
        """요청 본문을 보내고 응답을 돌려준다. 닿지 않으면 NarratorError, 시간 초과면 NarratorTimeout."""
        ...


class ReviewJobStore(Protocol):
    """`review_jobs` 저장소. 짧은 원자 연산만 둔다(인프라가 구현)."""

    def insert_if_absent(self, doc: dict) -> bool:
        """같은 판 작업이 없을 때만 넣고 넣었는지 돌려준다(멱등 등록)."""
        ...
    def get(self, game_id: str) -> Optional[dict]:
        """판 ID로 작업 문서를 읽는다."""
        ...
    def count_queued(self) -> int:
        """대기 중 작업 수."""
        ...
    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]:
        """가장 오래된 대기 작업이나 임대가 끝난 실행 작업을 토큰과 함께 잡는다."""
        ...
    def finish(self, game_id: str, token: str, now: float, fields: dict) -> bool:
        """토큰·실행 중·마감 전일 때만 완료로 바꾼다(늦은 결과 버림)."""
        ...
    def close(self, game_id: str, status: str, fields: dict) -> bool:
        """열린 작업을 실패·시간 초과로 닫는다."""
        ...


# ---------------------------------------------------------------- 입력 조립(3.2)
def _claim_sentence(claim, names: dict[str, str], places: dict[str, str]) -> str:
    who = names.get(claim.speaker, claim.speaker)
    when = f"{claim.night}일째 밤" if claim.night > 0 else "판 시작 전날 밤"
    if claim.slot is not None:
        when += " " + SLOT_KO.get(claim.slot.value, claim.slot.value)
    place = places.get(claim.place.value, claim.place.value) if claim.place is not None else ""
    deny = claim.polarity is Polarity.DENY
    if claim.claim_type is ClaimType.WHEREABOUTS:
        body = f"{when}에 {place}에 {'없었다' if deny else '있었다'}"
    elif claim.claim_type is ClaimType.SIGHTING:
        seen = ", ".join(names.get(s, s) for s in claim.seen) or "누군가"
        body = f"{when}에 {place}에서 {seen}{object_josa(seen)} {'못 봤다' if deny else '봤다'}"
    elif claim.claim_type is ClaimType.NON_SIGHTING:
        seen = ", ".join(names.get(s, s) for s in claim.seen)
        body = f"{when}에 {place}에서 {seen + object_josa(seen) if seen else '아무도'} 못 봤다"
    else:
        result = {"clear": "맑음", "murky": "탁함"}.get(getattr(claim.divination_result, "value", ""), "")
        return f"무당의 점괘 공개: {names.get(claim.subject, claim.subject)}의 점괘는 {result}"
    return f"{who}의 말: {body}"


def _evidence_texts(refs, claims: dict, names: dict[str, str], places: dict[str, str]) -> tuple[str, ...]:
    out: list[str] = []
    for ref in refs:
        ids = ref.record_id.split("|") if ref.type is EvidenceType.CONTRADICTION else [ref.record_id]
        found = [claims[i] for i in ids if i in claims]
        if not found:
            continue
        texts = [_claim_sentence(c, names, places) for c in found]
        if ref.type is EvidenceType.CONTRADICTION:
            out.append("어긋난 두 진술: " + " / ".join(texts))
        elif ref.type is EvidenceType.TELL:
            out.append(f"{names.get(found[0].speaker, found[0].speaker)}의 기색이 흔들림: {texts[0]}")
        else:
            out.append(texts[0])
    return tuple(dict.fromkeys(out))[:2]


def _bundle_dict(content) -> dict:
    return {"places": [{"name_ko": p.name_ko} for p in content.places],
            "characters": [{"name_ko": c.name_ko, "aliases": list(c.aliases)} for c in content.characters]}


def narration_input(output, content, public, seed: int) -> dict:
    """복기 DTO·콘텐츠·공개 기록 → 작업 문서에 담을 생성 입력(직렬화 가능한 dict)."""
    names = {c.npc_id: c.name_ko for c in content.characters}
    names["mudang"] = "무당"
    places = {p.place.value: p.name_ko for p in content.places}
    temperaments = {t.temperament_id: t.name_ko for t in content.temperaments}
    temper_of = {c.npc_id: temperaments.get(c.temperament_id, "") for c in content.characters}
    claims = {c.claim_id: c for c in (public.claims if public is not None else [])}
    items = []
    for row in output.timeline:
        if row.actor not in names:
            continue
        items.append(NarrationItem(
            row.item_id, row.day, row.phase, row.actor, row.kind, representative_code(row.reason_codes),
            target=names.get(row.target_npc) if row.target_npc else None,
            victim=names.get(row.victim) if row.victim else None,
            executor=names.get(row.executor) if row.executor else None,
            reaction=row.reaction, evidence=_evidence_texts(row.evidence, claims, names, places), template=row.text))
    possessed = {npc for npc, role in output.roles.items() if role is Role.POSSESSED}
    picked = select_items(items, possessed)
    blocks = [NpcBlock(npc, names[npc], "창귀에 씐 사람" if npc in possessed else "마을 사람",
                       temper_of.get(npc, ""), picked[npc]) for npc in sorted(picked, key=lambda n: _order(picked[n]))]
    reason = getattr(output.result.reason, "value", str(output.result.reason))
    return {"seed": int(seed) % (2 ** 31), "result_text": RESULT_KO.get(reason, reason),
            "blocks": [asdict(b) for b in blocks], "lexicon": lexicon_from_bundle(_bundle_dict(content)).to_json()}


def _order(items: list[NarrationItem]) -> tuple[int, int]:
    first = items[0]
    return (first.day, int(first.item_id.lstrip("d") or 0))


def blocks_from(payload: dict) -> list[NpcBlock]:
    """작업 문서의 입력 → 도메인 블록."""
    out = []
    for b in payload["blocks"]:
        items = [NarrationItem(**{**i, "evidence": tuple(i["evidence"])}) for i in b["items"]]
        out.append(NpcBlock(b["npc_id"], b["name"], b["role"], b.get("temperament", ""), items))
    return out


def _npcs_json(result: NarrationResult) -> list[dict]:
    return [{"npc": n.npc_id, "source": n.source, "sentences": [{"cite": c, "text": t} for c, t in n.sentences]}
            for n in result.npcs]


# ---------------------------------------------------------------- 서비스
@dataclass
class NarrationSettings:
    """작업 마감·임대·요청 제한·시작 최소 여유·대기 상한·시도 횟수·재시도 간격(초·건)."""
    deadline_s: float = 180.0
    lease_s: float = 120.0
    request_cap_s: float = 90.0
    min_start_s: float = 30.0
    max_queued: int = 10
    max_attempts: int = 2
    retry_wait_s: float = 5.0


@dataclass
class ProcessOutcome:
    """작업 하나 처리 결과(측정·지표용 집계 값만)."""
    game_id: str
    status: str
    queue_ms: int = 0
    gen_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    rejects: dict[str, int] = field(default_factory=dict)
    llm_npcs: int = 0
    template_npcs: int = 0
    saved: bool = True


class ReviewNarrationService:
    """해설 작업 등록·조회·처리. `narrator`가 없으면 기능 꺼짐(등록하지 않고 조회는 `off`)."""

    def __init__(self, store: ReviewJobStore, narrator: Optional[Narrator], *,
                 settings: Optional[NarrationSettings] = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, game_exists: Callable[[str], bool] = lambda _gid: True):
        self.store = store
        self.narrator = narrator
        self.settings = settings or NarrationSettings()
        self.clock = clock
        self.sleep = sleep
        self.game_exists = game_exists

    @property
    def enabled(self) -> bool:
        """생성기가 연결돼 있으면 기능 켜짐."""
        return self.narrator is not None

    # ------------------------------------------------------------ 등록
    def ensure(self, output, content, public, seed: int) -> None:
        """종료된 판의 해설 작업을 멱등 등록한다. 중단 판·꺼짐·이미 있음이면 아무것도 하지 않는다."""
        if not self.enabled or not getattr(output.result, "completed", True):
            return
        if self.store.get(output.game_id) is not None:
            return
        payload = narration_input(output, content, public, seed)
        now = self.clock()
        doc = {"_id": output.game_id, "status": QUEUED, "created_at": now,
               "deadline_at": now + self.settings.deadline_s, "attempts": 0, "claim_token": None,
               "lease_until": 0.0, "prompt_version": PROMPT_VERSION, "payload": payload}
        if not payload["blocks"]:
            doc.update(status=DONE, npcs=[])            # 요약할 NPC가 없는 판
        elif self.store.count_queued() >= self.settings.max_queued:
            doc.update(status=TIMEOUT, close_reason="overload")
        self.store.insert_if_absent(doc)

    # ------------------------------------------------------------ 조회
    def status(self, game_id: str) -> ReviewNarrationOutput:
        """화면 폴링용 조회. 마감이 지나면 템플릿으로 확정한다."""
        if not self.enabled:
            return ReviewNarrationOutput("off", True)
        doc = self.store.get(game_id)
        if doc is None:
            return ReviewNarrationOutput("off", True)
        status = doc["status"]
        if status not in FINAL and self.clock() >= doc["deadline_at"]:
            status = TIMEOUT
        if status not in FINAL:
            return ReviewNarrationOutput(status, False)
        rows = doc.get("npcs") if status == DONE else None
        if rows is None:
            rows = _npcs_json(all_template(blocks_from(doc["payload"])))
        return ReviewNarrationOutput(status, True, [
            NarrationNpc(r["npc"], [NarrationSentence(s["cite"], s["text"]) for s in r["sentences"]], r["source"])
            for r in rows])

    # ------------------------------------------------------------ 처리(작업 프로세스)
    def process_next(self) -> Optional[ProcessOutcome]:
        """대기 작업 하나를 FIFO로 처리한다. 할 일이 없으면 None."""
        st = self.settings
        now = self.clock()
        token = secrets.token_hex(8)
        doc = self.store.claim_next(now, now + st.lease_s, token)
        if doc is None:
            return None
        game_id = doc["_id"]
        queue_ms = int((now - doc["created_at"]) * 1000)
        if doc["attempts"] > st.max_attempts:
            self.store.close(game_id, FAILED, {"close_reason": "attempts"})
            return ProcessOutcome(game_id, FAILED, queue_ms)
        remaining = doc["deadline_at"] - now
        if remaining < st.min_start_s:
            self.store.close(game_id, TIMEOUT, {"close_reason": "late_start", "queue_ms": queue_ms})
            return ProcessOutcome(game_id, TIMEOUT, queue_ms)
        blocks = blocks_from(doc["payload"])
        payload = doc["payload"]
        lexicon = Lexicon.from_json(payload["lexicon"])
        limit = min(doc["deadline_at"], now + st.lease_s)       # 임대·마감을 넘겨 처리하지 않는다
        started = self.clock()
        results, tokens_in, tokens_out, model = [], 0, 0, ""
        for i, block in enumerate(blocks):
            # NPC마다 요청 1회(11/11 실측: 판 전체 한 번 요청은 3B 모델이 항목을 밀려 쓰고 출력 토큰이 제한을 넘음)
            body = request_body([block], payload["result_text"], payload["seed"])
            try:
                reply = self._call(body, limit)
            except NarratorTimeout:
                rest = all_template(blocks[i:])
                rest.rejects[TIMEOUT] = len(blocks) - i
                results.append(rest)
                break
            except NarratorError:
                self.store.close(game_id, FAILED, {"close_reason": "llm_unavailable", "queue_ms": queue_ms})
                return ProcessOutcome(game_id, FAILED, queue_ms, int((self.clock() - started) * 1000))
            tokens_in += reply.tokens_in
            tokens_out += reply.tokens_out
            model = reply.model_version or model
            results.append(narrate([block], reply.text, reply.finish_reason, lexicon))
        gen_ms = int((self.clock() - started) * 1000)
        result = merge(results)
        llm_n = sum(1 for n in result.npcs if n.source == SOURCE_LLM)
        outcome = ProcessOutcome(game_id, DONE, queue_ms, gen_ms, tokens_in, tokens_out,
                                 dict(result.rejects), llm_n, len(result.npcs) - llm_n)
        fields = {"npcs": _npcs_json(result), "fail_reasons": dict(result.rejects), "queue_ms": queue_ms,
                  "gen_ms": gen_ms, "tokens_in": tokens_in, "tokens_out": tokens_out, "model_version": model}
        # 삭제된 판에는 저장하지 않는다. 늦었거나 임대를 잃었으면 버린다(최종 고정).
        outcome.saved = self.game_exists(game_id) and self.store.finish(game_id, token, self.clock(), fields)
        return outcome

    def _call(self, body: dict, deadline_at: float) -> NarratorReply:
        """연결 오류는 남은 시간 안에서 1회 재시도한다(5.5)."""
        st = self.settings
        for attempt in (1, 2):
            left = deadline_at - self.clock()
            if left <= 1:
                raise NarratorTimeout
            try:
                return self.narrator.complete(body, min(st.request_cap_s, left))
            except NarratorError:
                if attempt == 2 or deadline_at - self.clock() < st.retry_wait_s + st.min_start_s:
                    raise
                self.sleep(st.retry_wait_s)
        raise NarratorError
