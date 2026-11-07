"""D-06-03: hidden CTR dice against an isolated real Django HTTP ad server.

Only the client reads/writes evaluation truth. No DB, existing service, or
production HTTP contract is changed by the synthetic test control plane.
"""
import argparse
import hashlib
import http.client
import json
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from ads.domain.model import AdSlot, GameOutcome
from ads.domain.rules import TOKEN_TTL_MS, VIEWABLE_MS, VIEWABLE_RATIO
from tools.gen_synthetic_ads import hidden_ctrs, synthetic_campaigns
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
STEP_MS = 10
INVALID_TYPES = ('duplicate', 'bad_signature', 'no_serve', 'expired')


def viewable(ratio, milliseconds):
    """Use the contractual 50% / 1 second boundary for sending beacons."""
    return ratio >= VIEWABLE_RATIO and milliseconds >= VIEWABLE_MS


def click_probability(ctr, previous_views, fatigue_decay):
    """Each prior view of this creative in this session multiplies CTR by 1-decay."""
    return ctr * (1 - fatigue_decay) ** previous_views


class _Server:
    def __init__(self, logdir):
        self.logdir = logdir
        self.journal = []

    def __enter__(self):
        self.logdir.mkdir(parents=True, exist_ok=True)
        self.stderr = (self.logdir / 'host.stderr').open('w', encoding='utf-8')
        try:
            self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'ads.testing.traffic_server',
                '--log-dir', str(self.logdir)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.stderr, text=True, encoding='utf-8',
                env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        except BaseException:
            self.stderr.close()
            raise
        self.replies = queue.Queue()
        self.reader = threading.Thread(target=self._read_replies, daemon=True)
        self.reader.start()
        try:
            ready = self._read()
            self.now = ready['now']
            self.http = http.client.HTTPConnection('127.0.0.1', ready['port'], timeout=10)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if hasattr(self, 'http'):
            self.http.close()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.process.stdout.close()
        self.stderr.close()

    def _read_replies(self):
        try:
            for line in self.process.stdout:
                self.replies.put(line)
        finally:
            self.replies.put(None)

    def _read(self):
        try:
            line = self.replies.get(timeout=30)
        except queue.Empty:
            raise TimeoutError('synthetic HTTP host control timed out; see host.stderr') from None
        if not line:
            raise RuntimeError('synthetic HTTP host stopped; see host.stderr')
        return json.loads(line)

    def control(self, op, **kwargs):
        self.process.stdin.write(json.dumps(dict(op=op, **kwargs)) + '\n')
        self.process.stdin.flush()
        return self._read()

    def clock(self, now):
        self.now = now
        self.control('clock', now=now)

    def request(self, method, path, eid, payload=None, remember=True):
        body = json.dumps(payload) if payload is not None else None
        self.http.request(method, path, body, {'Content-Type': 'application/json', 'X-Request-ID': eid})
        response = self.http.getresponse()
        raw = response.read()
        expected = 200 if path == '/ads/v1/serve' else 204 if path == '/ads/v1/impression' else 302
        if response.status != expected or response.getheader('X-Request-ID') != eid:
            raise RuntimeError(f'HTTP response mismatch: {response.status}, expected {expected}')
        if expected == 302 and not (response.getheader('Location') or '').startswith('/ads/landing/'):
            raise RuntimeError('click must redirect to relative internal landing')
        result = json.loads(raw) if raw else None
        if remember:
            self.journal.append((method, path, eid, payload, result))
        return result


def run_traffic(*, run_id, seed=17, requests=10000, invalid_rate=.05,
                fatigue_decay=.2, replay=False, root=ROOT):
    """Run real HTTP traffic and persist truth separately from observed server events.

    Invalid clicks are additional attacks: round(valid * rate / (1-rate)).
    Integer rounding is reported, with types assigned round-robin. Replay sends
    the same requests again to the SAME process and checks budget/idempotent serve.
    """
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}', run_id):
        raise ValueError('run_id must be 1..48 safe ASCII characters')
    if type(requests) is not int or not 1 <= requests <= TOKEN_TTL_MS // STEP_MS:
        raise ValueError(f'requests must be between 1 and {TOKEN_TTL_MS // STEP_MS} for this token window')
    if not 0 <= invalid_rate < 1 or not 0 <= fatigue_decay <= 1:
        raise ValueError('0 <= invalid_rate < 1, 0 <= fatigue_decay <= 1')
    root = Path(root)
    rng = random.Random(seed)
    hidden = hidden_ctrs(synthetic_campaigns(), rng)
    exposures, attacks = Counter(), Counter({kind: 0 for kind in INVALID_TYPES})
    valid_tokens = []
    fatigued, pending, unexpected, beacons, invisible = 0, 0, 0, 0, 0
    prefix = f'syn:{run_id}'
    with _Server(root / 'out/logs/ad_traffic' / run_id) as server:
        base = server.now
        for i in range(requests):
            server.clock(base + i * STEP_MS)
            slot = rng.choice(list(AdSlot))
            outcome = rng.choice(list(GameOutcome)) if slot is not AdSlot.LOBBY_BANNER else None
            session = f'{prefix}:session:{i // 40}'
            rid = f'{prefix}:request:{i}'
            payload = dict(request_id=rid, slot=slot.value, session_pseudonym=session,
                context=dict(preset_id=rng.choice(['base6', 'large8']),
                    outcome=outcome.value if outcome else None, returning=rng.random() < .5, hour=12),
                source='synthetic', load_test=False)
            out = server.request('POST', '/ads/v1/serve', rid + ':serve-event', payload)
            if not out['filled']:
                continue
            creative = out['creative']['creative_id']
            if not creative.startswith('syn:'):
                raise RuntimeError('non-synthetic creative selected')
            ratio, ms = (.4 if rng.random() < .15 else 1.), (800 if rng.random() < .05 else 1200)
            seen = viewable(ratio, ms)
            previous = exposures[(session, creative)]
            probability = click_probability(hidden[(creative, slot, outcome)], previous, fatigue_decay)
            clicked = rng.random() < probability and seen
            lost = rng.random() < .05
            click_first = rng.random() < .2
            if seen:
                fatigued += int(previous > 0)
                exposures[(session, creative)] += 1
            else:
                invisible += 1
            if seen and not lost and not (clicked and click_first):
                server.request('POST', '/ads/v1/impression', rid + ':impression',
                    dict(token=out['serve_token'], visible_ratio=ratio, visible_ms=ms,
                         occurred_at_ms=server.now))
                beacons += 1
            if clicked:
                server.request('GET', out['click_path'], rid + ':click')
                last = server.control('last')
                unexpected += int(last['status'] != 'valid')
                valid_tokens.append((rid, out['serve_token']))
        count = math.floor(len(valid_tokens) * invalid_rate / (1 - invalid_rate) + .5)
        delayed, orphans = [], []
        for i in range(count):
            kind = INVALID_TYPES[i % len(INVALID_TYPES)]
            rid, token = valid_tokens[i % len(valid_tokens)]
            eid = f'{prefix}:attack:{i}:{kind}'
            attacks[kind] += 1
            if kind == 'expired':
                delayed.append((eid, token))
                continue
            if kind == 'no_serve':
                orphan = server.control('orphan', serve_id=f'{prefix}:orphan:{i}',
                                        session=f'{prefix}:orphan-session:{i}')
                token = orphan['token']
                orphans.append((eid, token))
            elif kind == 'bad_signature':
                body, signature = token.split('.')
                token = body + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
            path = '/ads/v1/click?' + urlencode({'t': token})
            server.request('GET', path, eid)
            last = server.control('last')
            if kind == 'no_serve':
                pending += int(last['status'] == 'pending')
                unexpected += int(last['status'] != 'pending')
            else:
                unexpected += int(last['status'] != 'invalid' or last['invalid_reason'] != kind)
        if orphans:
            server.clock(server.now + orphan['grace_ms'])
            for eid, token in orphans:
                server.request('GET', '/ads/v1/click?' + urlencode({'t': token}), eid)
                last = server.control('last')
                unexpected += int(last['status'] != 'invalid' or last['invalid_reason'] != 'no_serve')
        if delayed:
            server.clock(server.now + TOKEN_TTL_MS + 1)
            for eid, token in delayed:
                server.request('GET', '/ads/v1/click?' + urlencode({'t': token}), eid)
                last = server.control('last')
                unexpected += int(last['status'] != 'invalid' or last['invalid_reason'] != 'expired')
        before = server.control('snapshot')
        mismatches = 0
        if replay:
            for method, path, eid, payload, expected in server.journal:
                actual = server.request(method, path, eid, payload, remember=False)
                mismatches += int(actual != expected)
        after = server.control('snapshot')
    events = before.pop('events')
    after.pop('events')
    report = dict(run_id=run_id, seed=seed, requests=requests,
        filled=sum(bool(e.get('filled')) for e in events if e['event_id'].endswith(':serve-event')),
        valid_clicks=len(valid_tokens), invalid_types=dict(attacks),
        requested_invalid_rate=invalid_rate,
        actual_invalid_rate=count / (len(valid_tokens) + count) if valid_tokens else 0.,
        invalid_charges=after['invalid_charges'], unexpected_judgements=unexpected,
        no_serve_pending=pending, implied_by_click=sum(bool(e.get('implied_by_click')) for e in events),
        fatigued_views=fatigued, beacons=beacons, nonviewable=invisible,
        confirmed=before['confirmed'], amount_mw=before['amount_mw'],
        totals_isolated=before['totals_isolated'],
        replayed_requests=len(server.journal) if replay else 0,
        suspect_valid_clicks=sum(e.get('status') == 'valid' and bool(e.get('suspect')) for e in events),
        cpc_charges=sum(bool(e.get('charge_id')) and e['billing'] == 'cpc' for e in events),
        cpm_charges=sum(bool(e.get('charge_id')) and e['billing'] == 'cpm' for e in events),
        replay_new_charges=after['confirmed'] - before['confirmed'],
        replay_new_reservations=after['reservations'] - before['reservations'],
        replay_response_mismatches=mismatches,
        events_sha256=hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest())
    truth = dict(run_id=run_id, seed=seed, fatigue_decay=fatigue_decay,
        fatigue_formula='ctr * (1 - fatigue_decay) ** previous_visible_views',
        invalid_types=dict(attacks), requested_invalid_rate=invalid_rate,
        actual_invalid_rate=report['actual_invalid_rate'],
        ctr={f'{cr}|{sl.value}|{oc.value if oc else "-"}': value
             for (cr, sl, oc), value in hidden.items()})
    truth_path = root / 'out/ad_truth' / f'{run_id}.json'
    truth_path.parent.mkdir(parents=True, exist_ok=True)
    truth_path.write_text(json.dumps(truth, indent=2) + '\n', encoding='utf-8')
    outdir = root / 'out/ad_traffic' / run_id
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
    (outdir / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    """CLI. Nonzero exit for invalid billing, isolation, replay or judgement failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--requests', type=int, default=10000)
    parser.add_argument('--invalid-rate', type=float, default=.05)
    parser.add_argument('--fatigue-decay', type=float, default=.2)
    parser.add_argument('--replay', action='store_true')
    args = parser.parse_args(argv)
    try:
        report = run_traffic(**vars(args))
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        log('ad_traffic_sim', 'error', 'simulation failed', error_type=type(exc).__name__, run_id=args.run_id)
        return 1
    failed = any(report[k] for k in ('invalid_charges', 'unexpected_judgements',
        'replay_new_charges', 'replay_new_reservations', 'replay_response_mismatches')) or not report['totals_isolated']
    log('ad_traffic_sim', 'error' if failed else 'info', 'simulation completed', **report)
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
