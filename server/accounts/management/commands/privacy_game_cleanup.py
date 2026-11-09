"""개인정보 요청의 계정 잠금 및 게임 저장소 정리를 단계별로 실행한다."""
import json
import os
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from server.accounts.lockout import counter_key
from server.accounts.models import AccountProfile, LoginLock
from server.accounts.validators import normalize_login_id
from server.infra.privacy_game import game_scope, purge_games


def find_account(player):
    """참가자 가명의 계정을 조회하고 운영자 계정을 제외한다."""
    profile = AccountProfile.objects.filter(player_pseudonym=player).first()
    if profile is None:
        return None
    user = get_user_model().objects.select_for_update().get(pk=profile.user_id)
    if user.is_staff or user.is_superuser:
        raise CommandError("privacy_staff_account_excluded")
    return user


def remove_sessions(user_id):
    """DB 세션 중 지정 계정의 세션만 찾아 삭제한다."""
    removed = 0
    for session in Session.objects.iterator(chunk_size=200):
        if str(session.get_decoded().get(SESSION_KEY)) == str(user_id):
            removed += Session.objects.filter(pk=session.pk).delete()[0]
    return removed


class Command(BaseCommand):
    """운영자가 승인된 요청 한 건을 잠그고 게임과 계정을 순서대로 처리한다."""

    def add_arguments(self, parser):
        """대상과 단계, 보호된 증빙 파일, 실행 확인을 받는다."""
        parser.add_argument("stage", choices=("freeze", "plan", "purge-games", "finalize"))
        parser.add_argument("--player", type=UUID, required=True)
        parser.add_argument("--plan", type=Path)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--game-writes-drained", action="store_true")
        parser.add_argument("--tombstone-registered", action="store_true")
        parser.add_argument("--external-deletions-verified", action="store_true")

    def handle(self, *args, **options):
        """계정 잠금 후에만 게임 정리를 허용하고 계정 행을 마지막에 지운다."""
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError

        stage, player = options["stage"], str(options["player"])
        if stage != "plan" and not options["apply"]:
            raise CommandError("privacy_apply_required")
        with transaction.atomic():
            account = find_account(player)
            if stage == "freeze":
                if account is None:
                    raise CommandError("privacy_account_missing")
                account.is_active = False
                account.save(update_fields=["is_active"])
                self.stdout.write(json.dumps({"account_frozen": True, "sessions_deleted": remove_sessions(account.pk)}))
                return
            if account is None or account.is_active:
                raise CommandError("privacy_frozen_account_required")

            if not options["plan"] or not settings.SESSION_MONGO_URI:
                raise CommandError("privacy_plan_and_mongo_required")
            if stage in ("purge-games", "finalize") and not options["game_writes_drained"]:
                raise CommandError("privacy_game_drain_required")
            if stage in ("purge-games", "finalize") and not options["tombstone_registered"]:
                raise CommandError("privacy_tombstone_required")
            try:
                with MongoClient(settings.SESSION_MONGO_URI, timeoutMS=15000) as client:
                    self.run_stage(client.get_default_database("paldo_web"), account, player, options)
            except (PyMongoError, ValueError, OSError, KeyError) as error:
                raise CommandError(f"privacy_cleanup_failed:{type(error).__name__}") from None

    def run_stage(self, database, account, player, options):
        """보호 계획을 기록하거나 승인된 범위의 상태를 확인하고 처리한다."""
        path = options["plan"]
        stage = options["stage"]
        if stage == "plan":
            scope = game_scope(database, player)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump({"player": player, **scope}, output)
            result = {"games": len(scope["game_ids"]), "ad_sessions": len(scope["ad_sessions"])}
        else:
            if path.is_symlink() or (os.name != "nt" and path.stat().st_mode & 0o077):
                raise CommandError("privacy_protected_plan_required")
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan["player"] != player:
                raise CommandError("privacy_plan_player_mismatch")
            if stage == "purge-games":
                result = purge_games(database, player, plan["game_ids"])
            else:
                if not options["external_deletions_verified"]:
                    raise CommandError("privacy_external_verification_required")
                scope = game_scope(database, player)
                remaining_interactions = database.b_interactions.count_documents(
                    {"_id": {"$in": plan["game_ids"]}}
                )
                if any(scope.values()) or remaining_interactions:
                    raise CommandError("privacy_game_data_remaining")
                username = normalize_login_id(account.get_username())
                LoginLock.objects.filter(pk=counter_key("login", username)).delete()
                removed = remove_sessions(account.pk)
                account.delete()
                result = {"account_deleted": True, "sessions_deleted": removed}
        self.stdout.write(json.dumps(result))
