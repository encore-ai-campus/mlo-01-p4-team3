"""웹 요청과 독립적으로 만료 예약을 해제한다."""
import time


def get_service():
    """작업 반복마다 운영 조립 지점의 새 광고 작업 상태를 받는다."""
    from ads.project.composition import get_service as compose

    return compose()


def expire_once(ledger):
    """밀린 예약을 해제하고 재전송 없이 pending 판정을 마감한다."""
    service = get_service()
    expired = len(ledger.release_expired(time.time_ns() // 1_000_000))
    return expired + service.store.finalize_pending(service)
