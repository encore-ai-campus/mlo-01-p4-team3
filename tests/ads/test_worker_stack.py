"""로컬 Kafka와 실제 MySQL에 연결하는 D-06-02 통합 시험."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import closing
from dataclasses import asdict
from unittest import skipUnless
from urllib.request import urlopen

from django.db import connection
from django.test import TransactionTestCase

from ads.app.beacon import handle_impression
from ads.app.click import handle_click
from ads.app.refund_consumer import consume_one
from ads.domain.events import TOPIC_CLICK, TOPIC_IMPRESSION, TOPIC_REJUDGE, TOPIC_SERVE, AdRejudgeEvent
from ads.domain.model import AD_CONTRACT_VERSION, AdContext, AdRequest, AdSlot, BillingType, InvalidReason
from ads.domain.rules import JUDGE_VERSION, RELEASE_AFTER_MS, reconcile
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.outbox import KafkaSender, publish_pending
from ads.project.composition import get_service
from ads.web.models import AdLedgerEntry, AdOutbox, Campaign, Creative
from tests.ads.test_http import BASE_MS
from tests.contract.test_ads import _camp, _serve


class WorkerStackTests(TransactionTestCase):
    """웹과 별도 실행 경로에서 MySQL·Kafka 상태를 직접 확인한다."""

    def test_expiry_process_catches_up_after_restart(self):
        schema = connection.settings_dict["NAME"]
        self.assertTrue(schema.startswith("test_"), "use an isolated test schema")
        env = os.environ | {"PALDO_ADS_DB_NAME": schema}
        args = [sys.executable, "-X", "utf8", "ads/manage.py", "ad_worker", "expire"]
        with subprocess.Popen(args + ["--interval", "0.1"], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, encoding="utf-8") as process:
            try:
                # 기존 작업자를 끈 상태에서 밀린 예약을 만든다.
                self.assertIn("ad_worker_started", process.stdout.readline())
            finally:
                process.kill()
                process.communicate(timeout=10)
        record = _serve("missed", BillingType.CPC, 10, int(time.time() * 1000) - RELEASE_AFTER_MS - 1000)
        self.assertTrue(MySQLLedger().reserve(_camp(100, BillingType.CPC), record))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with subprocess.Popen(args + ["--interval", "0.1", "--metrics-port", str(port)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8") as restarted:
            expected = 'paldo_ad_worker_processed_total{role="expire"} 1.0'
            metrics = ""
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and restarted.poll() is None:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=0.5) as response:
                            metrics = response.read().decode()
                        if expected in metrics:
                            break
                    except OSError:
                        pass
                    time.sleep(0.1)
                self.assertIn(expected, metrics)
            finally:
                restarted.kill()
                output, _ = restarted.communicate(timeout=10)
        self.assertIn('"count": 1', output)
        self.assertEqual(AdLedgerEntry.objects.get().state, "released")
        print(output.strip())
        print(json.dumps({"expired_after_restart": 1}))

    @skipUnless(os.environ.get("PALDO_ADS_TEST_KAFKA"), "PALDO_ADS_TEST_KAFKA not set")
    def test_real_kafka_replay_and_rejudge(self):
        from confluent_kafka import Consumer, TopicPartition
        from confluent_kafka.admin import AdminClient, NewTopic

        bootstrap = os.environ["PALDO_ADS_TEST_KAFKA"]
        self.assertTrue(bootstrap.startswith(("127.0.0.1:", "localhost:")), "use a loopback test broker")
        config = {"bootstrap.servers": bootstrap, "broker.address.family": "v4"}
        topics = (TOPIC_SERVE, TOPIC_IMPRESSION, TOPIC_CLICK, TOPIC_REJUDGE)
        admin = AdminClient(config)
        existing = admin.list_topics(timeout=10).topics
        missing = [NewTopic(name, 1, 1) for name in topics if name not in existing]
        if missing:
            for future in admin.create_topics(missing).values():
                future.result(10)

        rid = "stack-" + uuid.uuid4().hex
        campaign = Campaign.objects.create(campaign_id="stack", advertiser_id="adv", name="ad",
            slots=["lobby_banner"], billing="cpm", max_bid_mw=10_000_000, daily_budget_mw=10_000_000,
            start_date="2026-11-01", end_date="2026-12-01")
        Creative.objects.create(creative_id="stack-cr", campaign=campaign, title="ad", body="ad",
                                image_path="/static/ads/example.png")
        service = get_service()
        service.clock = lambda: BASE_MS
        out = service.serve(AdRequest(rid, AdSlot.LOBBY_BANNER, "stack", AdContext()))
        handle_impression(service, out.serve_token, 1.0, 1500, request_id=rid + "-impression")
        handle_click(service, out.serve_token, request_id=rid + "-click")
        public = dict(AdOutbox.objects.exclude(topic="").values_list("event_id", "topic"))
        self.assertEqual(set(public.values()), set(topics[:3]))
        sender = KafkaSender(config)

        def crash_after_ack(topic, key, payload):
            sender(topic, key, payload)
            raise RuntimeError("crash after broker ACK")

        with self.assertRaisesRegex(RuntimeError, "after broker ACK"):
            publish_pending(crash_after_ack)
        self.assertEqual(publish_pending(sender), 3)
        self.assertEqual(publish_pending(sender), 0)

        with closing(Consumer(config | {"group.id": rid, "enable.auto.commit": False})) as reader:
            reader.assign([TopicPartition(name, 0, 0) for name in topics[:3]])
            received = []
            end = time.monotonic() + 15
            while len(received) < 4 and time.monotonic() < end:
                msg = reader.poll(0.5)
                if msg and not msg.error():
                    event = json.loads(msg.value())
                    if event["event_id"] in public:
                        self.assertEqual(msg.topic(), public[event["event_id"]])
                        self.assertEqual(msg.key().decode(), event["serve_id"])
                        received.append(event["event_id"])
            self.assertEqual((len(received), len(set(received))), (4, 3))

        original = AdLedgerEntry.objects.get()
        cid = original.charge_id
        event = AdRejudgeEvent(f"rj:{cid}:{JUDGE_VERSION}", original.source_event_id, cid, InvalidReason.DUPLICATE,
                               JUDGE_VERSION, BASE_MS, "human", AD_CONTRACT_VERSION)
        ledger = MySQLLedger()
        with closing(Consumer(config | {"group.id": rid + "-refund", "enable.auto.commit": False})) as reader:
            partition = TopicPartition(TOPIC_REJUDGE, 0)
            _, high = reader.get_watermark_offsets(partition, timeout=10)
            reader.assign([TopicPartition(TOPIC_REJUDGE, 0, high)])
            for _ in range(2):
                sender(TOPIC_REJUDGE, cid, asdict(event))
            end = time.monotonic() + 15
            while time.monotonic() < end:
                consume_one(reader, ledger)
                offsets = reader.committed([partition], timeout=5)
                if offsets[0].offset >= high + 2:
                    break
            self.assertGreaterEqual(offsets[0].offset, high + 2)
        self.assertEqual(AdLedgerEntry.objects.filter(kind="refund").count(), 1)
        self.assertFalse(any(reconcile(ledger.confirmed_amounts(), {}).values()))
        print(json.dumps({"published_topics": sorted(set(public.values())), "received": len(received),
                          "unique": len(set(received)), "refund_rows": 1, "net_mw": 0}))
