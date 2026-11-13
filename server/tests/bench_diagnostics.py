"""Host and proxy measurements for the split-host API benchmark."""
import json
from pathlib import Path
import re
import subprocess
import time

from .api_bench import percentile


def access_summary(path, requests):
    """Join Caddy's duration to the exact API requests attempted by the client."""
    proxy = {}
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            event = json.loads(line)
            if not event.get('logger', '').startswith('http.log.access'):
                continue
            headers = event['request']['headers']
            ids = next((values for name, values in headers.items()
                        if name.lower() == 'x-paldo-bench-request-id'), [])
            if ids:
                proxy[ids[0]] = event['duration'] * 1000
    caddy = [proxy[row['bench_id']] for row in requests if row['bench_id'] in proxy]
    django = [row['stages']['server_total'] for row in requests if 'server_total' in row['stages']]
    return dict(caddy_samples=len(caddy), caddy_p95_ms=p95(caddy),
                django_samples=len(django), django_p95_ms=p95(django))


def p95(values):
    """Return milliseconds with three decimals, or null for a missing sample."""
    return round(percentile(values, .95), 3) if values else None


def cpu_sample():
    """Return timestamp, total ticks and idle ticks for all Linux CPUs."""
    with Path('/proc/stat').open() as stream:
        _, *fields = stream.readline().split()
    ticks = list(map(int, fields[:8]))  # guest ticks are already included in user and nice
    return time.time(), sum(ticks), ticks[3] + ticks[4]


def cpu_summary(samples, window):
    """Measure average host utilization over the counter interval inside the load window."""
    active = [row for row in samples if window['start'] <= row[0] <= window['end']]
    if len(active) < 2:
        raise ValueError('missing overlapping CPU samples')
    start, end = active[0], active[-1]
    total = end[1] - start[1]
    idle = end[2] - start[2]
    if total <= 0 or not 0 <= idle <= total:
        raise ValueError('invalid CPU counter difference')
    return dict(host_cpu_percent=round(100 * (total - idle) / total, 3),
                cpu_sample_window={'start': start[0], 'end': end[0]}, cpu_samples=len(active))


def with_ping(host, folder, drive):
    """Probe direct WSL2-to-server ICMP RTT throughout the same API load window."""
    path = folder / 'ping.log'
    with path.open('w') as log:
        ping = subprocess.Popen(['ping', '-n', '-D', '-O', '-i', '1', '-W', '1', host],
                                stdout=log, stderr=subprocess.STDOUT, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        try:
            row = drive()
        finally:
            if ping.poll() is None:
                ping.terminate()
                ping.wait(timeout=5)
    replies = []
    for line in path.read_text().splitlines():
        match = re.search(r'^\[(\d+(?:\.\d+)?)\].*time[=<](\d+(?:\.\d+)?) ms', line)
        if match:
            received, ms = map(float, match.groups())
            if row['window']['start'] <= received <= row['window']['end']:
                replies.append(ms)
    row.update(rtt_samples=len(replies), rtt_p95_ms=p95(replies))
    return row
