"""계정 비활성화가 다른 참가자의 세션을 건드리지 않는지 검사한다."""
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase

from server.accounts.models import AccountProfile


class PrivacyGameCleanupTests(TestCase):
    """대상 하나만 잠그고 일반 실행과 운영자 처리를 거절한다."""

    def test_freeze_removes_only_subject_sessions(self):
        subject = User.objects.create_user("subject")
        other = User.objects.create_user("other")
        profile = AccountProfile.objects.create(user=subject)
        subject_client, other_client = Client(), Client()
        subject_client.force_login(subject)
        other_client.force_login(other)
        with self.assertRaisesRegex(CommandError, "privacy_apply_required"):
            call_command("privacy_game_cleanup", "freeze", "--player", str(profile.player_pseudonym))
        call_command("privacy_game_cleanup", "freeze", "--player", str(profile.player_pseudonym), "--apply")
        subject.refresh_from_db()
        self.assertFalse(subject.is_active)
        self.assertFalse(subject_client.session.get("_auth_user_id"))
        self.assertEqual(other_client.session.get("_auth_user_id"), str(other.pk))

    def test_freeze_rejects_staff(self):
        staff = User.objects.create_user("staff", is_staff=True)
        profile = AccountProfile.objects.create(user=staff)
        with self.assertRaisesRegex(CommandError, "privacy_staff_account_excluded"):
            call_command("privacy_game_cleanup", "freeze", "--player", str(profile.player_pseudonym), "--apply")
        staff.refresh_from_db()
        self.assertTrue(staff.is_active)
