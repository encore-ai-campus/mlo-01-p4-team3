"""Publish versioned CTR and CPC settings or run the five-minute pacing controller."""
import json
import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from ads.app.bid_update import build_bids
from ads.app.ctr_update import build_ctr, finalized_rows
from ads.app.pacing_update import build_pacing
from ads.app.serve import ATTRIBUTION_TZ
from ads.infra.mysql_ledger import MySQLLedger
from ads.infra.optimization import (
    ctr_predictions, hold_beta, latest_version, optimization_writer, save_version, version_position,
)
from ads.project.composition import load_campaigns, load_policies
from ads.web.models import AdOptimizationVersion
from ads.web.telemetry import LOGGER
from ml.ad_ctr import FEATURE_VERSION, registered_report
from ml.gate import ad_ctr_gate


def _publish_bid(snapshot):
    finalized_rows(snapshot)
    as_of, source = snapshot["as_of_ms"], snapshot["source_version"]
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()).hexdigest()
    row = AdOptimizationVersion.objects.filter(kind="bid", as_of_ms=as_of, source_version=source).first()
    if row:
        if row.payload["input_sha256"] != digest:
            raise ValueError("bid source revision content conflict")
        return row
    last = latest_version("bid")
    if last and as_of // 3_600_000 <= last.as_of_ms // 3_600_000:
        raise ValueError("bid update already published for this or a later hour")
    saved = {r["campaign_id"]: r for r in last.payload["bids"]} if last else {}
    payload = dict(input_sha256=digest, bids=build_bids(snapshot, load_campaigns(), saved))
    return save_version("bid", payload, as_of, source)


def _publish_canary(report, last):
    source, registry = report["source"], report["registry_version"]
    if not ad_ctr_gate(report["lightgbm"], report["beta"]).accepted or report["gate"]["accepted"] is not True:
        raise ValueError("CTR evaluation rejected")
    if (report["feature_version"] != FEATURE_VERSION or not isinstance(registry, str)
            or not re.fullmatch(r"paldo-ad-ctr-" + re.escape(source) + r":[1-9][0-9]*", registry)):
        raise ValueError("registered CTR candidate required")
    payload = dict(source=source, predictions=report["predictions"])
    ctr_predictions(payload)
    if last and last.payload.get("predictions"):
        if last.source_version == registry and all(last.payload[key] == value for key, value in payload.items()):
            return last
        raise ValueError("another CTR canary is active")
    if AdOptimizationVersion.objects.filter(kind="ctrml", source_version=registry).exists():
        raise ValueError("rolled back CTR candidate cannot be replayed")
    beta = latest_version("ctr")
    payload["beta_version"] = (last.payload["beta_version"] if last and last.payload["source"] == source
                               and hold_beta(last.payload, beta) else beta.version if beta else "")
    return save_version("ctrml", payload, max(time.time_ns() // 1_000_000, last.as_of_ms + 1 if last else 0), registry)


def _rollback_canary(last, expected, signal, value):
    threshold = {"fallback_rate": .05, "p95_ms": 100}[signal]
    if not (threshold < value < float("inf")) or (signal == "fallback_rate" and value > 1):
        raise ValueError("rollback signal does not exceed its threshold")
    if not last or last.version != expected or not last.payload.get("predictions"):
        raise ValueError("active CTR version changed")
    return save_version("ctrml", dict(signal=signal, value=value, rolled_back=expected,
                        source=last.payload["source"], beta_version=last.payload["beta_version"],
                        resume_after=version_position(latest_version("ctr"))),
                        max(time.time_ns() // 1_000_000, last.as_of_ms + 1), "rollback:" + expected)


class Command(BaseCommand):
    """Adapter entry point for C gold exports; pacing reads the Primary ledger."""

    def add_arguments(self, parser):
        """CTR takes a complete export; pacing supports one bucket or continuous execution."""
        parser.add_argument("kind", choices=["ctr", "pacing", "bid", "ctrml", "rollback-ctr"])
        parser.add_argument("--input", type=Path)
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--expected-version")
        parser.add_argument("--signal", choices=["fallback_rate", "p95_ms"])
        parser.add_argument("--value", type=float)

    def handle(self, *args, **options):
        """Validate before publishing; repeat pacing once per five-minute wall-clock bucket."""
        kind = options["kind"]
        if kind in ("bid", "ctrml", "rollback-ctr"):
            try:
                if kind == "ctrml":
                    snapshot = json.loads(options["input"].read_text(encoding="utf-8"))
                    registry = snapshot.pop("registry_version")
                    if not isinstance(registry, str) or not re.fullmatch(r"paldo-ad-ctr-(synthetic|human):[1-9][0-9]*", registry):
                        raise ValueError("registered CTR candidate required")
                    if registered_report(registry) != snapshot:
                        raise ValueError("CTR candidate report differs from registry artifact")
                    snapshot["registry_version"] = registry
                with optimization_writer():
                    if kind == "rollback-ctr":
                        row = _rollback_canary(latest_version("ctrml"), options["expected_version"],
                                               options["signal"], options["value"])
                    elif kind == "bid":
                        snapshot = json.loads(options["input"].read_text(encoding="utf-8"))
                        row = _publish_bid(snapshot)
                    else:
                        row = _publish_canary(snapshot, latest_version("ctrml"))
            except (OSError, AttributeError, ImportError, KeyError, TypeError, ValueError) as exc:
                raise CommandError(f"{kind} update rejected: {exc}") from None
            self.stdout.write(row.version)
            return
        if kind == "ctr":
            if options["input"] is None:
                raise CommandError("ctr requires --input complete finalized export")
            try:
                snapshot = json.loads(options["input"].read_text(encoding="utf-8"))
                payload = build_ctr(snapshot, load_policies())
                with optimization_writer():
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
                    last = latest_version("pacing")
                    previous = {r["campaign_id"]: r["probability"] for r in last.payload
                                if r["attribution_day"] == day} if last else {}
                    payload = build_pacing(load_campaigns(), MySQLLedger(), day, now.hour * 60 + now.minute, previous)
                    save_version("pacing", payload, bucket, source)
                if options["once"]:
                    return
                time.sleep(max(1., 300 - time.time() % 300))
        except KeyboardInterrupt:
            return
