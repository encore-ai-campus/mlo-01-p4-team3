"""MySQL immutable optimization versions and per-process 60-second reload."""
import hashlib
import json
import threading
import time

from django.db import transaction

from ads.web.models import AdOptimizationVersion
from ads.web.telemetry import LOGGER


def save_version(kind, payload, as_of_ms, source_version):
    """Insert once; reject changed content under an already published source revision."""
    if (kind not in ("ctr", "pacing") or type(as_of_ms) is not int or as_of_ms < 0
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
                as_of_ms=as_of_ms, rows=len(payload), created=created)})
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
                    for kind in ("ctr", "pacing"):
                        row = AdOptimizationVersion.objects.filter(kind=kind).order_by("-as_of_ms", "-pk").first()
                        if row:
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
