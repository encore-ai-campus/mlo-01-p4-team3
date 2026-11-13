"""D BERT 어댑터의 B 조립·HTTP 계약 시험. 가중치 로더 대역과 실물 시험을 구분한다."""
import os
from unittest import skipUnless
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from intent.parser import BaselineIntentParser
from server.paldo_web.composition import configure_game_service
from server.paldo_web.intent import ObservedIntentParser, make_bert_parser
from server.tests import test_utterance_ui as screens
from server.tests.support import make_service

FACTORY = 'server.paldo_web.intent.make_bert_parser'


class BertFactoryBoundary(SimpleTestCase):
    """D 체크포인트 검증을 우회하거나 실패를 기준선 성공으로 숨기지 않는다."""

    def test_empty_path_fails_before_loader(self):
        """경로가 없으면 모델을 만들지 않는다."""
        with override_settings(INTENT_BERT_RUN_DIR=''), patch('intent.bert.BertIntentParser') as parser:
            with self.assertRaises(ImproperlyConfigured):
                make_bert_parser()
            parser.assert_not_called()

    def test_bad_checkpoint_does_not_echo_path_or_fall_back(self):
        """D 로더의 누락·변조 오류는 민감한 원문 없이 설정 오류로 끝낸다."""
        with override_settings(INTENT_BERT_RUN_DIR='private-model-path'):
            with patch('intent.bert.BertIntentParser', side_effect=ValueError('private-model-path')):
                with self.assertRaises(ImproperlyConfigured) as caught:
                    make_bert_parser()
                self.assertNotIn('private-model-path', str(caught.exception))
            with self.assertRaises(ImproperlyConfigured):
                make_bert_parser()

    def test_default_assembly_does_not_load_bert(self):
        """기존 기본값은 주입된 해석기를 유지하고 BERT를 활성화하지 않는다."""
        service = make_service()
        original = service.interactions.parser
        with override_settings(INTENT_PARSER_FACTORY=''), patch('intent.bert.BertIntentParser') as parser:
            configure_game_service(service)
            self.addCleanup(configure_game_service, None)
            self.assertIs(service.interactions.parser.parser, original)
            parser.assert_not_called()


@override_settings(INTENT_PARSER_FACTORY=FACTORY, INTENT_BERT_RUN_DIR='loader-double')
class BertAdapterScreens(screens.BaselineUtteranceScreens):
    """D 어댑터와 기존 B-07-01 HTTP 시험을 연결한다. 분류기 로드는 기준선 대역이다."""

    def setUp(self):
        """D 어댑터 클래스는 그대로 쓰고 가중치 로더만 실제 기준선 분류기로 대체한다."""
        self.classifier = BaselineIntentParser().model
        self.classifier.model_version = 'bert-receive-classifier-double'
        self.loader = self.enterContext(patch('intent.bert.load_classifier', return_value=self.classifier))
        super().setUp()

    def test_d_adapter_loaded_once_and_wrapped(self):
        """공유 서비스의 중복 조립은 모델을 다시 로드하지 않고 계측을 보존한다."""
        from intent.bert import BertIntentParser
        self.assertIsInstance(self.service.interactions.parser, ObservedIntentParser)
        self.assertIsInstance(self.service.interactions.parser.parser, BertIntentParser)
        configure_game_service(self.service)
        self.loader.assert_called_once_with('loader-double')

    def test_runtime_failure_is_unparseable_without_consumption(self):
        """추론 실패는 재입력 안내·빈 후보로 끝나고 판 버전과 원문 비노출을 유지한다."""
        from engine.domain.model import Phase
        self.at_phase(Phase.ROUND1)
        with patch.object(self.classifier, 'predict', side_effect=ValueError('private inference error')):
            response = self.parse(text='훈장이 수상하다')
        self.assertEqual(response.context['failure'], 'unparseable')
        self.assertFalse(response.context['cards'])
        self.assertContains(response, '다시 입력')
        self.assertNotContains(response, 'private inference error')
        state, _ = self.service.repository.load(self.gid)
        self.assertEqual(state.state_version, 0)

    def test_checkpoint_versions_and_public_target(self):
        """어댑터가 반환한 모델 버전과 공개 인물 대상이 시도 기록에 보존된다."""
        from engine.domain.model import Phase
        self.at_phase(Phase.ROUND1)
        response = self.parse(text='훈장이 수상하다')
        self.assertEqual(response.status_code, 200)
        parser = self.service.interactions.parser.parser
        self.assertEqual(parser.model.model_version, 'bert-receive-classifier-double')
        # UI 조각을 만들기 전 응용의 시도 저장 내용으로 같은 버전을 대조한다.
        with self.service.interactions.store.edit(self.gid) as records:
            attempt = records.attempts[response.context['attempt_id']]
            self.assertEqual(attempt['payload']['versions']['model'], parser.model.model_version)
            self.assertTrue(attempt['output'].needs_confirmation)
            self.assertTrue(any(c['command']['target'] == 'hunjang' for c in attempt['payload']['candidates']))


@skipUnless(os.environ.get('PALDO_BERT_RECEIVE_RUN_DIR'), 'actual D checkpoint path required')
@override_settings(INTENT_PARSER_FACTORY=FACTORY)
class RealBertScreens(screens.UtteranceScreens):
    """명시적으로 인수한 실물 체크포인트로 같은 B-07-01 HTTP 계약을 검증한다."""

    def setUp(self):
        """모델 로더를 대체하지 않고 이번 시험에서만 실제 아티팩트 경로를 지정한다."""
        self.enterContext(override_settings(INTENT_BERT_RUN_DIR=os.environ['PALDO_BERT_RECEIVE_RUN_DIR']))
        super().setUp()

    def test_real_checkpoint_target_versions_and_cancel_preserve_state(self):
        """실물 BERT의 공개 대상·버전을 HTTP 시도에 저장하고 취소는 판을 소비하지 않는다."""
        from engine.domain.model import Phase
        from intent.bert import BertIntentParser

        self.at_phase(Phase.ROUND1)
        parser = self.service.interactions.parser.parser
        self.assertIsInstance(parser, BertIntentParser)
        before = self.service.repository.load(self.gid)[0].state_version
        response = self.parse(text='훈장이 수상하다')
        self.assertEqual(response.status_code, 200)
        aid = response.context['attempt_id']
        with self.service.interactions.store.edit(self.gid) as records:
            attempt = records.attempts[aid]
            self.assertTrue(attempt['output'].needs_confirmation)
            self.assertEqual(attempt['payload']['versions']['model'], parser.model.model_version)
            self.assertIn('sha256:', attempt['payload']['versions']['tokenizer'])
            self.assertTrue(any(candidate['command']['kind'] == 'suspect' and
                                candidate['command']['target'] == 'hunjang'
                                for candidate in attempt['payload']['candidates']))
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, before)
        cancelled = self.resolve(aid, 'cancel')
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, before)
        with self.service.interactions.store.edit(self.gid) as records:
            self.assertTrue(records.attempts[aid]['closed'])
