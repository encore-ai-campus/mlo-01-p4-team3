"""광고 세션 삭제 범위를 계획하고 승인된 MySQL 정리를 실행한다."""
import json
import os
from pathlib import Path
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from ads.infra.privacy_cleanup import collect_scope, purge_scope


class Command(BaseCommand):
    """보호된 광고 삭제 계획과 운영 확인을 받는다."""

    def add_arguments(self, parser):
        """계획 파일·광고 가명·작업 경계를 받는다."""
        parser.add_argument("stage", choices=("plan", "purge"))
        parser.add_argument("--scope", type=Path, required=True)
        parser.add_argument("--game-plan", type=Path)
        parser.add_argument("--ad-writes-drained", action="store_true")
        parser.add_argument("--tombstone-registered", action="store_true")

    def handle(self, *args, **options):
        """입력 가명을 확인하고 식별자 대신 건수를 반환한다."""
        path = options["scope"]
        try:
            if options["stage"] == "plan":
                game = self.read_protected(options["game_plan"])
                sessions = sorted({str(UUID(value)) for value in game["ad_sessions"]})
                scope = collect_scope(sessions)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(scope, output)
                result = {key: len(values) for key, values in scope.items()}
            else:
                if not (options["ad_writes_drained"] and options["tombstone_registered"]):
                    raise CommandError("privacy_ads_drain_and_tombstone_required")
                scope = self.read_protected(path)
                result = purge_scope(scope)
        except (OSError, TypeError, KeyError, ValueError) as error:
            raise CommandError(f"privacy_ads_cleanup_failed:{type(error).__name__}") from None
        self.stdout.write(json.dumps(result))

    @staticmethod
    def read_protected(path):
        """링크와 다른 사용자가 읽는 파일을 거절한다."""
        if not path or path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
            raise CommandError("privacy_protected_scope_required")
        return json.loads(path.read_text(encoding="utf-8"))
