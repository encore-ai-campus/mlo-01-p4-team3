"""광고 요청 재생·유효 판정·공개 이벤트를 MySQL 트랜잭션으로 묶는다."""
import hashlib
import json
import time
from dataclasses import asdict

from django.db import transaction
from django.db.models import Count

from ads.app.beacon import EventResult, _process_event
from ads.app.serve import RequestConflict
from ads.domain.events import (
    TOPIC_CLICK, TOPIC_IMPRESSION, TOPIC_SERVE, AdClickEvent, AdImpressionEvent, AdServeEvent, ServeCandidate,
)
from ads.domain.model import (
    AD_CONTRACT_VERSION, AdCreativeView, AdResponse, AdSlot, BillingType, InvalidReason, JudgeStatus,
    ServeClaims, ServeRecord,
)
from ads.domain.rules import GRACE_MS, JUDGE_VERSION, Judgement, ValidityState, charge_id, verify_token
from ads.infra.outbox import enqueue
from ads.infra.timing import connect, phase, record as record_phase
from ads.web.models import AdReceipt, AdSession, AdStoredRequest
from ads.web.telemetry import LOGGER
from engine.domain.model import PRESETS


def _digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _record(data):
    if data is None:
        return None
    row = data["record"]
    return ServeRecord(**(row | {"slot": AdSlot(row["slot"]), "billing": BillingType(row["billing"])}))


def _response(data):
    creative = data["creative"]
    return AdResponse(**(data | {"creative": AdCreativeView(**creative) if creative else None}))


def _result(data, record):
    judgement = data["judgement"]
    reason = judgement["reason"]
    return EventResult(
        Judgement(**(judgement | {"status": JudgeStatus(judgement["status"]),
                                 "reason": InvalidReason(reason) if reason else None})),
        record, data["charge_id"], data["charge_mw"], data["redirect_path"],
    )


def _event_fields(eid, rid, record, snapshot, received, occurred):
    return dict(event_id=eid, request_id=rid, serve_id=record.serve_id if record else None,
                occurred_at_ms=occurred, received_at_ms=received,
                slot=record.slot if record else None, campaign_id=record.campaign_id if record else None,
                creative_id=record.creative_id if record else None,
                session_pseudonym=record.session_pseudonym if record else None,
                source=record.source if record else "human", policy_version=snapshot.get("policy_version", ""),
                ctr_version=snapshot.get("ctr_version", ""), contract_version=AD_CONTRACT_VERSION,
                billing=record.billing if record else None, price_mw=record.price_mw if record else 0,
                load_test=snapshot.get("load_test", False), bid_version=record.bid_version if record else "")


def _lock_session(session_id, synthetic):
    return AdSession.objects.select_for_update().get_or_create(pk=_digest(session_id, synthetic))[0]


class MySQLAdStore:
    """요청 단위 서비스의 공유 상태를 행 잠금 안에서 읽고 기록한다."""

    def serve(self, service, req):
        """응답·예약·serve 아웃박스를 함께 커밋하고 같은 요청을 재생한다. 연결·커밋 시간은 따로 잰다."""
        connect()
        with transaction.atomic():
            response = self._serve_locked(service, req)
            body_done = time.perf_counter()
        record_phase("commit", time.perf_counter() - body_done)
        return response

    def _serve_locked(self, service, req):
        started = service.clock()
        with phase("session_lock"):
            _lock_session(req.session_pseudonym, req.source == "synthetic")
        request = _digest(asdict(req))
        with phase("stored_request"):
            saved, _ = AdStoredRequest.objects.select_for_update().get_or_create(
                pk=req.request_id, defaults={"request": request, "session_id": req.session_pseudonym})
        if saved.request != request:
            raise RequestConflict("request_id reused with different input")
        if saved.response is not None:
            record = _record(saved.serve_data)
            if record:
                service.state.serves[record.serve_id] = record
            service.candidates[req.request_id] = [ServeCandidate(**row) for row in saved.candidates]
            return _response(saved.response)

        with phase("frequency_query"):
            frequencies = dict(AdStoredRequest.objects.filter(session_id=req.session_pseudonym,
                               creative_id__isnull=False)
                               .values("creative_id").annotate(total=Count("pk")).values_list("creative_id", "total"))
        service.responses.pop(req.request_id, None)
        def exposures(campaign_id, day):
            # Sorted campaign order in _serve keeps these cross-session locks consistent.
            with phase("campaign_lock"):
                _lock_session("exploration:" + campaign_id + ":" + day, campaign_id.startswith("syn:"))
            counts = dict(impressions=0, explorations=0, creatives={})
            with phase("exposure_query"):
                rows = AdStoredRequest.objects.filter(impressed=True,
                    serve_data__record__campaign_id=campaign_id, serve_data__record__attribution_day=day)
                for row in rows.values("creative_id", "serve_data__record__exploration").annotate(total=Count("pk")):
                    count, cr = row["total"], row["creative_id"]
                    counts["impressions"] += count
                    counts["explorations"] += count if row["serve_data__record__exploration"] is True else 0
                    counts["creatives"][cr] = counts["creatives"].get(cr, 0) + count
            return counts
        with phase("decide"):
            response = service._serve(req, lambda creative_id: frequencies.get(creative_id, 0), exposures)
        claims, _ = verify_token(response.serve_token, service.key, started) if response.filled else (None, None)
        record = service.state.serves[claims.serve_id] if claims else None
        auction = service.auctions[req.request_id]
        candidates = service.candidates[req.request_id]
        saved.response = asdict(response)
        saved.candidates = [asdict(candidate) for candidate in candidates]
        snapshot = dict(policy_version=response.policy_version, ctr_version=response.ctr_version, load_test=req.load_test)
        if record:
            saved.serve_id, saved.creative_id = record.serve_id, record.creative_id
            saved.serve_data = snapshot | {"record": asdict(record)}
        with phase("save"):
            saved.save(update_fields=["response", "candidates", "serve_id", "creative_id", "serve_data"])
        event_id = "serve:" + _digest(req.request_id)
        fields = _event_fields(event_id, req.request_id, record, snapshot, started,
                               record.served_at_ms if record else started)
        fields.update(slot=req.slot, session_pseudonym=req.session_pseudonym, source=req.source,
                      bid_version=service.auction_bid_version)
        cid = charge_id(record.serve_id, record.billing) if record else None
        context = asdict(req.context)
        if context["preset_id"] not in PRESETS:
            context["preset_id"] = None
        with phase("outbox"):
            enqueue(TOPIC_SERVE, AdServeEvent(
                **fields, filled=response.filled, candidates=candidates, threshold_ecpm_mw=auction.threshold_ecpm_mw,
                rank_ctr=record.rank_ctr if record else 0.0, capped=auction.capped, reserved_charge_id=cid,
                reserved_mw=service.book.get(cid).amount_mw if cid else 0, context=context,
                latency_ms=max(0, service.clock() - started)))
        if service.ctr_lookup_count:
            LOGGER.info("ad_ctr_canary", extra={"fields": dict(component="ctr_canary", request_id=req.request_id,
                version=service.ctr_canary_version, fallback=bool(service.ctr_lookup_misses),
                lookup_ms=service.ctr_lookup_ns / 1_000_000)})
        return response

    @transaction.atomic
    def event(self, service, kind, token, ratio, visible_ms, occurred, rid):
        """최초 시각과 판정 결과를 저장하고 원장·이벤트를 함께 커밋한다."""
        now = service.clock()
        token_digest = _digest(token)
        event_digest = _digest("event", kind, token_digest, rid, ratio, visible_ms, occurred)
        claims, reason = verify_token(token, service.key, now)
        session = _lock_session(claims.session_pseudonym, claims.serve_id.startswith("syn:")) if claims else None
        receipt, _ = AdReceipt.objects.select_for_update().get_or_create(
            pk=event_digest, defaults={"first_received_ms": now})
        if receipt.pending_input:
            data = receipt.pending_input
            claims = ServeClaims(**(data["claims"] | {"slot": AdSlot(data["claims"]["slot"])}))
            reason = None
        return self._evaluate(service, kind, receipt, claims, reason, session, ratio, visible_ms, occurred, rid, now)

    def _evaluate(self, service, kind, receipt, claims, reason, session, ratio, visible_ms, occurred, rid, now):
        service.state = ValidityState()
        saved = (AdStoredRequest.objects.select_for_update().filter(serve_id=claims.serve_id).first()
                 if claims else None)
        snapshot = saved.serve_data if saved else {}
        record = _record(saved.serve_data) if saved else None
        if receipt.result and receipt.result["judgement"]["status"] != JudgeStatus.PENDING:
            return _result(receipt.result, record)
        first = receipt.first_received_ms
        if claims:
            if record:
                service.state.serves[record.serve_id] = record
                service.load_tests[record.serve_id] = snapshot["load_test"]
                if saved.impressed:
                    service.state.impressed.add(record.serve_id)
                if saved.clicked:
                    service.state.clicked.add(record.serve_id)
            if session.last_click_ms is not None:
                service.state.last_click_ms[claims.session_pseudonym] = session.last_click_ms
        # 메모리 규칙에 검증된 claims를 주입한다. 원문 토큰은 저장하지 않는다.
        service.pending[(kind, receipt.digest)] = (first, claims, reason)
        event_id = "judged:" + receipt.digest
        result = _process_event(service, kind, receipt.digest, ratio, visible_ms, event_id, now)
        status = result.judgement.status
        if receipt.result and status is JudgeStatus.PENDING:
            return result
        if saved:
            saved.impressed = record.serve_id in service.state.impressed
            saved.clicked = record.serve_id in service.state.clicked
            saved.save(update_fields=["impressed", "clicked"])
            last = service.state.last_click_ms.get(claims.session_pseudonym)
            if last != session.last_click_ms:
                session.last_click_ms = last
                session.save(update_fields=["last_click_ms"])
        prior_event_id = event_id + ":pending" if receipt.result else None
        receipt.result = asdict(result)
        receipt.result.pop("record")
        if status is JudgeStatus.PENDING:
            receipt.pending_input = dict(kind=kind, claims=asdict(claims), ratio=ratio, visible_ms=visible_ms,
                                         occurred=occurred, request_id=rid)
            receipt.pending_after_ms = first + GRACE_MS
        else:
            receipt.pending_input, receipt.pending_after_ms = None, 0
        receipt.save(update_fields=["result", "pending_input", "pending_after_ms"])
        self._publish_judgement(kind, event_id, rid or event_id, result, snapshot, claims, first, prior_event_id,
                                now if occurred is None else occurred, ratio, visible_ms)
        return result

    def finalize_pending(self, service, limit=250):
        """재전송 없이 유예가 끝난 pending 이벤트를 다시 판정한다."""
        now = service.clock()
        ids = list(AdReceipt.objects.filter(pending_after_ms__gt=0, pending_after_ms__lte=now)
                   .order_by("pending_after_ms", "pk").values_list("pk", flat=True)[:limit])
        return sum(self._finalize_pending(service, receipt_id, now) for receipt_id in ids)

    @transaction.atomic
    def _finalize_pending(self, service, receipt_id, now):
        observed = AdReceipt.objects.get(pk=receipt_id)
        data = observed.pending_input
        if data is None:
            return 0
        claims = ServeClaims(**(data["claims"] | {"slot": AdSlot(data["claims"]["slot"])}))
        session = _lock_session(claims.session_pseudonym, claims.serve_id.startswith("syn:"))
        receipt = AdReceipt.objects.select_for_update().get(pk=receipt_id)
        if receipt.pending_after_ms == 0 or receipt.pending_after_ms > now:
            return 0
        self._evaluate(service, data["kind"], receipt, claims, None, session, data["ratio"], data["visible_ms"],
                       data["occurred"], data["request_id"], now)
        return 1

    def _publish_judgement(self, kind, eid, rid, result, snapshot, claims, first, prior_event_id, occurred, ratio,
                           visible_ms):
        record, judgement = result.record, result.judgement
        status = judgement.status
        base = _event_fields(eid + ":pending" if status is JudgeStatus.PENDING else eid,
                             record.request_id if record else rid, record, snapshot, first, occurred)
        if record is None and claims:
            base.update(serve_id=claims.serve_id, slot=claims.slot, session_pseudonym=claims.session_pseudonym,
                        source="synthetic" if claims.serve_id.startswith("syn:") else "human")
        fields = dict(status=status, invalid_reason=judgement.reason, judge_version=JUDGE_VERSION,
                      charge_id=result.charge_id, charge_mw=result.charge_mw, source_event_id=prior_event_id)
        if kind == "impression":
            enqueue(TOPIC_IMPRESSION, AdImpressionEvent(**base, **fields, visible_ratio=ratio, visible_ms=visible_ms))
            return
        cpm_implied = judgement.implied_impression and record.billing is BillingType.CPM
        no_charge = dict(charge_id=None, charge_mw=0)
        enqueue(TOPIC_CLICK, AdClickEvent(**base, **(fields | no_charge if cpm_implied else fields),
                                         suspect=judgement.suspect, redirect_path=result.redirect_path))
        if judgement.implied_impression:
            enqueue(TOPIC_IMPRESSION, AdImpressionEvent(**(base | {"event_id": eid + ":implied"}),
                **(fields if cpm_implied else fields | no_charge), implied_by_click=True))
