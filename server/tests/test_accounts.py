"""계정·잠금·세션·CSRF·비밀번호 규칙의 Django 회귀 검사."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import json
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
import secrets
from threading import Barrier
from tempfile import TemporaryDirectory, gettempdir
from unittest.mock import patch
import uuid

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from engine.domain.serde import dumps
from ads.domain.model import AdSlot
from ads.testing.fakes import FakeAdProvider
from server.app.ads_port import AdGateway
from server.app.test_sessions import TestSessionService
from server.accounts.lockout import counter_key, login_user
from server.accounts.models import AccountProfile, LoginLock
from server.paldo_web.composition import configure_ad_gateway, configure_game_service, configure_session_service
from server.paldo_web.middleware import JsonFormatter, LOGGER
from .support import make_service
from .session_store import MemorySessionStore

PASSWORD = secrets.token_hex(12) + "a1!"
WRONG_PASSWORD = secrets.token_hex(12) + "b2!"
NEW_PASSWORD = secrets.token_hex(12) + "c3!"


class Accounts(TestCase):
    """개인정보 없는 가입·동일 실패 응답·세션 폐기를 확인한다."""

    def signup(self, client=None, **overrides):
        """시험 계정 가입 폼을 보낸다."""
        data = {"username": "PilotOne", "password": PASSWORD, "password_confirm": PASSWORD, "consent": "on"}
        data.update(overrides)
        return (client or self.client).post("/accounts/signup", data)

    def test_signup_normalization_consent_and_hash(self):
        """동의·ID 유일성·비밀번호 해시와 가명 표시를 검사한다."""
        form = self.client.get("/accounts/signup")
        self.assertContains(form, "2026년 12월 12일까지")
        self.assertNotContains(form, 'name="invite_code"')
        self.assertEqual(form.wsgi_request.sensitive_post_parameters, "__ALL__")
        self.assertEqual(self.client.get("/accounts/login").wsgi_request.sensitive_post_parameters, "__ALL__")
        self.assertEqual(self.signup(consent="").status_code, 400)
        self.assertEqual(self.signup().status_code, 302)
        user = User.objects.get(username="pilotone")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertNotEqual(user.password, PASSWORD)
        profile = AccountProfile.objects.get(user=user)
        self.assertIsNotNone(profile.consented_at)
        self.assertEqual(profile.player_pseudonym.version, 4)
        self.assertEqual(self.signup(username="PILOTONE").status_code, 400)
        self.assertContains(self.client.get("/"), str(profile.player_pseudonym))
        self.assertNotContains(self.client.get("/"), "pilotone")

    def test_account_tables_and_session_settings(self):
        """인증·세션·admin과 계정 테이블은 모두 Django default DB에 있어야 한다."""
        self.assertFalse(settings.DEBUG)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 1_209_600)
        connection = connections["default"]
        tables = {"auth_user", "django_session", "django_admin_log", "accounts_accountprofile",
                  "accounts_loginlock"}
        actual_tables = set(connection.introspection.table_names())
        self.assertLessEqual(tables, actual_tables)
        self.assertNotIn("accounts_signupwindow", actual_tables)
        if connection.vendor == "mysql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE(), VERSION()")
                schema, version = cursor.fetchone()
            self.assertEqual(schema, "test_paldo_auth")
            print(f"Account tables on default: {schema} (MySQL {version}): {', '.join(sorted(tables))}", flush=True)

    def test_admin_login_uses_account_lock_and_profile_listing(self):
        """관리자 로그인 우회를 차단하고 운영 화면에서 로그인 ID를 숨긴다."""
        staff = User.objects.create_superuser("staff-check", password=PASSWORD)
        profile = AccountProfile.objects.create(user=staff, consented_at=timezone.now())
        for _ in range(5):
            self.assertEqual(self.client.post("/accounts/login", {
                "username": staff.username, "password": WRONG_PASSWORD,
            }).status_code, 400)
        credentials = {"username": staff.username, "password": PASSWORD}
        locked = self.client.post("/admin/login/", credentials)
        self.assertContains(locked, "로그인할 수 없습니다.", status_code=400)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)
        with patch("server.accounts.management.commands.unlock_account.getpass", return_value=staff.username):
            call_command("unlock_account", stdout=StringIO())
        self.assertEqual(self.client.post("/admin/login/", credentials).status_code, 302)
        listing = self.client.get("/admin/accounts/accountprofile/")
        self.assertContains(listing, str(profile.player_pseudonym))
        self.assertNotContains(listing, staff.username)

    def test_game_resources_are_scoped_to_stored_account_pseudonyms(self):
        """실제 계정 두 개의 판·시도·렌더·중복 명령 접근을 구분한다."""
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.assertEqual(self.signup().status_code, 302)
        owner = self.client
        other = Client()
        self.assertEqual(self.signup(client=other, username="pilot-two").status_code, 302)

        def request(client, method, path, body=None):
            return client.generic(method, path, json.dumps(body or {}), content_type="application/json",
                                  HTTP_ACCEPT="application/json")

        start = {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}
        self.assertEqual(request(owner, "POST", "/games", dict(start, player_pseudonym=str(uuid.uuid4()))).status_code, 400)
        started = request(owner, "POST", "/games", start)
        self.assertEqual(started.status_code, 200)
        game_id = started.json()["view"]["game_id"]
        base = f"/games/{game_id}"
        parsed = request(owner, "POST", base + "/utterances",
                         {"game_id": game_id, "expected_state_version": 0, "text": "지켜보"})
        self.assertEqual(parsed.status_code, 200)
        attempt_id = parsed.json()["attempt_id"]
        for step in service.engine.steps[1:4]:
            command = dict(step["request"], game_id=game_id, command_id=str(uuid.uuid4()))
            self.assertEqual(request(owner, "POST", base + "/commands", command).status_code, 200)
        self.assertTrue(request(owner, "POST", base + "/commands", command).json()["replayed"])
        self.assertEqual(request(owner, "GET", base + "/renders/job_001").status_code, 200)
        for method, path, body in (
            ("GET", base, None),
            ("POST", base + "/commands", command),
            ("POST", base + f"/utterances/{attempt_id}", {"game_id": game_id, "attempt_id": attempt_id, "reaction": "cancel"}),
            ("GET", base + "/renders/job_001", None),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(request(other, method, path, body).status_code, 404)

    def test_password_rules_shared_with_management(self):
        """가입과 관리 명령이 공유하는 모든 비밀번호 규칙을 검사한다."""
        user = User(username="pilotone")
        for invalid in ("a1!", "A123456!", "abcdefgh!", "abc12345", "abc 12345", "a1!" * 44, "pilotone1!", "password1!"):
            with self.subTest(password_length=len(invalid)), self.assertRaises(ValidationError):
                validate_password(invalid, user)
        validate_password(PASSWORD, user)

    def test_lockout_clock_and_unknown_id(self):
        """없는 ID에도 5회·15분 규칙과 만료 연장 금지를 적용한다."""
        self.signup()
        request = RequestFactory().post("/accounts/login")
        now = timezone.now()
        for name in ("PILOTONE", "unknown-account"):
            for _ in range(5):
                self.assertIsNone(login_user(request, name, "wrong", now))
            row = LoginLock.objects.get(pk=counter_key("login", name.lower()))
            self.assertEqual(row.locked_until, now + timedelta(minutes=15))
            self.assertIsNone(login_user(request, name, PASSWORD, now + timedelta(minutes=1)))
            row.refresh_from_db()
            self.assertEqual(row.locked_until, now + timedelta(minutes=15))
        self.assertIsNotNone(login_user(request, "PILOTONE", PASSWORD, now + timedelta(minutes=15)))
        self.assertEqual(LoginLock.objects.get(pk=counter_key("login", "pilotone")).failures, 0)

    def test_failures_have_identical_response(self):
        """없는 ID·오답·잠금의 응답 코드와 문구가 같아야 한다."""
        self.signup()
        self.client.logout()
        wrong = self.client.post("/accounts/login", {"username": "pilotone", "password": WRONG_PASSWORD})
        absent = self.client.post("/accounts/login", {"username": "absent", "password": WRONG_PASSWORD})
        for _ in range(4):
            self.client.post("/accounts/login", {"username": "pilotone", "password": WRONG_PASSWORD})
        locked = self.client.post("/accounts/login", {"username": "pilotone", "password": PASSWORD})
        for response in (wrong, absent, locked):
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "로그인할 수 없습니다.", status_code=400)
            self.assertNotIn("Location", response)

    def test_database_equivalent_login_ids_share_lock_and_unlock(self):
        """MySQL이 같은 계정으로 조회하는 철자는 잠금 행도 공유한다."""
        username, alias = "pilotone", "pilótone"
        self.signup()
        self.client.logout()
        if not User.objects.filter(username=alias).exists():
            self.skipTest("악센트를 구분하는 DB에서는 두 ID가 별개입니다.")
        path = "/accounts/login"
        for login_id in (username, alias, username.upper(), alias, username):
            self.assertEqual(self.client.post(path, {"username": login_id, "password": WRONG_PASSWORD}).status_code, 400)
        for login_id in (username, alias):
            self.assertEqual(self.client.post(path, {"username": login_id, "password": PASSWORD}).status_code, 400)
        lock = LoginLock.objects.get()
        self.assertEqual(lock.key, counter_key("login", username))
        self.assertEqual(lock.failures, 5)
        with patch("server.accounts.management.commands.unlock_account.getpass", return_value=alias):
            call_command("unlock_account", stdout=StringIO())
        lock.refresh_from_db()
        self.assertIsNone(lock.locked_until)
        self.assertEqual(self.client.post(path, {"username": alias, "password": PASSWORD}).status_code, 302)

    @override_settings(SIGNUP_INVITE_CODE="test-invitation")
    def test_invite_gate(self):
        """초대 코드 설정이 있을 때만 올바른 코드를 요구한다."""
        self.assertEqual(self.signup().status_code, 400)
        self.assertEqual(self.signup(invite_code="test-invitation").status_code, 302)

    def test_csrf_and_session_invalidation(self):
        """변경 요청 CSRF와 비밀번호 초기화·로그아웃 후 세션 폐기를 검사한다."""
        secure = Client(enforce_csrf_checks=True)
        for path in ("/accounts/signup", "/accounts/login", "/accounts/logout", "/games"):
            self.assertEqual(secure.post(path, {}).status_code, 403)
        self.assertEqual(self.client.get("/accounts/logout").status_code, 405)
        self.signup()
        before = self.client.cookies["sessionid"].value
        user = User.objects.get(username="pilotone")
        user.set_password(NEW_PASSWORD)
        user.save()
        self.assertEqual(self.client.get("/players/me/progress", HTTP_ACCEPT="application/json").status_code, 401)
        self.client.post("/accounts/login", {"username": "pilotone", "password": NEW_PASSWORD})
        self.assertNotEqual(self.client.cookies["sessionid"].value, before)
        self.client.post("/accounts/logout")
        self.assertEqual(self.client.get("/players/me/progress", HTTP_ACCEPT="application/json").status_code, 401)

    def test_changepassword_validates_rules_and_invalidates_sessions(self):
        """관리 명령의 규칙 거부는 기존 비밀번호를 보존하고 성공은 이전 세션을 폐기한다."""
        # 비밀번호의 16진 문자와 겹치지 않아 우연한 유사성 거부를 막는다.
        username = "reset-" + "".join(secrets.choice("ghjkmnpqrstuvwxyz") for _ in range(16))
        self.assertEqual(self.signup(username=username).status_code, 302)
        user = User.objects.get(username=username)
        original_hash = user.password
        pseudonym = user.paldo_profile.player_pseudonym
        original_session = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        invalid_passwords = (
            ("short", secrets.token_hex(2) + "a1!"),
            ("long", secrets.token_hex(65) + "a1!"),
            ("lowercase", secrets.token_hex(12).upper() + "A1!"),
            ("digit", "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz") for _ in range(24)) + "!"),
            ("special", secrets.token_hex(12) + "a1"),
            ("whitespace", secrets.token_hex(12) + "a1 "),
            ("common", "password1!"),
            ("similar", username + "1!"),
        )
        for rule, invalid in invalid_passwords:
            with self.subTest(rule=rule), patch(
                "django.contrib.auth.management.commands.changepassword.getpass.getpass", return_value=invalid,
            ), self.assertRaises(CommandError):
                call_command("changepassword", username, stdout=StringIO(), stderr=StringIO())
            user.refresh_from_db()
            self.assertTrue(user.password == original_hash, "거부된 초기화가 기존 해시를 바꿨습니다.")
        self.assertTrue(self.client.get("/").wsgi_request.user.is_authenticated)

        new_password = secrets.token_hex(24) + "a1!"
        with patch("django.contrib.auth.management.commands.changepassword.getpass.getpass", return_value=new_password):
            call_command("changepassword", username, stdout=StringIO(), stderr=StringIO())
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_password))
        self.assertEqual(user.paldo_profile.player_pseudonym, pseudonym)
        self.assertFalse(user.check_password(PASSWORD))
        stale_browser = Client()
        stale_browser.cookies[settings.SESSION_COOKIE_NAME] = original_session
        for browser in (self.client, stale_browser):
            self.assertEqual(browser.get("/players/me/progress", HTTP_ACCEPT="application/json").status_code, 401)
        self.assertFalse(Session.objects.filter(session_key=original_session).exists())
        self.assertEqual(self.client.post("/accounts/login", {"username": username, "password": new_password}).status_code, 302)
        self.assertTrue(self.client.cookies[settings.SESSION_COOKIE_NAME].value != original_session)

    def test_login_replaces_anonymous_session_key(self):
        """로그인 전 익명 세션 키를 재사용해도 로그인 권한을 얻지 못한다."""
        self.assertEqual(self.signup().status_code, 302)
        self.client.post("/accounts/logout")
        anonymous = self.client.session
        anonymous["prelogin"] = "kept"
        anonymous.save()
        old_key = anonymous.session_key
        self.client.cookies[settings.SESSION_COOKIE_NAME] = old_key
        self.assertTrue(Session.objects.filter(session_key=old_key).exists())

        self.assertEqual(self.client.post("/accounts/login", {"username": "pilotone", "password": PASSWORD}).status_code, 302)
        self.assertTrue(self.client.cookies[settings.SESSION_COOKIE_NAME].value != old_key)
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())
        stale_browser = Client()
        stale_browser.cookies[settings.SESSION_COOKIE_NAME] = old_key
        self.assertEqual(stale_browser.get("/players/me/progress", HTTP_ACCEPT="application/json").status_code, 401)
        self.assertTrue(self.client.get("/").wsgi_request.user.is_authenticated)

    def test_relogin_with_same_account_replaces_session_key(self):
        """로그인 중 다시 인증해도 새 세션을 발급하고 이전 쿠키를 무효화한다."""
        self.assertEqual(self.signup().status_code, 302)
        User.objects.filter(username="pilotone").update(is_staff=True)
        for path in ("/accounts/login", "/admin/login/"):
            with self.subTest(path=path):
                old_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
                self.assertEqual(self.client.post(path, {"username": "pilotone", "password": PASSWORD}).status_code, 302)
                new_key = self.client.cookies[settings.SESSION_COOKIE_NAME].value
                self.assertNotEqual(new_key, old_key)
                self.assertFalse(Session.objects.filter(session_key=old_key).exists())
                stale_browser = Client()
                stale_browser.cookies[settings.SESSION_COOKIE_NAME] = old_key
                self.assertEqual(stale_browser.get("/players/me/progress", HTTP_ACCEPT="application/json").status_code, 401)
                self.assertTrue(self.client.get("/").wsgi_request.user.is_authenticated)

    def test_credentials_absent_from_logs_outbox_and_metrics(self):
        """계정 성공·실패와 판 완주 후 로그·이벤트·지표·광고 포트에 인증 입력이 없다."""
        username = "privacy-" + secrets.token_hex(12)
        absent_username = "absent-" + secrets.token_hex(12)
        password = secrets.token_hex(24) + "a1!"
        wrong_password = secrets.token_hex(24) + "a1!"
        invalid_reset = secrets.token_hex(24).upper()
        invite = secrets.token_urlsafe(32)
        wrong_invite = secrets.token_urlsafe(32)
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        sessions = TestSessionService(MemorySessionStore())
        configure_session_service(sessions)
        self.addCleanup(configure_session_service, None)
        provider = FakeAdProvider()
        gateway = AdGateway(provider)
        configure_ad_gateway(gateway)
        self.addCleanup(configure_ad_gateway, None)
        self.addCleanup(gateway._pool.shutdown, wait=True)
        self.addCleanup(gateway._relay_pool.shutdown, wait=True)
        log_directory = TemporaryDirectory(prefix="paldo-file-log-")
        log_root = Path(log_directory.name).resolve()
        self.assertEqual(log_root.parent, Path(gettempdir()).resolve())
        self.addCleanup(log_directory.cleanup)
        log_path = log_root / "web.jsonl"
        handler = TimedRotatingFileHandler(log_path, when="midnight", utc=True, backupCount=7, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        self.addCleanup(handler.close)

        with self.settings(SIGNUP_INVITE_CODE=invite), patch.object(LOGGER, "handlers", [handler]):
            signup_data = {"username": username, "password": password, "password_confirm": password,
                           "consent": "on", "invite_code": wrong_invite}
            self.assertEqual(self.client.post("/accounts/signup", signup_data).status_code, 400)
            signup_data["invite_code"] = invite
            self.assertEqual(self.client.post("/accounts/signup", signup_data).status_code, 302)
            self.assertEqual(self.client.post("/accounts/logout").status_code, 302)
            self.assertEqual(self.client.post("/accounts/login", {"username": username.upper(), "password": password}).status_code, 302)
            login_session_key = self.client.session.session_key
            # 요청 경계에서 위조한 계정·판 식별자도 광고 DTO로 옮기지 않는다.
            for ad_slot in AdSlot:
                ad_response = self.client.get(f"/ads/slot/{ad_slot.value}", {
                    "username": username, "player_pseudonym": "forged-player", "game_id": "forged-game",
                })
                self.assertEqual(ad_response.status_code, 200)
                self.assertEqual(provider.calls[-1].request_id, ad_response["X-Request-ID"])
            response = self.client.post(
                "/games", data=json.dumps({"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}),
                content_type="application/json", HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 200)
            game_id = response.json()["view"]["game_id"]
            recording_path = Path(__file__).resolve().parents[2] / "fixtures/api/recorded_game_base6.json"
            recording = json.loads(recording_path.read_text(encoding="utf-8"))
            for step in recording["steps"][1:]:
                payload = dict(step["request"], game_id=game_id, command_id=str(uuid.uuid4()))
                response = self.client.post(
                    f"/games/{game_id}/commands", data=json.dumps(payload),
                    content_type="application/json", HTTP_ACCEPT="application/json",
                )
                self.assertEqual(response.status_code, 200)
            self.assertIsNotNone(response.json()["view"]["result"])
            self.assertEqual(self.client.get("/ads/slot/result_banner").status_code, 200)
            self.assertEqual(self.client.post("/accounts/signup", dict(signup_data, username=username.upper())).status_code, 400)
            self.client.post("/accounts/logout")
            for _ in range(5):
                self.assertEqual(self.client.post("/accounts/login", {"username": username, "password": wrong_password}).status_code, 400)
            self.assertEqual(self.client.post("/accounts/login", {"username": username, "password": password}).status_code, 400)
            self.assertEqual(self.client.post("/accounts/login", {"username": absent_username, "password": wrong_password}).status_code, 400)
            calls_before = len(provider.calls)
            self.assertEqual(self.client.get("/ads/slot/lobby_banner", HTTP_ACCEPT="application/json").status_code, 401)
            self.assertEqual(len(provider.calls), calls_before)
            with patch(
                "django.contrib.auth.management.commands.changepassword.getpass.getpass", return_value=invalid_reset,
            ), self.assertRaises(CommandError):
                call_command("changepassword", username, stdout=StringIO(), stderr=StringIO())
            metrics_response = self.client.get("/metrics")
            self.assertEqual(metrics_response.status_code, 200)

        handler.flush()
        logs = log_path.read_text(encoding="utf-8")
        records = [json.loads(line) for line in logs.splitlines()]
        self.assertEqual({record["result"] for record in records if record["msg"] == "login_result"},
                         {"success", "failure", "locked"})
        events = service.repository.unpublished(game_id)
        self.assertTrue(any(event.event_type == "game_ended" for event in events))
        pseudonym = str(AccountProfile.objects.get(user__username=username).player_pseudonym)
        self.assertTrue(all(event.player_pseudonym == pseudonym for event in events))
        self.assertTrue(pseudonym in logs)
        metrics = metrics_response.content.decode()
        self.assertTrue("paldo_auth_login_total" in metrics)
        self.assertEqual(len(provider.calls), len(AdSlot) + 1)
        ad_identities = {request.session_pseudonym for request in provider.calls}
        self.assertEqual(len(ad_identities), 1)
        self.assertEqual(sessions.store.ad_identities(pseudonym), list(ad_identities))
        self.assertNotIn(pseudonym, ad_identities)
        self.assertEqual(uuid.UUID(next(iter(ad_identities))).version, 4)
        ad_requests = json.loads(dumps(provider.calls))
        for request in ad_requests:
            self.assertEqual(set(request), {"request_id", "slot", "session_pseudonym", "context", "source", "load_test"})
            self.assertEqual(request["source"], "human")
            self.assertFalse(request["load_test"])
            self.assertIsNone(request["context"]["preset_id"])
            self.assertIsNone(request["context"]["outcome"])
        self.assertNotIn(pseudonym, dumps(provider.calls))
        self.assertNotIn(login_session_key, dumps(provider.calls))
        self.assertNotIn(game_id, dumps(provider.calls))
        self.assertNotIn("forged-player", dumps(provider.calls))
        self.assertNotIn("forged-game", dumps(provider.calls))
        forbidden = (username, username.upper(), absent_username, password, wrong_password, invalid_reset, invite, wrong_invite)
        for output_name, output in (("file_log", logs), ("outbox", dumps(events)), ("metrics", metrics),
                                    ("ad_provider_requests", dumps(provider.calls))):
            with self.subTest(output=output_name):
                self.assertFalse(any(value in output for value in forbidden), "인증 입력이 외부 기록에 노출되었습니다.")


class AccountConcurrency(TransactionTestCase):
    """행 잠금을 지원하는 계정 DB에서 동시 가입과 로그인 실패를 검증한다."""

    @override_settings(SIGNUP_INVITE_CODE="")
    @skipUnlessDBFeature("has_select_for_update")
    def test_six_concurrent_signups_from_one_address_all_succeed(self):
        """같은 주소의 서로 다른 계정 가입 6건을 모두 허용한다."""
        path = "/accounts/signup"
        address = "192.0.2.1"
        usernames = [f"participant-number-{index}" for index in range(6)]
        barrier = Barrier(len(usernames))

        def signup(username):
            client = Client(enforce_csrf_checks=True, REMOTE_ADDR=address)
            try:
                client.get(path)
                token = client.cookies[settings.CSRF_COOKIE_NAME].value
                barrier.wait(timeout=15)
                response = client.post(path, {
                    "username": username, "password": PASSWORD, "password_confirm": PASSWORD, "consent": "on",
                }, HTTP_X_CSRFTOKEN=token)
                return response.status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(usernames)) as pool:
            statuses = list(pool.map(signup, usernames))
        self.assertEqual(statuses, [302] * len(usernames))
        self.assertEqual(AccountProfile.objects.filter(user__username__in=usernames).count(), len(usernames))
        print(f"Concurrent signups from one address: {statuses}", flush=True)

    @skipUnlessDBFeature("has_select_for_update")
    def test_five_concurrent_failures_lock_existing_and_absent_ids(self):
        """각 ID의 최초 5개 동시 실패가 유실 없이 한 잠금 행에 누적된다."""
        now = timezone.now()
        username = "concurrent-" + secrets.token_hex(12)
        absent_username = "missing-" + secrets.token_hex(12)
        password = secrets.token_hex(24) + "a1!"
        wrong_password = secrets.token_hex(24) + "a1!"
        User.objects.create_user(username=username, password=password)
        for case, login_id in (("existing", username), ("absent", absent_username)):
            with self.subTest(account=case):
                barrier = Barrier(5)

                def fail_login():
                    """독립 DB 연결의 로그인 실패를 같은 시점에 시작한다."""
                    try:
                        request = RequestFactory().post("/accounts/login")
                        barrier.wait(timeout=10)
                        return login_user(request, login_id, wrong_password, now)
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = [pool.submit(fail_login) for _ in range(5)]
                    self.assertTrue(all(future.result(timeout=30) is None for future in futures))
                row = LoginLock.objects.get(pk=counter_key("login", login_id))
                self.assertEqual(row.failures, 5)
                self.assertEqual(row.locked_until, now + timedelta(minutes=15))
                self.assertIsNone(login_user(RequestFactory().post("/accounts/login"), login_id, password, now + timedelta(minutes=1)))
                row.refresh_from_db()
                self.assertEqual(row.failures, 5)
                self.assertEqual(row.locked_until, now + timedelta(minutes=15))
