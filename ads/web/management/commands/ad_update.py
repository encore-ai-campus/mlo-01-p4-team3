"""Publish a complete final CTR snapshot or run the five-minute pacing controller."""
import json
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from ads.app.ctr_update import build_ctr
from ads.app.pacing_update import build_pacing
from ads.app.serve import ATTRIBUTION_TZ
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.optimization import save_version
from ads.project.composition import load_campaigns, load_policies
from ads.web.models import AdOptimizationVersion
from ads.web.telemetry import LOGGER


class Command(BaseCommand):
    """Adapter entry point for C gold exports; pacing reads the Primary ledger."""

    def add_arguments(self, parser):
        """CTR takes a complete export; pacing supports one bucket or continuous execution."""
        parser.add_argument("kind", choices=["ctr", "pacing"])
        parser.add_argument("--input", type=Path)
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        """Validate before publishing; repeat pacing once per five-minute wall-clock bucket."""
        kind = options["kind"]
        if kind == "ctr":
            if options["input"] is None:
                raise CommandError("ctr requires --input complete finalized export")
            try:
                snapshot = json.loads(options["input"].read_text(encoding="utf-8"))
                payload = build_ctr(snapshot, load_policies())
                save_version("ctr", payload, snapshot["as_of_ms"], snapshot["source_version"])
            except (OSError, KeyError, TypeError, ValueError) as exc:
                LOGGER.error("ad_update_failed", extra={"fields": dict(component=kind, error_type=type(exc).__name__)})
                raise CommandError("invalid complete CTR snapshot") from None
            return
        if options["input"] is not None:
            raise CommandError("pacing uses MySQL reserved + net confirmed, not an input export")
        try:
            # shortcut: one pacing worker; serialize buckets in DB before scheduling multiple workers.
            while True:
                close_old_connections()
                bucket = int(time.time() // 300) * 300_000
                source = "pacing-5min:" + str(bucket)
                if not AdOptimizationVersion.objects.filter(kind="pacing", source_version=source).exists():
                    now = datetime.fromtimestamp(bucket / 1000, ATTRIBUTION_TZ)
                    day = now.date().isoformat()
                    last = AdOptimizationVersion.objects.filter(kind="pacing").order_by("-as_of_ms", "-pk").first()
                    previous = {r["campaign_id"]: r["probability"] for r in last.payload
                                if r["attribution_day"] == day} if last else {}
                    payload = build_pacing(load_campaigns(), MySQLLedger(), day, now.hour * 60 + now.minute, previous)
                    save_version("pacing", payload, bucket, source)
                if options["once"]:
                    return
                time.sleep(max(1., 300 - time.time() % 300))
        except KeyboardInterrupt:
            return
