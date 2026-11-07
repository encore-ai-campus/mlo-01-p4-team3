"""노출 판정과 클릭이 공유하는 토큰·유예·과금 처리."""
import uuid
from dataclasses import dataclass

from ads.domain.model import BillingType, JudgeStatus
from ads.domain.rules import charge_id, judge_click, judge_impression, verify_token


@dataclass
class EventResult:
    """HTTP 응답에 노출하지 않는 판정·과금 로그 데이터."""
    judgement: object
    record: object = None
    charge_id: str | None = None
    charge_mw: int = 0
    redirect_path: str = "/ads/landing/unavailable"


def process_event(service, kind, token, *, visible_ratio=0., visible_ms=0):
    """서버 수신 시각으로 판정하고 유효 이벤트만 멱등 확정한다."""
    with service.lock:
        now = service.clock()
        pending_key = (kind, token)
        first, claims, reason = service.pending.get(pending_key, (None, None, None))
        if first is None:
            first = now
            claims, reason = verify_token(token, service.key, first)
        load_test = bool(claims and service.load_tests.get(claims.serve_id, False))
        if kind == "click":
            j = judge_click(service.state, claims, reason, first, now, load_test)
        else:
            j = judge_impression(service.state, claims, reason, visible_ratio, visible_ms, first, now, load_test)
        if j.status is JudgeStatus.PENDING:
            service.pending[pending_key] = (first, claims, reason)
        record = service.state.serves.get(claims.serve_id) if claims else None
        result = EventResult(j, record)
        if record:
            result.redirect_path = "/ads/landing/" + record.creative_id
            if j.status is JudgeStatus.VALID and (
                (record.billing is BillingType.CPC and kind == "click")
                or (record.billing is BillingType.CPM and (kind == "impression" or j.implied_impression))
            ):
                cid = charge_id(record.serve_id, record.billing)
                if service.book.confirm(cid, str(uuid.uuid4())):
                    result.charge_id = cid
                    result.charge_mw = service.book.rows[cid].amount_mw
        service.last_result = result
        return result


def handle_impression(service, token, visible_ratio, visible_ms):
    """가시 비콘을 판정한다. 클라이언트 발생 시각은 판정에 사용하지 않는다."""
    return process_event(service, "impression", token, visible_ratio=visible_ratio, visible_ms=visible_ms)
