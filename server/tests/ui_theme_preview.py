"""공통 UI 검수 전용. 임시 계정 DB·메모리 녹화 판을 실제 Daphne에서 제공한다."""
import argparse
import os
from pathlib import Path
import secrets
import sys
from functools import partial
from tempfile import TemporaryDirectory


def main():
    """운영 .env·저장소에 접속하지 않는 루프백 미리보기를 시작한다."""
    parser = argparse.ArgumentParser(description='공통 UI 검수: 임시 DB·녹화 엔진 전용')
    parser.add_argument('--port', type=int, default=8783)
    parser.add_argument('--font-probe', action='store_true', help='로그인 첫 화면의 로컬 글꼴·로드 계측 활성화')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.update(DJANGO_SETTINGS_MODULE='server.paldo_web.test_settings',
                     PALDO_GAME_UI=os.environ.get('PALDO_GAME_UI', 'vn'),
                     DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
                     PALDO_PUBLIC_ORIGIN=f'http://127.0.0.1:{args.port}',
                     PALDO_GAME_SERVICE_FACTORY='', PALDO_ADS_INTERNAL_URL='', PALDO_MONGO_URI='')
    import django
    from django.conf import settings
    with TemporaryDirectory(prefix='paldo-ui-theme-') as directory:
        settings.DATABASES['default']['NAME'] = str(Path(directory) / 'accounts.sqlite3')
        settings.ALLOWED_HOSTS = ['127.0.0.1']
        # 별도 worktree/포트의 검수 화면이 서로의 로그인 세션을 덮어쓰지 않는다.
        settings.SESSION_COOKIE_NAME = f'paldo_ui_preview_{args.port}'
        if args.font_probe:
            settings.MIDDLEWARE = [*settings.MIDDLEWARE, 'server.tests.ui_font_probe.FontProbe']
        django.setup()
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from django.db import connections
        from django.utils import timezone
        from daphne.cli import CommandLineInterface
        from engine.domain.model import ENGINE_VERSION
        from server.accounts.models import AccountProfile
        from server.app.open_start import StartPolicy, StartVersions
        from server.app.test_sessions import TestSessionService
        from server.paldo_web.composition import configure_game_service, configure_session_service
        from server.tests.preset_fixture import PresetRecordingEngine
        from server.tests.session_store import MemorySessionStore
        from server.tests.support import make_service
        try:
            call_command('migrate', verbosity=0, interactive=False)
            user = User.objects.create_user('preview', password='Preview123!')
            profile = AccountProfile.objects.create(user=user, consented_at=timezone.now())
            service = make_service()
            service.engine = PresetRecordingEngine()
            content_version = service.dependencies(None).content.content_version
            sessions = TestSessionService(MemorySessionStore())
            sessions.create('ui-theme', [{'preset_id': 'base6', 'seed': 7},
                                        {'preset_id': 'large8', 'seed': 8}],
                            [str(profile.player_pseudonym)],
                            {'rule_version': 'test-rule', 'engine_version': ENGINE_VERSION,
                             'content_version': content_version})
            service.prepare_start = StartPolicy(partial(sessions.prepare, load_game=service.repository.load),
                lambda: StartVersions('test-rule', ENGINE_VERSION, content_version), service.repository.load)
            configure_game_service(service)
            configure_session_service(sessions)
            print(f'UI preview: http://127.0.0.1:{args.port}/', flush=True)
            print('Local-only: preview / Preview123!; temporary SQLite + recorded games.', flush=True)
            CommandLineInterface().run(['-b', '127.0.0.1', '-p', str(args.port),
                                        'server.paldo_web.asgi:application'])
        finally:
            configure_game_service(None)
            configure_session_service(None)
            connections.close_all()


if __name__ == '__main__':
    main()
