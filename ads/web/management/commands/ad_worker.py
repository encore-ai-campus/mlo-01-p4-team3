"""웹 서버와 분리해 만료·발행·환입 중 하나를 실행한다."""
import json
import os
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from prometheus_client import CollectorRegistry, Counter, start_http_server

from ads.app.expire_reservations import expire_once
from ads.app.refund_consumer import consume_one
from ads.domain.events import TOPIC_REJUDGE
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.outbox import KafkaSender, publish_pending
from ads.web.telemetry import LOGGER


class Command(BaseCommand):
    """각 role을 별도 프로세스로 실행하는 광고 관리 명령."""

    help = "Run an ad reservation expiry, Kafka outbox, or refund worker."

    def add_arguments(self, parser):
        """실행 모드와 비밀 설정 파일, 작업자 계측 옵션을 받는다."""
        parser.add_argument("role", choices=["expire", "publish", "refund"])
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=float, default=1.0)
        parser.add_argument("--metrics-port", type=int)
        parser.add_argument("--kafka-config", type=Path)
        parser.add_argument("--group", default="paldo-ad-refund")

    def handle(self, *args, **options):
        """작업을 반복하며 성공 건수·오류를 구조화 로그와 지표에 남긴다."""
        role, interval = options["role"], options["interval"]
        if not 0 < interval <= 60:
            raise CommandError("--interval must be in (0, 60]")
        ledger = MySQLLedger()
        consumer = None
        if role == "expire":
            work = lambda: expire_once(ledger)
        else:
            config = _kafka_config(options["kafka_config"])
            if role == "publish":
                sender = KafkaSender(config)
                work = lambda: publish_pending(sender)
            else:
                from confluent_kafka import Consumer

                consumer = Consumer(config | {"group.id": options["group"], "enable.auto.commit": False,
                    "enable.auto.offset.store": False, "auto.offset.reset": "earliest"})
                consumer.subscribe([TOPIC_REJUDGE])
                work = lambda: consume_one(consumer, ledger)
        registry = CollectorRegistry()
        processed = Counter("paldo_ad_worker_processed_total", "Completed ad worker changes", ["role"], registry=registry)
        failures = Counter("paldo_ad_worker_errors_total", "Ad worker iteration errors", ["role"], registry=registry)
        if options["metrics_port"] is not None:
            start_http_server(options["metrics_port"], addr="127.0.0.1", registry=registry)
        LOGGER.info("ad_worker_started", extra={"fields": {"component": role}})
        try:
            while True:
                close_old_connections()
                try:
                    count = work()
                except Exception as exc:
                    failures.labels(role).inc()
                    LOGGER.error("ad_worker_error", extra={"fields": {
                        "component": role, "error_type": type(exc).__name__,
                    }})
                    # Rejoin before polling after a refund error; a later offset must not skip it.
                    if options["once"] or consumer is not None:
                        raise CommandError(f"{role} iteration failed") from None
                else:
                    processed.labels(role).inc(count)
                    if count or options["once"]:
                        LOGGER.info("ad_worker_processed", extra={"fields": {"component": role, "count": count}})
                    if options["once"]:
                        return
                if role != "refund":
                    time.sleep(interval)
        except KeyboardInterrupt:
            return
        finally:
            if consumer is not None:
                consumer.close()


def _kafka_config(path):
    if path is None:
        bootstrap = os.environ.get("PALDO_KAFKA_BOOTSTRAP")
        if not bootstrap:
            raise CommandError("PALDO_KAFKA_BOOTSTRAP or --kafka-config is required")
        config = {"bootstrap.servers": bootstrap}
        if bootstrap.startswith(("localhost:", "127.0.0.1:")):
            config["broker.address.family"] = "v4"
        return config
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not config.get("bootstrap.servers"):
            raise ValueError
    except (OSError, ValueError):
        raise CommandError("invalid Kafka config") from None
    return config
