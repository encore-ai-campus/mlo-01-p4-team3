"""실제 Mongo·Django 요청 경로와 명시적인 녹화 엔진을 사용하는 격리 부하 시험."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Barrier
import time
import uuid


def percentile(values, fraction):
    """nearest-rank 분위수를 구한다. 빈 표본은 측정 실패다."""
    if not values:
        raise ValueError('no_samples')
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def fixture_service(runtime):
    """가짜 엔진만 주입하고 게임·진척·콘텐츠는 실제 Mongo 포트를 사용한다."""
    from .support import make_service
    service = make_service(repository=runtime.game.repository, progress=runtime.game.progress)
    service.dependencies = runtime.game.dependencies
    service.interactions = None
    service.load_test = True
    return service


def mongo_uri(root):
    """환경 → server/.env → Compose 루트 .env 순으로 비밀 접속값만 읽는다."""
    uri = os.environ.get('PALDO_MONGO_URI')
    if not uri and (root / 'server/.env').is_file():
        from dotenv import dotenv_values
        uri = dotenv_values(root / 'server/.env', encoding='utf-8-sig',
                            interpolate=False).get('PALDO_MONGO_URI')
    if not uri and (root / '.env').is_file():
        from ops.compose.devenv import parse_env
        uri = parse_env((root / '.env').read_text(encoding='utf-8-sig')).get('PALDO_MONGO_URI')
    if not uri:
        raise ValueError('mongo_not_configured')
    return uri


def run(args):
    """사용자별 독립 판을 초당 한 요청으로 진행하고 임시 DB 자원을 정리한다."""
    os.environ['DJANGO_SETTINGS_MODULE'] = 'server.paldo_web.test_settings'
    os.environ['DJANGO_SECRET_KEY'] = secrets.token_urlsafe(48)
    os.environ['PALDO_PUBLIC_ORIGIN'] = 'http://testserver'
    import django
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.utils import timezone
    from engine.testing.fakes import FixtureContentSource, ScriptedGameEngine
    from server.paldo_web.composition import configure_game_service, configure_session_service
    from server.paldo_web.mongo import open_mongo_runtime

    uri = mongo_uri(Path(settings.BASE_DIR))
    prefix = 'b0602_' + uuid.uuid4().hex + '_'
    with TemporaryDirectory(prefix='paldo-api-bench-') as temporary:
        settings.DATABASES['default']['NAME'] = str(Path(temporary) / 'accounts.sqlite3')
        django.setup()
        from django.contrib.auth.models import User
        from django.test import Client
        from server.accounts.models import AccountProfile
        from .support import issue_test_csrf
        runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None, uri=uri, collection_prefix=prefix)
        try:
            runtime.content.insert(FixtureContentSource().load('*'))
            service = fixture_service(runtime)
            configure_game_service(service)
            call_command('migrate', verbosity=0, interactive=False)
            logging.getLogger('paldo.web').disabled = True
            clients = []
            for number in range(args.users):
                user = User.objects.create_user(username=f'bench-{number}')
                AccountProfile.objects.create(user=user, consented_at=timezone.now())
                client = Client(enforce_csrf_checks=True, raise_request_exception=False)
                client.force_login(user)
                clients.append((client, issue_test_csrf(client)))
            barrier = Barrier(args.users)
            steps = service.engine.steps[1:]
            def worker(session):
                client, csrf = session
                samples, statuses, errors, finished = [], {}, 0, 0
                barrier.wait(timeout=30)
                deadline = time.monotonic() + args.minutes * 60
                game, step = None, 0
                try:
                    while time.monotonic() < deadline:
                        started = time.monotonic()
                        if game is None:
                            path = '/games'
                            body = dict(preset_id='base6', session_id='test-session', mudang_variant='f')
                        else:
                            path = f'/games/{game}/commands'
                            body = dict(steps[step]['request'], game_id=game, command_id=str(uuid.uuid4()))
                        response = client.post(path, body, content_type='application/json',
                                               HTTP_ACCEPT='application/json', HTTP_X_CSRFTOKEN=csrf)
                        samples.append((time.monotonic() - started) * 1000)
                        statuses[str(response.status_code)] = statuses.get(str(response.status_code), 0) + 1
                        if response.status_code != 200:
                            errors += 1
                            game, step = None, 0
                        elif game is None:
                            game = response.json()['view']['game_id']
                        elif not response.json()['accepted']:
                            errors += 1
                            game, step = None, 0
                        else:
                            step += 1
                            if step == len(steps):
                                finished += 1
                                game, step = None, 0
                        time.sleep(max(0, min(1 - (time.monotonic() - started), deadline - time.monotonic())))
                    return samples, statuses, errors, finished
                finally:
                    connections.close_all()
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=args.users) as pool:
                results = list(pool.map(worker, clients))
            values = [sample for result in results for sample in result[0]]
            statuses = {}
            for _, counts, _, _ in results:
                for status, count in counts.items():
                    statuses[status] = statuses.get(status, 0) + count
            errors = sum(result[2] for result in results)
            documents = list(runtime.game.repository.collection.find({}, {'outbox': 1}))
            events = [row['envelope'] for doc in documents for row in doc['outbox']]
            if not events or not all(event['load_test'] for event in events):
                raise AssertionError('load_test_not_marked')
            return dict(storage='mongo', transport='django-test-client', engine='scripted',
                        mongo_version=runtime.client.server_info()['version'], users=args.users,
                        minutes=args.minutes, elapsed_seconds=round(time.monotonic()-started, 3),
                        requests=len(values), errors=errors, statuses=statuses,
                        finished_games=sum(result[3] for result in results),
                        p50_ms=round(percentile(values, .5), 3), p95_ms=round(percentile(values, .95), 3),
                        load_test_events=len(events), all_load_test=True)
        finally:
            configure_game_service(None)
            configure_session_service(None)
            try:
                for name in runtime.collection_names:
                    if not name.startswith(prefix) or not prefix.startswith('b0602_'):
                        raise AssertionError('unsafe_cleanup')
                    runtime.database.drop_collection(name)
            finally:
                runtime.close()
                connections.close_all()


def main(argv=None):
    """10명·3분 기본 부하를 실행한다. 오류 또는 p95 1초 초과면 종료 코드 1이다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users', type=int, default=10)
    parser.add_argument('--minutes', type=float, default=3)
    parser.add_argument('--result-name', help='공식 증빙 JSON 이름. 생략하면 표준 출력만 쓴다.')
    parser.add_argument('--compare-asgi', action='store_true', help='B-07-05 실제 HTTP·웹소켓 비교 시험')
    args = parser.parse_args(argv)
    if args.compare_asgi:
        from .asgi_bench import main as compare
        forwarded = ['--users', str(args.users), '--minutes', str(args.minutes)]
        if args.result_name:
            forwarded += ['--result-name', args.result_name]
        try:
            return compare(forwarded)
        except Exception as error:
            print(json.dumps({'error': 'network_benchmark_failed', 'error_type': type(error).__name__}), flush=True)
            return 2
    if not 1 <= args.users <= 100 or not 0 < args.minutes <= 30:
        parser.error('users=1~100, minutes=0 초과 30 이하')
    if args.result_name:
        from tools.evidence_result import NAME_RE
        if not NAME_RE.fullmatch(args.result_name) or args.result_name in ('.', '..'):
            parser.error('invalid_result_name')
    try:
        report = run(args)
        if args.result_name:
            from tools.evidence_result import write_result
            metrics = {key: {'value': report[key], 'unit': unit} for key, unit in
                       [('users', '명'), ('minutes', 'min'), ('requests', '건'), ('errors', '건'),
                        ('p50_ms', 'ms'), ('p95_ms', 'ms'), ('finished_games', '판')]}
            write_result('B-06-02', args.result_name, metrics,
                         f'python -B tools/perf/api_bench.py --users {args.users} --minutes {args.minutes} --result-name {args.result_name}')
        print(json.dumps(report), flush=True)
        return int(report['errors'] > 0 or report['p95_ms'] > 1000)
    except Exception as error:
        print(json.dumps({'error': 'benchmark_failed', 'error_type': type(error).__name__}), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
