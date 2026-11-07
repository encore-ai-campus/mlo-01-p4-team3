"""트랜잭션에 광고 이벤트를 적재하고 Kafka ACK 이후에만 발행 완료 처리한다."""
import json
from dataclasses import asdict

from django.db import transaction
from django.utils import timezone

from ads.web.models import AdOutbox


def enqueue(topic, event):
    """현재 원장·판정 트랜잭션에 계약 이벤트를 추가한다."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("outbox requires a transaction")
    return AdOutbox.objects.create(event_id=event.event_id, topic=topic,
                                   message_key=event.serve_id, payload=asdict(event))


def publish_pending(send, limit=100):
    """대기 행을 순서대로 발행한다. ACK 직후 장애 시 동일 ID로 다시 발행될 수 있다."""
    count = 0
    for _ in range(limit):
        with transaction.atomic():
            row = (AdOutbox.objects.select_for_update().filter(published_at__isnull=True)
                   .exclude(topic="").order_by("id").first())
            if row is None:
                break
            send(row.topic, row.message_key, row.payload)
            row.published_at = timezone.now()
            row.save(update_fields=["published_at"])
            count += 1
    return count


class KafkaSender:
    """로컬과 운영 Kafka 설정을 받아 브로커 전달 확인을 반환한다."""

    def __init__(self, config):
        from confluent_kafka import Producer

        self.producer = Producer(config | {"enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 5000})

    def __call__(self, topic, key, payload):
        """전달 실패 또는 타임아웃을 예외로 돌려줘 아웃박스 행이 재시도되도록 한다."""
        outcome = []
        self.producer.produce(topic, key=key, value=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
                              on_delivery=lambda error, _: outcome.append(error))
        remaining = self.producer.flush(6)
        if remaining or not outcome or outcome[0] is not None:
            raise RuntimeError("kafka_delivery_not_acknowledged")
