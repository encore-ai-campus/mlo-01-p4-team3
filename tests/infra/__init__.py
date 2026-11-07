"""실제 Mongo/VM 통합 테스트. core discover에서는 서비스 테스트를 실행하지 않는다."""
def load_tests(loader, tests, pattern):
    """서비스 없는 core에서는 비우고 명시 모듈 실행은 PALDO_TEST_MONGO_URI로 수행한다."""
    return tests
