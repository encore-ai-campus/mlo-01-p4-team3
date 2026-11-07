"""광고 서비스 테스트는 Django 관리 명령으로 명시 실행한다."""
import os
import unittest


def load_tests(loader, tests, pattern):
    """일반 unittest 탐색에서는 외부 MySQL이 필요한 테스트를 제외한다."""
    if not os.environ.get("PALDO_SERVICE_TESTS"):
        return unittest.TestSuite()
    return loader.discover(__path__[0], pattern or "test*.py", top_level_dir=str(__path__[0] + "/../.."))
