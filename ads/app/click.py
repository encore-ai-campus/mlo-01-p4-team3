"""클릭 판정과 사이트 안 가상 랜딩 결정."""
from ads.app.beacon import process_event


def handle_click(service, token):
    """유효 클릭을 처리하고 무효일 때도 상대 랜딩 경로를 반환한다."""
    return process_event(service, "click", token)
