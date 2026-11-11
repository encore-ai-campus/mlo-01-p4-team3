"""부하 측정의 분위수 계산과 부적절한 실행 입력을 검증한다."""
from unittest import TestCase
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import os
from .api_bench import main, mongo_uri, percentile


class ApiBench(TestCase):
    """표본·판정 경계와 DB 접근 전 입력 거부를 검사한다."""

    def test_compose_env_fallback_and_explicit_environment_priority(self):
        """Compose 생성 파일을 그대로 읽고 명시 환경이 우선한다. 비밀값은 출력하지 않는다."""
        with TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, 'mongo_not_configured'):
                mongo_uri(root)
            (root / '.env').write_text('PALDO_MONGO_URI=mongodb://compose-test\n', encoding='utf-8-sig')
            self.assertEqual(mongo_uri(root), 'mongodb://compose-test')
            with patch.dict(os.environ, {'PALDO_MONGO_URI': 'mongodb://explicit-test'}):
                self.assertEqual(mongo_uri(root), 'mongodb://explicit-test')

    def test_nearest_rank_uses_all_samples_and_rejects_empty(self):
        """정렬되지 않은 20개 표본의 p95는 19이며 빈 표본은 실패다."""
        self.assertEqual(percentile(list(range(20, 0, -1)), .95), 19)
        self.assertEqual(percentile([1], .5), 1)
        with self.assertRaises(ValueError):
            percentile([], .95)

    def test_invalid_workload_never_opens_database(self):
        """잘못된 사용자 수·시간·출력 이름은 부하 전에 거부한다."""
        with patch('server.tests.api_bench.run') as run:
            for args in (['--users', '0'], ['--minutes', '0'], ['--result-name', '../secret']):
                with self.assertRaises(SystemExit):
                    main(args)
            run.assert_not_called()

    def test_latency_limit_and_http_errors_fail_the_run(self):
        """필수 1초 초과 또는 응답 오류를 성공 측정으로 보고하지 않는다."""
        for latency, errors, expected in ((1000, 0, 0), (1000.001, 0, 1), (10, 1, 1)):
            with patch('server.tests.api_bench.run', return_value={'p95_ms': latency, 'errors': errors}):
                self.assertEqual(main([]), expected)
