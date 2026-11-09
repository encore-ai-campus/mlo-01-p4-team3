"""18:00 스냅숏 전에 끝났는데 설문 이벤트가 없는 판을 건너뜀으로 일괄 채운다(B-09-07 9번)."""
from django.core.management.base import BaseCommand
from server.accounts.models import AccountProfile
from server.paldo_web.composition import get_game_service


class Command(BaseCommand):
    """다시 오지 않은 참가자의 끝난 판도 설문 이벤트 1건을 갖게 하는 운영 명령."""
    help = "끝났는데 설문 이벤트가 없는 판을 건너뜀(reason=backfill)으로 채웁니다. 여러 번 실행해도 판당 1건입니다."

    def handle(self, *args, **options):
        """모든 참가자 가명으로 응용 계층 일괄 보완을 부르고 채운 판 수만 출력한다(가명은 출력하지 않음)."""
        players = [str(p) for p in AccountProfile.objects.values_list("player_pseudonym", flat=True)]
        filled = get_game_service().interactions.backfill_all_skipped_surveys(players)
        self.stdout.write(f"참가자 {len(players)}명, 건너뜀으로 채운 판 {filled}개")
