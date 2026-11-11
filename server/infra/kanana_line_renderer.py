"""인프라: 캐묻기 답 실시간 생성 `LineRenderer` 구현(E-11-13, `npc_llm_design.md` 2-3절).

`submit`은 모드가 live일 때만 작업 저장소(`npc_line_jobs`, `kind="live"`, 우선순위 0)에 줄을 넣는다. 생성은 작업
프로세스(`npc_line_worker`)가 하고, 마감(`deadline_at`) 안에 끝난 결과만 저장된다(`finished_at ≤ deadline_at`).
`poll`은 저장소 문서만 읽는다. live가 아니거나 대기 줄이 이미 있으면 등록하지 않아 바로 템플릿(폴백)이 된다.
"""
from __future__ import annotations

import time
from typing import Callable

from engine.domain.serde import to_jsonable
from engine.ports import RenderRequest, RenderResult, RenderStatus
from llm.prompt import MODEL_FILE, PROMPT_VERSION
from server.app.npc_lines import LIVE, NpcLineStore, OpsSettings

LIVE_QUEUE_MAX = 1      # 대기 중인 실시간 줄이 이만큼 있으면 새 줄은 바로 템플릿(설계 D7)
QUANTIZATION = "Q4_K_M"


def job_key(game_id: str, job_id: str) -> str:
    """작업 문서 키. 엔진의 렌더 작업 번호(`job_001`…)는 판마다 새로 매기므로 판 ID로 묶는다."""
    return f"{game_id}:live:{job_id}"


class KananaLineRenderer:
    """`LineRenderer` 포트 구현. 생성은 작업 프로세스가 하고 이 객체는 저장소만 다룬다."""

    enforces_deadline = True     # 저장소가 `finished_at ≤ deadline_at`일 때만 완료로 저장한다
    scoped_by_game = True        # poll·cancel에 판 ID(`game_id=`)를 받는다

    def __init__(self, store: NpcLineStore, settings: OpsSettings, clock: Callable[[], float] = time.time):
        self.store, self.settings, self.clock = store, settings, clock

    def deadline_ms(self, req: RenderRequest) -> int:
        """화면 마감. live면 설정 값, 아니면 요청 값."""
        cfg = self.settings.current()
        return cfg.live_deadline_ms if cfg.live_on else req.deadline_ms

    def submit(self, req: RenderRequest) -> None:
        """live 모드이고 대기 줄이 비었을 때만 실시간 작업을 넣는다. 아니면 넣지 않아 바로 폴백된다."""
        cfg = self.settings.current()
        if not cfg.live_on or self.store.count_queued(LIVE, self.clock()) >= LIVE_QUEUE_MAX:
            return
        now = self.clock()
        self.store.insert_if_absent({
            "_id": job_key(req.game_id, req.job_id), "game_id": req.game_id, "speech_id": req.speech_id, "kind": LIVE,
            "priority": 0, "day": None, "phase": None, "status": "queued", "request": to_jsonable(req),
            "created_at": now, "deadline_at": now + cfg.live_deadline_ms / 1000, "attempts": 0,
            "claim_token": None, "lease_until": 0.0, "final": None, "text": None, "reason": None,
            "prompt_version": PROMPT_VERSION})

    def poll(self, job_id: str, game_id: str = "") -> RenderResult:
        """작업 문서 상태를 렌더 결과로 바꾼다. 마감 안에 저장된 결과만 done이다."""
        doc = self.store.get(job_key(game_id, job_id))
        meta = {"prompt_version": PROMPT_VERSION, "quantization": QUANTIZATION}
        if doc is None:
            return RenderResult(job_id, RenderStatus.FAILED, None, failure_reason="runtime_llm_disabled",
                                fallback=True, **meta)
        status = doc["status"]
        model = doc.get("model_version") or MODEL_FILE
        if status == "done" and doc.get("text"):
            return RenderResult(job_id, RenderStatus.DONE, doc["text"], latency_ms=doc.get("latency_ms"),
                                queue_ms=doc.get("queue_ms"), model_version=model, **meta)
        if status in ("queued", "running"):
            if self.clock() >= doc["deadline_at"]:
                return RenderResult(job_id, RenderStatus.TIMEOUT, None, failure_reason="deadline", fallback=True,
                                    model_version=model, **meta)
            return RenderResult(job_id, RenderStatus.QUEUED if status == "queued" else RenderStatus.RUNNING, None,
                                model_version=model, **meta)
        return RenderResult(job_id, RenderStatus.FAILED, None, failure_reason=doc.get("reason") or status,
                            latency_ms=doc.get("latency_ms"), fallback=True, model_version=model, **meta)

    def cancel(self, job_id: str, game_id: str = "") -> None:
        """열린 실시간 작업을 취소로 닫는다."""
        self.store.close(job_key(game_id, job_id), "cancelled", {"reason": "cancelled"})
