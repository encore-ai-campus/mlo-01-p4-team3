"""D-06-03 acceptance measurements: original CTR, 0/5/20%, replay and reproduction."""
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

from tools.ad_traffic_sim import ROOT, run_traffic
from tools.evidence_result import write_result
from tools.jsonlog import log


def main(argv=None):
    """Run independent HTTP hosts, repeat seed/run 5%, and write checked evidence metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=10000)
    args = parser.parse_args(argv)
    def measure(rate):
        return run_traffic(run_id=f'd0603-r{rate}', seed=10, requests=args.requests,
                           invalid_rate=rate / 100, replay=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(measure, (0, 5, 20)))
    repeated = measure(5)
    metrics = {}
    for rate, report in zip((0, 5, 20), reports):
        run = report['run_id']
        events = [json.loads(line) for line in (ROOT / 'out/ad_traffic' / run / 'events.jsonl').read_text().splitlines()]
        clicks = [e for e in events if 'suspect' in e]
        observed = {kind: sum(e['invalid_reason'] == kind for e in clicks)
                    for kind in report['invalid_types']}
        assert observed == report['invalid_types'], (run, observed)
        assert sum(e['status'] == 'valid' for e in clicks) == report['valid_clicks']
        assert report['valid_clicks'] > 0 and report['implied_by_click'] > 0
        assert report['cpc_charges'] > 0 and report['cpm_charges'] > 0
        assert report['totals_isolated']
        assert all(e['source'] == 'synthetic' and run in e['event_id'] for e in events)
        assert len({e['event_id'] for e in events}) == len(events)
        assert all(not e.get(key) or e[key].startswith('syn:')
                   for e in events for key in ('campaign_id', 'creative_id'))
        assert all(not e.get(key) or run in e[key]
                   for e in events for key in ('serve_id', 'session_pseudonym'))
        assert report['no_serve_pending'] == observed['no_serve']
        assert all(e.get('implied_by_click') or e['visible_ratio'] >= .5 and e['visible_ms'] >= 1000
                   for e in events if 'visible_ratio' in e)
        for key in ('invalid_charges', 'unexpected_judgements', 'replay_new_charges',
                    'replay_new_reservations', 'replay_response_mismatches'):
            assert report[key] == 0, (run, key, report[key])
        if rate:
            assert all(observed.values()), 'increase --requests to cover all invalid types'
            assert abs(report['actual_invalid_rate'] - rate / 100) < .01
        for key in ('requests', 'filled', 'valid_clicks', 'invalid_charges', 'implied_by_click',
                    'fatigued_views', 'nonviewable', 'beacons', 'replay_new_charges',
                    'replay_new_reservations', 'replay_response_mismatches', 'replayed_requests',
                    'no_serve_pending', 'cpc_charges', 'cpm_charges', 'suspect_valid_clicks'):
            metrics[f'r{rate}_{key}'] = {'value': report[key], 'unit': '건'}
        metrics[f'r{rate}_actual_invalid_rate'] = {'value': report['actual_invalid_rate'], 'unit': ''}
        for kind, value in observed.items():
            metrics[f'r{rate}_{kind}'] = {'value': value, 'unit': '건'}
    assert reports[1] == repeated, 'same seed/run/config must reproduce all observations'
    metrics['reproduction_mismatches'] = {'value': int(reports[1] != repeated), 'unit': '건'}
    digest = hashlib.sha256()
    paths = sorted((ROOT / 'ads').rglob('*.py')) + [ROOT / p for p in
        ('tools/ad_traffic_sim.py', 'tools/gen_synthetic_ads.py', 'tools/build_ads_fixtures.py',
         'tools/perf/ad_traffic_bench.py', 'fixtures/ads/campaigns.json')]
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    verification = dict(reports=reports, reproduced=reports[1] == repeated, source_sha256=digest.hexdigest())
    (ROOT / 'out/ad_traffic/verification.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
    source = f'python -m tools.perf.ad_traffic_bench --requests {args.requests}'
    result = write_result('D-06-03', 'traffic', metrics, source)
    log('ad_traffic_bench', 'info', 'acceptance passed', result=str(result.relative_to(ROOT)),
        source_sha256=digest.hexdigest(), reproduction_mismatches=0)
    for report in reports:
        log('ad_traffic_bench', 'info', 'scenario passed', **report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
