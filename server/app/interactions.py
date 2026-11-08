"""자유 입력·수첩·설문·렌더의 B 응용. 저장 구현은 조립 시 주입한다."""
import copy
import hashlib
import json
import re
import secrets
import uuid
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import ContextManager, Protocol

from engine.domain.events import DomainEvent, EventType
from engine.domain.model import CommandKind, PLAYER_ID
from engine.domain.serde import to_jsonable
from engine.ports import ParseContext, ParseFailure, RenderStatus
from .dto import (CommandOutput, ParseUtteranceInput, ParseUtteranceOutput, RenderOutput, ResolveUtteranceInput,
                  SubmitCommandInput, UtteranceReaction)
from .usecases import ResourceNotFound


class InteractionUnavailable(Exception):
    """시도·렌더·수첩 저장 또는 외부 포트가 연결되지 않았다."""


class InteractionConflict(Exception):
    """닫힌 시도나 이미 제출한 설문은 다른 값으로 덮어쓸 수 없다."""


@dataclass
class InteractionState:
    """판별 보조 기록. 게임 상태와 별도이며 계정 식별 정보는 넣지 않는다."""
    attempts: dict = field(default_factory=dict)
    renders: dict = field(default_factory=dict)
    notebook: dict = field(default_factory=dict)
    survey: dict | None = None


class InteractionStore(Protocol):
    """판별 직렬화·성공 시 보존을 제공할 B 보조 저장 경계(C 인수 대상)."""

    def edit(self, game_id: str) -> ContextManager[InteractionState]:
        """같은 판의 호출을 직렬화하고 예외 시 보조 변경을 되돌린다."""
        ...

    def edit_effects(self, game_id: str) -> ContextManager[dict]:
        """명령 후속 처리 계획을 별도 보존한다. edit의 롤백에 포함하지 않는다.

        호출 사이에 보존하며 성공 시 반영·예외 시 해당 변경만 되돌린다.
        판 삭제 시 함께 지운다. 실제 저장 구현은 C 인수 대상이다.
        """
        ...


def mask_text(text):
    """privacy.md의 이메일·전화·주민번호·긴 숫자를 저장 전에 가린다."""
    patterns = (r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", r"\d{13,}", r"\d{6}[-\s]?[1-8]\d{6}",
                r"(?<!\d)(?:\+82[- .]?)?0?\d{1,2}[- .]?\d{3,4}[- .]?\d{4}(?!\d)")
    for pattern in patterns:
        text = re.sub(pattern, "<MASK>", text)
    return text


class InteractionService:
    """공통 DTO와 append_events로 상태 버전을 소비하지 않는 API를 처리한다."""

    def __init__(self, game, store, parser=None, renderer=None):
        self.game, self.store, self.parser, self.renderer = game, store, parser, renderer

    def _event(self, state, kind, payload, key):
        event = DomainEvent(kind, state.day, state.phase, payload)
        self.game.repository.append_events(state.meta.game_id, self.game._events(state, [event]), key)

    def _restore_participant(self, game_id, name, records):
        with self.store.edit_effects(game_id) as effects:
            plan = copy.deepcopy(effects.get('participant:' + name))
        if plan is None or getattr(records, name) == plan['value']:
            return
        try:
            self.game.repository.append_events(game_id, plan['events'], plan['key'])
        except (ConnectionError, TimeoutError):
            raise InteractionUnavailable('participant_append_unavailable') from None
        setattr(records, name, copy.deepcopy(plan['value']))

    @contextmanager
    def participant_records(self, game_id):
        """판별 잠금 안에서 수첩·설문의 미완료 저장을 동일 이벤트 키로 복구한다."""
        with self.store.edit(game_id) as records:
            for name in ('notebook', 'survey'):
                self._restore_participant(game_id, name, records)
            yield records

    def _save_participant(self, state, name, kind, payload, value, records):
        # 보조 상태의 롤백과 독립된 계획을 먼저 저장한다. 성공한 계획도 마지막 값 복구용으로 유지한다.
        event = DomainEvent(kind, state.day, state.phase, payload)
        with self.store.edit_effects(state.meta.game_id) as effects:
            effects['participant:' + name] = dict(value=copy.deepcopy(value),
                events=self.game._events(state, [event]), key=name + ':' + str(uuid.uuid4()))
        self._restore_participant(state.meta.game_id, name, records)

    def prepare_command_effects(self, command_id, response_json, previous, current, requests):
        """게임 commit 전에 복구 재료만 보존한다. 렌더 실행은 저장 확인 뒤다."""
        key = hashlib.sha256(response_json.encode("utf-8")).hexdigest()
        with self.store.edit_effects(current.meta.game_id) as plans:
            plans.setdefault(command_id, {}).setdefault(key, {
                "previous": (previous.day, previous.phase, previous.state_version),
                "current": (current.day, current.phase, current.state_version),
                "requests": copy.deepcopy(requests),
            })

    def complete_command_effects(self, game_id, saved, player):
        """실제 저장 응답에 대응하는 계획만 복구한다. 엔진은 다시 호출하지 않는다."""
        key = hashlib.sha256(saved.response_json.encode("utf-8")).hexdigest()
        with self.store.edit_effects(game_id) as plans:
            plan = copy.deepcopy(plans.get(saved.command_id, {}).get(key))
        if plan is None:
            return
        current, _ = self.game.owned(game_id, player)
        def context(point):
            day, phase, version = point
            return replace(current, day=day, phase=phase, state_version=version)
        previous, accepted = context(plan["previous"]), context(plan["current"])
        self.cancel_previous(previous, accepted)
        self.register_renders(accepted, plan["requests"])
        latest, _ = self.game.owned(game_id, player)
        self.cancel_previous(accepted, latest)
        # 카드 반응 등 바깥 edit가 나중에 롤백돼도 다음 재전송에서 복구할 수
        # 있도록 계획을 유지한다. 등록·취소는 작업 ID로 중복 처리하지 않는다.

    def parse(self, data, player):
        """공개 맥락만 해석기에 전달하고 마스킹한 시도를 보존한다."""
        state, _ = self.game.owned(data.game_id, player)
        if state.state_version != data.expected_state_version:
            return self.game._conflict(state)
        if self.parser is None:
            raise InteractionUnavailable
        if len(data.text) > 4000 or data.expected_state_version < 0:
            raise ValueError("invalid_utterance")
        view = self.game.detail(data.game_id, player).view
        if any(npc is not None and npc not in view.active for npc in (data.selected_npc, data.last_addressee)):
            raise ValueError("unknown_npc")
        content = self.game.dependencies(state.meta).content
        aliases = {c.npc_id: [c.name_ko, *c.aliases] for c in content.characters if c.npc_id in view.active}
        last_addressee = data.last_addressee or next((
            speech.addressee if speech.speaker == PLAYER_ID else speech.speaker
            for speech in reversed(view.public.speeches) if speech.kind is CommandKind.INTERROGATE
            and ((speech.speaker == PLAYER_ID and speech.addressee in aliases)
                 or (speech.addressee == PLAYER_ID and speech.speaker in aliases))), None)
        ctx = ParseContext(data.game_id, state.state_version, state.phase, copy.deepcopy(view.public), aliases,
                           data.selected_npc, data.selected_cards, last_addressee)
        result = self.parser.parse(data.text, ctx)
        output = ParseUtteranceOutput(str(uuid.uuid4()), state.state_version, result.candidates,
                                      result.failure, True)
        payload = {"attempt_id": output.attempt_id, "superseded_by": None, "text": mask_text(data.text),
                   "mask_version": "b-mask-1", "state_version": state.state_version, "phase": state.phase.value,
                   "candidates": to_jsonable(result.candidates), "failure": to_jsonable(result.failure),
                   "versions": result.versions, "reaction": None, "final_command": None, "label_source": None}
        with self.store.edit(data.game_id) as records:
            records.attempts[output.attempt_id] = {"output": output, "payload": payload, "closed": False,
                                                  "selection": {"selected_npc": data.selected_npc,
                                                      "selected_cards": copy.deepcopy(data.selected_cards),
                                                      "last_addressee": last_addressee},
                                                  "signature_salt": secrets.token_hex(32)}
            self._event(state, EventType.INTENT_ATTEMPT, payload, "intent:" + output.attempt_id)
        return output

    def resolve(self, data, player):
        """소유권·시도·재전송·버전을 검사하고 confirm/edit만 명령으로 전달한다."""
        state, _ = self.game.owned(data.game_id, player)
        with self.store.edit(data.game_id) as records:
            attempt = records.attempts.get(data.attempt_id)
            if attempt is None:
                raise ResourceNotFound
            reaction = data.reaction
            if not attempt["closed"] and reaction == UtteranceReaction.RETRY and not data.retry_text.strip():
                # 빈 재입력은 화면을 유지하므로 현재 시도를 닫거나 새 시도를 만들지 않는다.
                return ParseUtteranceOutput(data.attempt_id, state.state_version, [], ParseFailure.EMPTY_INPUT, True)
            saved = self.game.repository.find_command(data.game_id, data.command_id) if data.command_id and reaction in (
                UtteranceReaction.CONFIRM, UtteranceReaction.EDIT) else None
            if attempt["closed"] and saved is not None:
                # 같은 명령은 경로가 edit/confirm으로 달라져도 원래 응답을 반환한다.
                # 기존 반응 라벨·시도 기록은 덮어쓰지 않는다.
                return self.game.replay_command(data.game_id, saved, player)
            # 재전송 비교에 원문을 보관하지 않는다. 마스킹 후 같은 문장이 되는 서로 다른
            # 입력도 구분하도록 원문은 임시 직렬화에만 사용하고 시도별 salt와 요약값만 저장한다.
            serialized = json.dumps(to_jsonable(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            signature = hashlib.sha256((attempt["signature_salt"] + serialized).encode("utf-8")).hexdigest()
            if attempt["closed"]:
                if attempt.get("signature") != signature:
                    raise InteractionConflict
                response = attempt["response"]
                return replace(response, replayed=True) if isinstance(response, CommandOutput) and response.accepted else response
            original = attempt["output"]
            # 잠금을 기다리는 동안 판이 진행됐을 수 있으므로 새 해석에는 현재 버전을 쓴다.
            state, _ = self.game.owned(data.game_id, player)
            command = None
            if reaction in (UtteranceReaction.CONFIRM, UtteranceReaction.EDIT):
                if original.state_version != state.state_version and saved is None:
                    return self.game._conflict(state)
                if data.expected_state_version != original.state_version and saved is None:
                    return self.game._conflict(state)
                if reaction == UtteranceReaction.CONFIRM:
                    if data.chosen_index >= len(original.cards):
                        raise ValueError("unknown_card")
                    candidate = original.cards[data.chosen_index]
                    if candidate.missing_slots or candidate.evidence_alternatives:
                        raise ValueError("missing_slots")
                    command = candidate.command
                else:
                    command = data.edited_command
                response = self.game.command(SubmitCommandInput(data.game_id, data.command_id,
                    data.expected_state_version, command, data.attempt_id), player)
                if not response.accepted:
                    return response
            elif reaction == UtteranceReaction.RETRY:
                selection = attempt.get("selection", {})
                selected_npc = selection.get("selected_npc")
                last_addressee = selection.get("last_addressee")
                response = self.parse(ParseUtteranceInput(data.game_id, state.state_version, data.retry_text,
                    selected_npc=selected_npc if selected_npc in state.active else None,
                    selected_cards=copy.deepcopy(selection.get("selected_cards", [])),
                    last_addressee=last_addressee if last_addressee in state.active else None), player)
                if isinstance(response, CommandOutput):
                    # 재조회와 해석 사이의 추가 경합은 409로 돌려주고 원래 시도는 열어 둔다.
                    return response
            else:
                response = ParseUtteranceOutput(data.attempt_id, state.state_version, [], None, False)
            payload = dict(attempt["payload"], reaction=reaction.value, final_command=to_jsonable(command),
                           label_source="player_edited" if reaction == UtteranceReaction.EDIT else
                           "player_confirmed" if reaction == UtteranceReaction.CONFIRM else None,
                           superseded_by=response.attempt_id if reaction == UtteranceReaction.RETRY else None)
            self._event(state, EventType.INTENT_ATTEMPT, payload, f"intent:{data.attempt_id}:reaction")
            attempt.update(closed=True, signature=signature, response=response, payload=payload)
            return response

    def submit_linked(self, data, player):
        """commands 경로의 intent_attempt_id도 같은 카드 수명주기로 검증한다."""
        self.game.owned(data.game_id, player)
        with self.store.edit(data.game_id) as records:
            attempt = records.attempts.get(data.intent_attempt_id)
            if attempt is None:
                raise ResourceNotFound
            index = next((i for i, card in enumerate(attempt["output"].cards) if card.command == data.command), None)
            resolved = ResolveUtteranceInput(data.game_id, data.intent_attempt_id,
                UtteranceReaction.CONFIRM if index is not None else UtteranceReaction.EDIT,
                command_id=data.command_id, expected_state_version=data.expected_state_version,
                chosen_index=index, edited_command=None if index is not None else data.command)
            return self.resolve(resolved, player)

    def notebook(self, data, player):
        """같은 날짜·단계의 동일 저장만 생략하고 버전 소비 없이 기록한다."""
        state, _ = self.game.owned(data.game_id, player)
        view = self.game.detail(data.game_id, player).view
        if set(data.entries) - set(view.names) or "mudang" in data.entries:
            raise ValueError("unknown_npc")
        payload = {"entries": dict(data.entries), "vote_confidence": data.vote_confidence}
        with self.participant_records(data.game_id) as records:
            state, _ = self.game.owned(data.game_id, player)
            stored = {**payload, "_saved_context": {"day": state.day, "phase": state.phase.value}}
            if records.notebook == stored:
                return
            self._save_participant(state, 'notebook', EventType.NOTEBOOK_UPDATED, payload, stored, records)

    def survey(self, data, player, comment_digest):
        """종료된 판의 설문을 한 번 저장하고 최초 저장 여부를 반환한다."""
        state, _ = self.game.owned(data.game_id, player)
        if state.result is None:
            raise InteractionConflict
        payload = {"skipped": data.skipped, "fun": data.fun, "unfair": data.unfair,
                   "npc_plausible": data.npc_plausible,
                   "comment": mask_text(data.comment), "mask_version": "b-mask-1"}
        with self.participant_records(data.game_id) as records:
            if records.survey is not None:
                previous = dict(records.survey)
                previous.setdefault("skipped", False)
                previous_digest = previous.pop("comment_digest", comment_digest)
                if previous != payload or not secrets.compare_digest(previous_digest, comment_digest):
                    raise InteractionConflict
                return False
            self._save_participant(state, 'survey', EventType.SURVEY_SUBMITTED, payload,
                                   {**payload, 'comment_digest': comment_digest}, records)
            return True

    def register_renders(self, state, requests):
        """수락·저장된 전이의 렌더 요청만 등록한다. 외부 실패는 폴백으로 닫는다."""
        if not requests:
            return
        with self.store.edit(state.meta.game_id) as records:
            for req in requests:
                if req.game_id != state.meta.game_id or req.state_version != state.state_version:
                    raise ValueError("invalid_render_request")
                if req.job_id in records.renders:
                    continue
                records.renders[req.job_id] = {"request": req, "final": None,
                    "day": state.day, "phase": state.phase,
                    "deadline_at": time.time() + req.deadline_ms / 1000, "submit_failed": False}
                if self.renderer is not None:
                    try:
                        self.renderer.submit(req)
                    except (ConnectionError, TimeoutError):
                        records.renders[req.job_id]["submit_failed"] = True

    def poll(self, game_id, job_id, player):
        """판에 속한 작업만 조회하고 완료 결과·폴백을 고정해 늦은 응답을 막는다."""
        state, _ = self.game.owned(game_id, player)
        with self.store.edit(game_id) as records:
            record = records.renders.get(job_id)
            if record is None:
                raise ResourceNotFound
            if record["final"] is not None:
                return record["final"]
            req = record["request"]
            if self.renderer is None:
                raise InteractionUnavailable
            from engine.ports import RenderResult
            try:
                if record.get("submit_failed"):
                    result = RenderResult(job_id, RenderStatus.FAILED, None, fallback=True)
                elif time.time() >= record["deadline_at"]:
                    result = RenderResult(job_id, RenderStatus.TIMEOUT, None, fallback=True)
                    try:
                        self.renderer.cancel(job_id)
                    except (ConnectionError, TimeoutError, KeyError):
                        pass
                else:
                    result = self.renderer.poll(job_id)
            except (ConnectionError, TimeoutError, KeyError):
                result = RenderResult(job_id, RenderStatus.FAILED, None, fallback=True)
            final = result.status not in (RenderStatus.QUEUED, RenderStatus.RUNNING)
            output = RenderOutput(job_id, req.speech_id, result.status,
                                  result.text if result.status == RenderStatus.DONE and result.text else req.fallback_text, final)
            self._event(state, EventType.RENDER_JOB, {**to_jsonable(result), "fallback": result.fallback or not result.text or result.status != RenderStatus.DONE},
                        f"render:{job_id}:{result.status.value}")
            if final:
                record["final"] = output
            return output

    def render_snapshot(self, game_id, player):
        """화면에 붙일 공개 폴백/완료 문장만 읽는다. 외부 폴링은 실행하지 않는다."""
        self.game.owned(game_id, player)
        with self.store.edit(game_id) as records:
            return {job_id: record["final"] or RenderOutput(job_id, record["request"].speech_id,
                    RenderStatus.QUEUED, record["request"].fallback_text, False)
                    for job_id, record in records.renders.items()}

    def cancel_previous(self, previous, current):
        """날짜·단계가 바뀌면 이전 단계의 미완료 렌더를 폴백으로 확정한다."""
        if (previous.day, previous.phase) == (current.day, current.phase):
            return
        with self.store.edit(current.meta.game_id) as records:
            for job_id, record in records.renders.items():
                if record["final"] is not None:
                    continue
                req = record["request"]
                if (record["day"], record["phase"]) == (current.day, current.phase):
                    # 같은 단계의 추가 행동은 버전을 올리지만 기존 렌더는 유효하다.
                    continue
                if req.state_version >= current.state_version:
                    # 같은 전이의 재처리나 늦은 요청이 새 단계의 작업을 취소하지 않게 한다.
                    continue
                if self.renderer is not None:
                    try:
                        self.renderer.cancel(job_id)
                    except (ConnectionError, TimeoutError, KeyError):
                        pass
                output = RenderOutput(job_id, req.speech_id, RenderStatus.CANCELLED, req.fallback_text, True)
                self._event(current, EventType.RENDER_JOB, {"job_id": job_id, "status": "cancelled", "fallback": True,
                    "quantization": "", "model_version": "", "prompt_version": "", "latency_ms": None, "queue_ms": None},
                    f"render:{job_id}:cancelled")
                record["final"] = output
