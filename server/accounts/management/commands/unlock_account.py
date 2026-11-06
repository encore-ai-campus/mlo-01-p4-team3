"""로그인 ID 원문을 출력하지 않고 잠금을 해제한다."""
from getpass import getpass
from django.core.management.base import BaseCommand
from server.accounts.lockout import login_identity
from server.accounts.models import LoginLock


class Command(BaseCommand):
    """현장 확인을 마친 운영자가 사용하는 잠금 해제 명령."""
    help = "계정 로그인 잠금을 해제합니다. ID는 비표시 입력으로 받습니다."

    def handle(self, *args, **options):
        """기존 잠금 행만 원자 갱신하고 ID 없는 결과를 출력한다."""
        key, _ = login_identity(getpass("로그인 ID: "))
        LoginLock.objects.filter(pk=key).update(failures=0, locked_until=None)
        self.stdout.write("잠금 해제 처리를 마쳤습니다.")
