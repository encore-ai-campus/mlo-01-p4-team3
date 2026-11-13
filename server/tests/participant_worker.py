"""B-07-02 시험의 새 프로세스: 기존 Mongo 판을 새 인증 세션으로 조회·재전송한다."""
import json
import logging
import os
from pathlib import Path
import re
import sys
import uuid


def main():
    """고유 시험 접두어만 허용한다. 연결 비밀·쿠키는 입력/출력에 포함하지 않는다."""
    request = json.load(sys.stdin)
    prefix = request['prefix']
    if not re.fullmatch(r'b0702_[0-9a-f]{32}_', prefix):
        raise ValueError('invalid_test_prefix')
    gid = str(uuid.UUID(request['game_id']))
    player = str(uuid.UUID(request['player']))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'server.paldo_web.test_settings'
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.contrib.auth.models import User
    from django.test import Client
    from django.utils import timezone
    from engine.testing.fakes import FakeIntentParser, ScriptedGameEngine
    from server.accounts.models import AccountProfile
    from server.infra.game_factory import attach_interactions, INTERACTIONS
    from server.infra.interaction_store import MongoInteractionStore
    from server.paldo_web.composition import configure_game_service
    from server.paldo_web.mongo import open_mongo_runtime
    from .api_bench import mongo_uri
    from .support import make_service, issue_test_csrf
    runtime = open_mongo_runtime(engine=ScriptedGameEngine(), suspicion=None,
                                 uri=mongo_uri(Path(settings.BASE_DIR)), collection_prefix=prefix)
    try:
        service = make_service(repository=runtime.game.repository, progress=runtime.game.progress)
        service.dependencies = runtime.game.dependencies
        service.load_test = True
        attach_interactions(service, MongoInteractionStore(runtime.database[prefix + INTERACTIONS]), FakeIntentParser())
        configure_game_service(service)
        call_command('migrate', verbosity=0, interactive=False)
        user = User.objects.create_user(username='restart-participant')
        AccountProfile.objects.create(user=user, player_pseudonym=player, consented_at=timezone.now())
        client = Client(enforce_csrf_checks=True, raise_request_exception=False)
        client.force_login(user)
        csrf = issue_test_csrf(client)
        logging.getLogger('paldo.web').disabled = True
        action = request['action']
        path = f'/games/{gid}'
        if action == 'view':
            response = client.get(path, HTTP_ACCEPT='application/json')
        elif action in ('notebook', 'survey'):
            response = getattr(client, 'put' if action == 'notebook' else 'post')(
                path + '/' + action, request['body'], content_type='application/json',
                HTTP_ACCEPT='application/json', HTTP_X_CSRFTOKEN=csrf)
        else:
            raise ValueError('invalid_test_action')
        print(json.dumps({'status': response.status_code,
                          'data': response.json() if response.content else None}))
    finally:
        runtime.close()


if __name__ == '__main__':
    main()
