"""읽기 전용 운영 조회의 저장소 결과와 도메인 계산을 조립한다."""
from ads.domain.reporting import summarize_day


def build_report(read_rows, start_day, end_day, source, campaign_id=""):
    """서울 귀속일 범위·트래픽 출처별 캠페인 운영 행을 반환한다."""
    return [summarize_day(row) for row in read_rows(start_day, end_day, source, campaign_id)]
