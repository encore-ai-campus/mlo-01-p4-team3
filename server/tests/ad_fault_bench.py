"""B-08-03 HTTP 광고 장애 측정. 로컬 대역과 실제 D 인수 서버 결과를 구분한다."""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from .asgi_bench import beacon_load, run_case
from .api_bench import mongo_uri


class _AdTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag == 'section':
            self.token = dict(attrs).get('data-ad-token') or self.token


def _ad_page(opener, origin, cookie, slot):
    with opener.open(Request(origin + '/ads/slot/' + slot, headers={'Cookie': cookie}), timeout=5) as response:
        if response.status != 200:
            raise RuntimeError('slot_failed')
        text = response.read().decode('utf-8')
    parser = _AdTokenParser()
    parser.feed(text)
    return parser.token


def http_beacons(origin, cookie, stop, ready):
    """serve 토큰으로 인증 웹소켓을 중계한다. 광고 실패 시에도 게임 연결은 검사한다."""
    try:
        token = _ad_page(build_opener(), origin, cookie, 'lobby_banner')
    except OSError:
        token = None
    return beacon_load(origin, cookie, stop, ready, token=token or 'b0803.unavailable')


def click_outcome(origin, final_url):
    """클릭 뒤 도착한 같은 출처 경로를 랜딩·실패 안내·홈·기타로 나눈다(원인 계측, B-08-03 E 보완)."""
    path = urlsplit(final_url).path
    if path.startswith('/ads/landing/'):
        return 'landing'
    if path == '/ads/click':
        return 'unavailable'
    return 'home' if path == '/' else 'other'


def http_ads_and_clicks(origin, cookie, stop):
    """세 지면·클릭·랜딩을 게임 출처로만 요청하며 실패 시 안내·홈 복귀도 정상으로 센다.

    클릭마다 도착 경로(랜딩·실패 안내·홈)와 왕복 시간을 남겨, 장애 때 작업 부하가 바뀌는지 본다.
    """
    opener = build_opener()
    counts = dict(requests=0, errors=0, clicks=0, click_errors=0,
                  outcomes=dict(landing=0, unavailable=0, home=0, other=0), click_ms=[])
    slots = ('lobby_banner', 'result_banner', 'review_side')
    while not stop.is_set():
        started = time.monotonic()
        token = None
        try:
            token = _ad_page(opener, origin, cookie, slots[counts['requests'] % 3])
        except (OSError, ValueError):
            counts['errors'] += 1
        counts['requests'] += 1
        click_started = time.monotonic()
        try:
            with opener.open(Request(origin + '/ads/click?' + urlencode({'t': token or 'b0803.unavailable'}),
                                     headers={'Cookie': cookie}), timeout=5) as response:
                if response.status != 200 or urlsplit(response.url).netloc != urlsplit(origin).netloc:
                    counts['click_errors'] += 1
                response.read()
                counts['outcomes'][click_outcome(origin, response.url)] += 1
        except OSError:
            counts['click_errors'] += 1
        counts['click_ms'].append((time.monotonic() - click_started) * 1000)
        counts['clicks'] += 1
        stop.wait(max(0, 1 - (time.monotonic() - started)))
    return counts


def assess_case(row, baseline=None):
    """오류 증가·p95 증가·중계 생존을 판정한다. 짧은 시험은 별도 표시한다."""
    ok = (row['errors'] == 0 and row['finished_games'] > 0 and row['ad_errors'] == 0
          and row['beacon_acks'] > 0 and row['beacon_errors'] == 0
          and row['click_requests'] > 0 and row['click_errors'] == 0)
    if baseline is None:
        return ok and row['beacon_relayed'] > 0
    row['p95_increase_ms'] = round(row['p95_ms'] - baseline['p95_ms'], 3)
    row['error_rate_increase'] = row['errors'] / row['requests'] - baseline['errors'] / baseline['requests']
    return ok and row['p95_increase_ms'] <= 50 and row['error_rate_increase'] <= 0 and row['beacon_failed'] > 0


def main(argv=None):
    """같은 api_bench 명령 부하로 기준선과 장애 4종을 측정한다. D 제어 API는 만들지 않는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users', type=int, default=10)
    parser.add_argument('--minutes', type=float, default=5)
    parser.add_argument('--local-fixture', action='store_true', help='B loopback 대역의 5개 구간을 자동 측정')
    parser.add_argument('--scenario', choices=('normal', 'stopped', 'unresponsive', 'delay', 'error'))
    parser.add_argument('--ads-url-env', default='PALDO_ADS_INTERNAL_URL', help='D 내부 출처가 있는 환경 변수 이름')
    parser.add_argument('--baseline', type=Path, help='동일 조건 normal 측정 JSON')
    parser.add_argument('--output', type=Path, default=Path('out/handoff/b0803-ad-fault.json'))
    args = parser.parse_args(argv)
    if not 1 <= args.users <= 60 or not 0 < args.minutes <= 10:
        parser.error('users=1..60, minutes=0 초과 10 이하')
    if not args.local_fixture and (not args.scenario or not os.environ.get(args.ads_url_env)):
        parser.error('실제 시험은 scenario와 내부 출처 환경 변수가 필요합니다')
    if not args.local_fixture and args.scenario != 'normal' and not args.baseline:
        parser.error('장애 시험은 같은 조건의 baseline이 필요합니다')
    root = Path(__file__).resolve().parents[2]
    uri = mongo_uri(root)
    (root / 'out/handoff').mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    baseline = None
    if args.baseline:
        prior = json.loads(args.baseline.read_text(encoding='utf-8'))
        if prior.get('mode') != 'd-handoff' or prior['users'] != args.users or prior['minutes'] != args.minutes:
            parser.error('같은 환경 구분·사용자 수·시간의 D 기준선이 필요합니다')
        baseline = next(r for r in prior['rows'] if r['scenario'] == 'normal')
        if not assess_case(baseline):
            parser.error('실패한 기준선으로 증가분을 판정할 수 없습니다')
    with TemporaryDirectory(prefix='b0803-', dir=root / 'out/handoff') as temporary:
        folder = Path(temporary)
        os.environ.update(DJANGO_SETTINGS_MODULE='server.tests.asgi_bench_settings',
            PALDO_BENCH_AUTH_DB=str(folder / 'auth.sqlite3'), DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
            PALDO_PUBLIC_ORIGIN='http://127.0.0.1:8765')
        import django
        django.setup()
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from django.db import connections
        from django.test import Client
        from django.utils import timezone as django_timezone
        from server.accounts.models import AccountProfile
        from .support import make_service
        from .ad_http_host import LocalAdHost
        call_command('migrate', verbosity=0, interactive=False)
        users = []
        for number in range(args.users):
            user = User.objects.create_user(username=f'b0803-{number}')
            AccountProfile.objects.create(user=user, consented_at=django_timezone.now())
            client = Client()
            client.force_login(user)
            users.append('sessionid=' + client.cookies['sessionid'].value)
        steps = make_service().engine.steps[1:]
        rows, checks = [], []
        try:
            scenarios = ('normal', 'stopped', 'unresponsive', 'delay', 'error') if args.local_fixture else (args.scenario,)
            for scenario in scenarios:
                host = LocalAdHost() if args.local_fixture else None
                url = host.url if host else os.environ[args.ads_url_env]
                if host:
                    host.mode = scenario
                    if scenario == 'stopped':
                        host.close()
                try:
                    print(json.dumps({'starting_case': scenario}), flush=True)
                    row = run_case(args, 'http_' + scenario, 'daphne', 'normal', True, uri, users, steps, folder,
                        extra_env={'PALDO_BENCH_HTTP_ADS_URL': url}, ad_worker=http_ads_and_clicks,
                        beacon_worker=http_beacons)
                    row['scenario'] = scenario
                    if host:
                        serves = [c for c in host.calls if c['path'] == '/ads/v1/serve']
                        row['http_serve_requests'] = len(serves)
                        row['http_all_load_test'] = all(c['body']['load_test'] is True for c in serves)
                        if not row['http_all_load_test']:
                            raise AssertionError('http_load_test_mark_missing')
                    checks.append(assess_case(row, baseline))
                    if scenario == 'normal':
                        baseline = row
                    rows.append(row)
                finally:
                    if host and scenario != 'stopped':
                        host.close()
            report = dict(mode='local-http-fixture' if args.local_fixture else 'd-handoff',
                users=args.users, minutes=args.minutes, measured_at=datetime.now(timezone.utc).isoformat(),
                formal_duration=args.minutes >= 5, passed=all(checks), task_complete=False, rows=rows)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({'passed': report['passed'], 'mode': report['mode'],
                              'formal_duration': report['formal_duration'], 'task_complete': False}), flush=True)
            return 0 if report['passed'] else 1
        finally:
            connections.close_all()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error': 'ad_fault_benchmark_failed', 'error_type': type(error).__name__}), flush=True)
        raise SystemExit(2)
