"""silver 재판정 이벤트를 검증하고 환입 커밋 뒤 Kafka offset을 커밋한다."""
import json
import logging

from ads.domain.events import TOPIC_REJUDGE, AdRejudgeEvent
from ads.domain.model import AD_CONTRACT_VERSION, InvalidReason

LOGGER = logging.getLogger("paldo.ads")


class InvalidRejudge(ValueError):
    """필수 재판정 필드나 원과금 키가 계약과 다르다."""


def refund_message(ledger, topic, key, payload):
    """ad.rejudge 계약과 원과금 이벤트 식별자가 맞을 때만 환입한다."""
    try:
        event = AdRejudgeEvent(**json.loads(payload))
        InvalidReason(event.invalid_reason)
        if not all(isinstance(value, str) and 0 < len(value) <= size for value, size in (
            (event.event_id, 220), (event.source_event_id, 220), (event.charge_id, 160), (event.judge_version, 32),
        )):
            raise ValueError
        if (topic != TOPIC_REJUDGE or key != event.charge_id.encode()
                or not event.charge_id.endswith((":click", ":impression"))
                or event.event_id != f"rj:{event.charge_id}:{event.judge_version}"
                or type(event.occurred_at_ms) is not int or event.occurred_at_ms < 0
                or event.source not in ("human", "synthetic")
                or event.contract_version not in ("ads-0.4", "ads-0.5", AD_CONTRACT_VERSION)):
            raise ValueError
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidRejudge("invalid ad.rejudge") from exc
    return ledger.refund(event.charge_id, event.event_id,
                         source_event_id=event.source_event_id, source=event.source)


def consume_one(consumer, ledger):
    """재판정 한 건을 처리한다. DB 실패 시 offset을 커밋하지 않는다."""
    message = consumer.poll(1)
    if message is None:
        return 0
    if message.error():
        raise RuntimeError("kafka_rejudge_poll_failed")
    try:
        row = refund_message(ledger, message.topic(), message.key(), message.value())
    except InvalidRejudge:
        LOGGER.warning("ad_rejudge_invalid", extra={"fields": {"component": "refund"}})
        row = None
    if row:
        LOGGER.info("ad_refunded", extra={"fields": {
            "component": "refund", "charge_id": row.charge_id, "amount_mw": row.amount_mw,
        }})
    consumer.commit(message=message, asynchronous=False)
    return int(row is not None)
