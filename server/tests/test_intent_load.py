"""B-09-02 해석 혼합 부하의 수명주기·오류 집계와 설정 교체 회귀."""
import json
from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase as UnitTestCase
from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from server.paldo_web.composition import configure_game_service
from server.tests.intent_load import probe_intent, should_probe
from server.tests.support import make_service, install_test_profiles


class IntentProbeTests(UnitTestCase):
    """오류가 있는 해석·취소를 정상 부하로 세지 않는다."""

    def test_parse_and_cancel_failures_are_counted(self):
        """해석 실패·버전 변경·취소 실패를 오류로 반환한다."""
        view = dict(game_id='test', state_version=7)
        for status, parsed in ((503, {}), (200, {}),
                (200, dict(attempt_id='a', state_version=8, needs_confirmation=True))):
            send = Mock(return_value=(status, parsed))
            self.assertEqual(probe_intent(view, send), 1)
            self.assertEqual(send.call_count, 1)
        parsed = dict(attempt_id='a', state_version=7, needs_confirmation=True)
        for status, cancelled in ((409, {}), (200, dict(state_version=8, needs_confirmation=False)),
                                  (200, dict(state_version=7, needs_confirmation=True))):
            self.assertEqual(probe_intent(view, Mock(side_effect=[(200, parsed), (status, cancelled)])), 1)

    def test_interval_and_phase_restrictions(self):
        """말하기 단계와 지정 간격에서만 해석을 추가한다."""
        for phase in ('morning', 'night', 'ended'):
            self.assertFalse(should_probe(dict(phase=phase), 2, 2))
        self.assertTrue(should_probe(dict(phase='round1'), 2, 2))
        self.assertFalse(should_probe(dict(phase='round1'), 1, 2))
        self.assertFalse(should_probe(dict(phase='round1'), 1, 0))

    def test_invalid_interval_fails_before_database_access(self):
        """잘못된 CLI 설정은 DB 접속 전에 차단한다."""
        from server.tests import multiproc_bench
        with patch.object(multiproc_bench, 'mongo_uri') as uri, redirect_stderr(StringIO()):
            for interval in ('-1', '101'):
                with self.assertRaises(SystemExit):
                    multiproc_bench.main(['--intent-every', interval])
            with self.assertRaises(SystemExit):
                multiproc_bench.main(['--intent-every', '1', '--intent-factory', 'not a module'])
            uri.assert_not_called()


class IntentFactoryReceiveTests(TestCase):
    """기존 설정 한 줄로 해석기를 교체해도 HTTP 해석·취소가 행동을 소비하지 않는다."""

    def test_factory_switch_keeps_recorded_command_sequence(self):
        """가짜·기준선 설정에서 해석·취소 후 같은 녹화 명령이 끝까지 수락된다."""
        for factory in ('engine.testing.fakes.FakeIntentParser', 'intent.parser.BaselineIntentParser'):
            with self.subTest(factory=factory), override_settings(INTENT_PARSER_FACTORY=factory):
                service = make_service()
                configure_game_service(service)
                self.addCleanup(configure_game_service, None)
                user = User.objects.create_user(username='load-' + str(User.objects.count()))
                profile = install_test_profiles(self)(user)
                self.client.force_login(user)
                def send(route, path, body):
                    response = self.client.post(path, json.dumps(body), content_type='application/json',
                                                HTTP_ACCEPT='application/json')
                    return response.status_code, response.json()
                status, output = send('start', '/games', dict(preset_id='base6', session_id='test-session', mudang_variant='f'))
                self.assertEqual(status, 200)
                view = output['view']
                from uuid import uuid4
                for step in service.engine.steps[1:]:
                    if should_probe(view, 1, 1):
                        self.assertEqual(probe_intent(view, send), 0)
                        state, _ = service.repository.load(view['game_id'])
                        self.assertEqual(state.state_version, view['state_version'])
                    body = dict(step['request'], game_id=view['game_id'], command_id=str(uuid4()))
                    status, output = send('command', '/games/' + view['game_id'] + '/commands', body)
                    self.assertEqual(status, 200)
                    self.assertTrue(output['accepted'])
                    view = output['view']
                self.assertEqual(view['phase'], 'ended')
                self.assertEqual(service.interactions.parser.parser.__class__.__module__, factory.rsplit('.', 1)[0])
                events = service.repository.unpublished(view['game_id'])
                self.assertTrue(any(e.event_type == 'intent_attempt' for e in events))
