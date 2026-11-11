"""B-08-02 공개 8인 녹화 화면의 Django 전체 HTML 렌더 시간을 측정한다."""
import os
from statistics import median
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4


def main():
    """임시 계정 DB·메모리 저장소에서 15화면을 10회씩 렌더한다. 브라우저 시간은 제외한다."""
    os.environ['DJANGO_SETTINGS_MODULE'] = 'server.paldo_web.test_settings'
    from django.conf import settings
    if settings.configured:
        raise RuntimeError('standalone_test_process_required')
    if settings.DATABASES['default'] != {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}:
        raise RuntimeError('in_memory_test_database_required')
    import django
    django.setup()
    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.test import Client
    from server.accounts.models import AccountProfile
    from django.utils import timezone
    from server.paldo_web.composition import configure_game_service
    from server.tests.preset_fixture import make_preset_service
    from tools.evidence_result import write_result
    call_command('migrate', verbosity=0)
    user = User.objects.create_user(username='render-benchmark')
    AccountProfile.objects.create(user=user, consented_at=timezone.now())
    client = Client()
    client.force_login(user)
    service = make_preset_service()
    configure_game_service(service)
    samples = []
    try:
        with patch('server.app.open_start.open_preset_ids', return_value=['base6', 'large8']):
            start = client.post('/games', {'preset_id': 'large8', 'mudang_variant': 'f', 'start_token': str(uuid4())},
                                content_type='application/json', HTTP_ACCEPT='application/json')
        assert start.status_code == 200
        gid = start.json()['view']['game_id']
        frames = service.engine.recordings['large8'].steps
        for index, frame in enumerate(frames):
            if index:
                result = client.post('/games/' + gid + '/commands',
                    dict(frame['request'], game_id=gid, command_id=str(uuid4())),
                    content_type='application/json', HTTP_ACCEPT='application/json')
                assert result.status_code == 200
            for _ in range(10):
                before = perf_counter()
                response = client.get('/games/' + gid, HTTP_ACCEPT='text/html')
                samples.append((perf_counter() - before) * 1000)
                assert response.status_code == 200
        metrics = {'screen_p50': {'value': round(median(samples), 3), 'unit': 'ms'},
                   'screen_p95': {'value': round(sorted(samples)[int(len(samples) * .95) - 1], 3), 'unit': 'ms'},
                   'screens': {'value': len(frames), 'unit': ''},
                   'renders': {'value': len(samples), 'unit': ''}}
        print(write_result('B-08-02', 'render_large8', metrics,
                           'python -B -m server.tests.preset_render_bench'))
        print(metrics)
    finally:
        configure_game_service(None)


if __name__ == '__main__':
    main()
