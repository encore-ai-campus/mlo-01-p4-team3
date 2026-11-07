"""광고 발행 ACK·재발행과 재판정 입력 경계를 검증한다."""
from dataclasses import asdict
import json
from unittest.mock import Mock

from django.test import TransactionTestCase

from ads.app.refund_consumer import refund_message
from ads.domain.events import TOPIC_CLICK, TOPIC_REJUDGE, AdRejudgeEvent
from ads.domain.model import AD_CONTRACT_VERSION, BillingType, InvalidReason
from ads.domain.rules import JUDGE_VERSION, charge_id
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.outbox import publish_pending
from ads.web.models import AdOutbox
from tests.contract.test_ads import _camp, _serve


class WorkerTests(TransactionTestCase):
    """실제 MySQL에서 브로커 응답 실패 후 재시도와 환입 멱등성을 확인한다."""

    def test_publish_retries_same_event_without_marking_failed_delivery(self):
        row = AdOutbox.objects.create(event_id="fixed-event", topic=TOPIC_CLICK, message_key="serve",
                                      payload={"event_id": "fixed-event"})
        send = Mock(side_effect=[RuntimeError("ack missing"), None])
        with self.assertRaises(RuntimeError):
            publish_pending(send)
        row.refresh_from_db()
        self.assertIsNone(row.published_at)
        self.assertEqual(publish_pending(send), 1)
        self.assertEqual(send.call_args_list[0], send.call_args_list[1])
        self.assertEqual(publish_pending(send), 0)

    def test_rejudge_only_refunds_original_event_once(self):
        ledger = MySQLLedger()
        record = _serve("s", BillingType.CPC, 50)
        cid = charge_id(record.serve_id, record.billing)
        self.assertTrue(ledger.reserve(_camp(100, BillingType.CPC), record))
        self.assertTrue(ledger.confirm(cid, "original"))
        event = AdRejudgeEvent(f"rj:{cid}:{JUDGE_VERSION}", "original", cid, InvalidReason.DUPLICATE,
                               JUDGE_VERSION, 10, "human", AD_CONTRACT_VERSION)
        data = asdict(event)
        with self.assertRaises(ValueError):
            refund_message(ledger, TOPIC_REJUDGE, b"other", json.dumps(data).encode())
        self.assertIsNone(refund_message(ledger, TOPIC_REJUDGE, cid.encode(),
                                        json.dumps(data | {"source_event_id": "other"}).encode()))
        self.assertEqual(refund_message(ledger, TOPIC_REJUDGE, cid.encode(),
                                       json.dumps(data).encode()).amount_mw, -50)
        self.assertIsNone(refund_message(ledger, TOPIC_REJUDGE, cid.encode(), json.dumps(data).encode()))
