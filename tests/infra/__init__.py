"""서비스 통합 검사는 명시적으로 실행한다. core에는 외부 의존성을 요구하지 않는다."""


def load_tests(loader,tests,pattern):
    """외부 서비스가 없는 기본 discover에서는 이 패키지를 비운다."""
    return tests
