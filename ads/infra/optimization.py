"""MySQL immutable optimization versions and per-process 60-second reload."""
import hashlib
import json
import threading
import time
from contextlib import contextmanager

from django.db import connection, transaction

from ads.domain.model import AdSlot, GameOutcome
from ads.web.models import AdOptimizationVersion
from ads.web.telemetry import LOGGER


KINDS = ("ctr", "pacing", "bid", "ctrml")


def latest_version(kind):
    """Find the newest immutable row by observation time, then insert order."""
    return AdOptimizationVersion.objects.filter(kind=kind).order_by("-as_of_ms", "-pk").first()


@contextmanager
def optimization_writer():
    """Serialize read-compute-publish on one Primary without locking serving reads."""
    db = hashlib.sha256(connection.settings_dict["NAME"].encode()).hexdigest()[:32]
    name = "paldo-ads-opt-" + db
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, 5)", [name])
        if cursor.fetchone()[0] != 1:
            raise ValueError("optimization writer busy")
        try:
            yield
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [name])


def version_position(row):
    """Return the ordering cursor stored when resuming Beta after rollback."""
    return [row.as_of_ms, row.pk] if row else [-1, -1]


def hold_beta(payload, beta):
    """Hold the original control during canary and until a fresh Beta after rollback."""
    return bool(payload.get("predictions")) or version_position(beta) <= payload["resume_after"]


def ctr_predictions(payload):
    """Compile an exported model lookup, rejecting invalid probabilities and mixed sources."""
    source = payload["source"]
    if source not in ("human", "synthetic"):
        raise ValueError("invalid model source")
    result = {}
    for slot, creative, outcome, preset, hour, p in payload["predictions"]:
        key = AdSlot(slot).value, creative, GameOutcome(outcome) if outcome is not None else None, preset, hour
        if (not isinstance(creative, str) or not creative or creative.startswith("syn:") != (source == "synthetic")
                or (preset is not None and not isinstance(preset, str))
                or type(hour) is not int or not 0 <= hour < 24
                or type(p) not in (float, int) or not 0 < p < 1 or key in result):
            raise ValueError("invalid CTR prediction cell")
        result[key] = p
    if not result:
        raise ValueError("empty CTR prediction lookup")
    return result


def save_version(kind, payload, as_of_ms, source_version):
    """Insert once; reject changed content under an already published source revision."""
    if (kind not in KINDS or type(as_of_ms) is not int or as_of_ms < 0
            or not isinstance(source_version, str) or not 0 < len(source_version) <= 200):
        raise ValueError("invalid optimization version metadata")
    content = json.dumps([kind, payload, as_of_ms, source_version], sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    version = kind + "-" + hashlib.sha256(content.encode()).hexdigest()
    with transaction.atomic():
        row, created = AdOptimizationVersion.objects.get_or_create(
            kind=kind, source_version=source_version, as_of_ms=as_of_ms,
            defaults={"version": version, "payload": payload})
        if row.version != version:
            raise ValueError("optimization source revision content conflict")
    LOGGER.info("ad_update_saved", extra={"fields": dict(component=kind, version=row.version,
                as_of_ms=as_of_ms, rows=len(payload.get("predictions", [])) if kind == "ctrml"
                else len(payload["bids"]) if kind == "bid" else len(payload),
                created=created)})
    return row


class OptimizationCache:
    """Atomically replace snapshots at most every 60 seconds; keep last good on DB error."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.loaded_at, self.values = clock, float("-inf"), {}
        self.lock = threading.Lock()

    def get(self):
        """Read latest persisted versions on first use/restart and after the TTL."""
        with self.lock:
            now = self.clock()
            if now - self.loaded_at >= 60:
                try:
                    values = {}
                    for kind in KINDS:
                        row = latest_version(kind)
                        if row:
                            if kind == "ctrml":
                                if row.payload.get("predictions"):
                                    row.predictions = ctr_predictions(row.payload)
                                beta = values.get("ctr")
                                if hold_beta(row.payload, beta):
                                    version = row.payload["beta_version"]
                                    row.baseline = (beta if beta and beta.version == version else
                                                    AdOptimizationVersion.objects.get(kind="ctr", version=version)
                                                    if version else None)
                                    row.holds_beta = True
                            values[kind] = row
                except Exception as exc:
                    LOGGER.error("ad_update_reload_failed", extra={"fields": dict(
                        component="optimization_cache", error_type=type(exc).__name__)})
                else:
                    self.values = values
                    LOGGER.info("ad_update_reloaded", extra={"fields": dict(component="optimization_cache",
                                versions={k: v.version for k, v in values.items()})})
                self.loaded_at = now
            return self.values.copy()
