"""Isolated synthetic HTTP test host; never imported by production settings/URLs.

Control (clock, orphan token, observations) uses stdin/stdout, not HTTP routes.
The original Django WSGI application handles all serve/impression/click requests.
"""
import argparse
import io
import json
import logging
import os
import secrets
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import WSGIRequestHandler, make_server

from ads.app.serve import AdService, CTR_VERSION, POLICY_VERSION
from ads.domain.events import AdClickEvent, AdImpressionEvent, AdServeEvent
from ads.domain.model import AD_CONTRACT_VERSION, AdSlot, BillingType, ChargeState, ServeClaims
from ads.domain.rules import GRACE_MS, JUDGE_VERSION, TOKEN_TTL_MS, charge_id, sign_token, verify_token
from tools.gen_synthetic_ads import synthetic_campaigns
from tools.build_ads_fixtures import slot_policies

BASE_MS = 1793923200000


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class _Host:
    def __init__(self, application):
        self.now = BASE_MS
        self.application = application
        self.service = AdService(synthetic_campaigns, {p.slot: p for p in slot_policies()},
                                 secrets.token_bytes(32), clock=lambda: self.now)
        self.events, self.observations = {}, []

    def __call__(self, environ, start_response):
        path = environ['PATH_INFO']
        payload = {}
        if environ.get('CONTENT_LENGTH'):
            body = environ['wsgi.input'].read(int(environ['CONTENT_LENGTH']))
            environ['wsgi.input'] = io.BytesIO(body)
            payload = json.loads(body)
        response = self.application(environ, start_response)
        eid = environ.get('HTTP_X_REQUEST_ID', '')
        with self.service.lock:
            if path == '/ads/v1/serve':
                # Body request_id is repeated in the correlation header, plus :serve-event.
                rid = eid.removesuffix(':serve-event')
                out = self.service.responses.get(rid)
                if out is not None:
                    claims, _ = verify_token(out.serve_token, self.service.key, self.now) if out.filled else (None, None)
                    rec = self.service.state.serves.get(claims.serve_id) if claims else None
                    auction = self.service.auctions[rid]
                    req = self.service.requests[rid]
                    base = self._base(eid, rec)
                    base.update(request_id=rid, slot=req['slot'], session_pseudonym=req['session_pseudonym'])
                    event = AdServeEvent(**base, filled=out.filled,
                        candidates=self.service.candidates[rid], threshold_ecpm_mw=auction.threshold_ecpm_mw,
                        rank_ctr=rec.rank_ctr if rec else 0., capped=auction.capped,
                        reserved_charge_id=charge_id(rec.serve_id, rec.billing) if rec else None,
                        reserved_mw=self.service.book.rows[charge_id(rec.serve_id, rec.billing)].amount_mw if rec else 0,
                        context=req['context'])
                    self._remember(event)
            elif path in ('/ads/v1/impression', '/ads/v1/click'):
                result = self.service.last_result
                j, rec = result.judgement, result.record
                base = self._base(eid, rec)
                if rec is None and path.endswith('click'):
                    token = parse_qs(environ.get('QUERY_STRING', '')).get('t', [''])[0]
                    claims, _ = verify_token(token, self.service.key, self.now)
                    if claims:
                        base.update(serve_id=claims.serve_id, slot=claims.slot,
                                    session_pseudonym=claims.session_pseudonym)
                fields = dict(status=j.status, invalid_reason=j.reason, judge_version=JUDGE_VERSION,
                              charge_id=result.charge_id, charge_mw=result.charge_mw)
                if path.endswith('click'):
                    # An implied CPM impression owns the charge, not the click row.
                    click_fields = fields | ({'charge_id': None, 'charge_mw': 0}
                        if rec and rec.billing is BillingType.CPM and j.implied_impression else {})
                    event = AdClickEvent(**base, **click_fields, suspect=j.suspect,
                                         redirect_path=result.redirect_path)
                    self._remember(event)
                    if j.implied_impression:
                        imp_fields = fields | ({'charge_id': None, 'charge_mw': 0}
                            if rec.billing is BillingType.CPC else {})
                        self._remember(AdImpressionEvent(**(base | {'event_id': eid + ':implied'}),
                            **imp_fields, implied_by_click=True))
                else:
                    event = AdImpressionEvent(**base, **fields,
                        visible_ratio=payload['visible_ratio'], visible_ms=payload['visible_ms'])
                    self._remember(event)
        return response

    def _base(self, eid, rec):
        return dict(event_id=eid, request_id=rec.request_id if rec else eid,
            serve_id=rec.serve_id if rec else None, occurred_at_ms=self.now, received_at_ms=self.now,
            slot=rec.slot if rec else None, campaign_id=rec.campaign_id if rec else None,
            creative_id=rec.creative_id if rec else None,
            session_pseudonym=rec.session_pseudonym if rec else None, source='synthetic',
            policy_version=POLICY_VERSION, ctr_version=CTR_VERSION, contract_version=AD_CONTRACT_VERSION,
            billing=rec.billing if rec else None, price_mw=rec.price_mw if rec else 0)

    def _remember(self, event):
        row = asdict(event)
        self.observations.append(row)
        previous = self.events.get(event.event_id)
        if previous is None or previous.get('status') == 'pending':
            self.events[event.event_id] = row

    def control(self, command):
        """Test-only control plane. No signing key or raw token is persisted."""
        with self.service.lock:
            op = command['op']
            if op == 'clock':
                if command['now'] < self.now:
                    raise ValueError('clock must be monotonic')
                self.now = command['now']
                return {}
            if op == 'orphan':
                claims = ServeClaims(command['serve_id'], command['session'], AdSlot.LOBBY_BANNER,
                                     self.now, self.now + TOKEN_TTL_MS)
                return {'token': sign_token(claims, self.service.key), 'grace_ms': GRACE_MS}
            if op == 'last':
                return self.observations[-1] if self.observations else {}
            if op == 'snapshot':
                rows = list(self.service.book.rows.values())
                return dict(events=list(self.events.values()), reservations=len(rows),
                    confirmed=sum(r.state is ChargeState.CONFIRMED for r in rows),
                    amount_mw=sum(r.amount_mw for r in rows if r.state is ChargeState.CONFIRMED),
                    invalid_charges=sum(1 for e in self.observations
                        if e.get('status') in ('invalid', 'pending') and (e.get('charge_id') or e.get('charge_mw'))),
                    totals_isolated=all(cid.startswith('syn:') for cid, _ in self.service.book.totals))
            raise ValueError('unknown control operation')


def main():
    """Start a temporary loopback WSGI server; exit when its owning client's stdin closes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-dir', type=Path, required=True)
    args = parser.parse_args()
    os.environ['DJANGO_SETTINGS_MODULE'] = 'ads.project.settings'
    os.environ['PALDO_ADS_INTERNAL_URL'] = 'http://127.0.0.1'
    os.environ['DJANGO_SECRET_KEY'] = secrets.token_hex(32)
    os.environ['PALDO_ADS_TOKEN_KEY'] = secrets.token_hex(32)
    os.environ['PALDO_LOG_DIR'] = str(args.log_dir)
    from django.core.wsgi import get_wsgi_application
    application = get_wsgi_application()
    logger = logging.getLogger('paldo.ads')
    logger.handlers[:] = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    host = _Host(application)
    from ads.web import views
    views.get_service = lambda: host.service
    with make_server('127.0.0.1', 0, host, handler_class=_QuietHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({'port': server.server_port, 'now': BASE_MS}), flush=True)
        try:
            for line in sys.stdin:
                result = host.control(json.loads(line))
                print(json.dumps(result, default=str), flush=True)
        finally:
            server.shutdown()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
