"""Five-stage confirmation-only parser implementing engine.ports.IntentParser."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone

from engine.domain.model import (PLAYER_ID, Command, CommandKind, DivinationPayload, DivinationResult,
                                 EvidenceType, InterrogationKind, missing_slots)
from engine.ports import CommandCandidate, ParseContext, ParseFailure, ParseResult, UtteranceForm
from intent.baseline import IntentBaseline, TOKENIZER_VERSION, load_examples
from intent.matcher import MATCHER_VERSION, find_people, match_evidence, normalize_people, resolve_time

_INTERROGATIONS = {"interrogate_whereabouts": InterrogationKind.WHEREABOUTS,
                   "interrogate_contradiction": InterrogationKind.CONTRADICTION,
                   "interrogate_suspicion_reason": InterrogationKind.SUSPICION_REASON}
_QUOTE = re.compile(r"(?:수상하다고|의심한다고)\s*[?？]")
_LOGGER = logging.getLogger("paldo.intent")


def utterance_form(text: str) -> UtteranceForm:
    """Distinguish quoted suspicion, questions, requests and assertions before slot resolution."""
    if _QUOTE.search(text):
        return UtteranceForm.QUOTE
    if text.rstrip().endswith(("?", "？")):
        return UtteranceForm.QUESTION
    if re.search(r"(?:보시오|마시오|주세요|하시오)\s*[.!]?\s*$", text):
        return UtteranceForm.REQUEST
    return UtteranceForm.ASSERTION


class BaselineIntentParser:
    """Use a fitted seed baseline plus public slot matching; never execute a command."""

    def __init__(self, model: IntentBaseline | None = None):
        self.model = model if model is not None else IntentBaseline().fit(load_examples())

    def parse(self, text: str, ctx: ParseContext) -> ParseResult:
        """Return score-sorted candidates and missing slots without mutating the context."""
        alias_data = json.dumps(ctx.aliases, ensure_ascii=False, sort_keys=True)
        versions = {"model": self.model.model_version,
                    "tokenizer": getattr(self.model, "tokenizer_version", TOKENIZER_VERSION),
                    "alias_dict": hashlib.sha256(alias_data.encode()).hexdigest()[:12],
                    "matcher": MATCHER_VERSION, "threshold": f"max-prob-{self.model.threshold:g}-v1"}
        if not text.strip():
            return self._result([], ParseFailure.EMPTY_INPUT, versions)
        candidates = []
        ambiguous = False
        # Preserve the predicate ending: cutting all of '하지만' loses '하다' and changes TF-IDF evidence.
        clauses = re.sub(r"하지만", "하다;", text)
        for part in re.split(r"그렇지만|그러나|[;\n]+", clauses):
            if not part.strip():
                continue
            try:
                normalized = normalize_people(part, ctx.aliases)
                label, score = self.model.predict(normalized)
                form = utterance_form(part)
                if form is UtteranceForm.QUOTE:
                    label = "interrogate_suspicion_reason"
                    score = self.model.probabilities(normalized).get(label, 0.0)
                if label == "unsupported":
                    continue
                candidate, tied = self._candidate(part, ctx, label, score, form)
                candidates.append(candidate)
                ambiguous |= tied
            except ValueError:
                return self._result([], ParseFailure.UNPARSEABLE, versions)
        candidates.sort(key=lambda candidate: candidate.intent_score, reverse=True)
        failure = (ParseFailure.UNSUPPORTED_INTENT if not candidates else
                   ParseFailure.AMBIGUOUS_EVIDENCE if ambiguous else
                   ParseFailure.MISSING_TARGET if any({"target", "addressee"} & set(c.missing_slots)
                                                       for c in candidates) else None)
        return self._result(candidates, failure, versions)

    def _candidate(self, text, ctx, label, score, form):
        matches = find_people(text, ctx.aliases)
        people = list(dict.fromkeys(match.npc_id for match in matches))
        fallback = next((person for person in (ctx.selected_npc, ctx.last_addressee)
                         if person in ctx.aliases), None)
        person = people[0] if people else fallback
        command = Command(CommandKind.INTERROGATE if label in _INTERROGATIONS else CommandKind(label), PLAYER_ID)
        evidence = None
        if label in ("suspect", "defend"):
            command.target = person
        elif label in _INTERROGATIONS:
            command.interrogation_kind = _INTERROGATIONS[label]
            command.addressee = person
            if command.interrogation_kind is InterrogationKind.SUSPICION_REASON:
                # Existing target carries the mentioned person; addressee remains the recipient.
                command.target = people[-1] if people else None
                if form is UtteranceForm.QUOTE:
                    speakers = [s.speaker for s in ctx.public.speeches
                                if s.kind is CommandKind.SUSPECT and s.target == command.target
                                and s.speaker in ctx.aliases]
                    command.addressee = speakers[-1] if speakers else (
                        ctx.last_addressee if ctx.last_addressee in ctx.aliases else None)
                elif len(people) < 2:
                    command.addressee = fallback
            elif command.interrogation_kind is InterrogationKind.WHEREABOUTS:
                command.night, command.slot = resolve_time(text, ctx)
        elif label == "reveal_divination":
            night, _ = resolve_time(text, ctx)
            if night is None:
                night = max((c.night for c in ctx.public.claims), default=None)
            result = DivinationResult.CLEAR if "맑" in text else DivinationResult.MURKY if "탁" in text else None
            if person is not None and night is not None and result is not None:
                command.divination = DivinationPayload(person, night, result)
        needs_evidence = (label == "point_contradiction" or label == "interrogate_contradiction")
        if needs_evidence:
            evidence = match_evidence(text, ctx)
            command.evidence = evidence.evidence
        missing = missing_slots(command)
        if needs_evidence:
            adequate = (any(ref.type is EvidenceType.CONTRADICTION for ref in command.evidence) or
                        len({ref.record_id for ref in command.evidence if ref.type is EvidenceType.CLAIM}) >= 2)
            if not adequate and "evidence" not in missing:
                missing.append("evidence")
        scores = {"person": 1.0 if people else .5 if person else 0.0}
        if evidence is not None:
            scores.update({f"evidence:{key}": value for key, value in evidence.scores.items()})
        return CommandCandidate(command, score, form, missing, scores,
                                evidence.alternatives if evidence else []), bool(evidence and evidence.alternatives)

    @staticmethod
    def _result(candidates, failure, versions):
        _LOGGER.info(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "level": "info",
                                 "service": "intent", "component": "parser", "msg": "parsed",
                                 "candidates": len(candidates), "failure": failure.value if failure else None,
                                 "versions": versions}, ensure_ascii=False))
        return ParseResult(candidates, failure, True, versions)
