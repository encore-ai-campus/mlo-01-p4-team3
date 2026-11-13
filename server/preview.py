"""루프백에서 녹화 판을 확인하는 개발용 진입점."""
import argparse
import os
from pathlib import Path
import secrets
import sys
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4


PREVIEW_LOGIN = ("preview", "Preview123!")


def add_preview_templates(template_setting, directory):
    """미리보기 전용 템플릿 폴더를 UI 로더 뒤에 덧붙인다.

    UI 로더는 엔진 DIRS를 보지 않고 모드별 폴더만 찾으므로 DIRS에 더하면 찾지 못한다.
    캐시 로더 안에 폴더를 지정한 파일 로더를 마지막으로 넣어 운영 템플릿 탐색 순서는 그대로 둔다.
    """
    loaders = template_setting["OPTIONS"]["loaders"]
    cached_name, inner = loaders[0]
    loaders[0] = (cached_name, [*inner, ("django.template.loaders.filesystem.Loader", [str(directory)])])


def main():
    """임시 SQLite와 녹화 판으로만 동작하는 화면을 실행한다."""
    parser = argparse.ArgumentParser(description="팔도무록 회의 화면 미리보기")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    host = f"127.0.0.1:{args.port}"
    origin = f"http://{host}"
    os.environ.update(
        DJANGO_SETTINGS_MODULE="server.paldo_web.test_settings",
        DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
        PALDO_PUBLIC_ORIGIN=origin,
        PALDO_GAME_SERVICE_FACTORY="",
    )

    import django
    from django.conf import settings
    from django.core.management import call_command

    with NamedTemporaryFile(prefix="paldo-preview-", suffix=".sqlite3", delete_on_close=False) as database:
        database.close()
        settings.DATABASES["default"]["NAME"] = database.name
        settings.ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
        add_preview_templates(settings.TEMPLATES[0], root / "server" / "tests" / "templates")
        django.setup()

        from django.contrib.auth.models import User
        from django.contrib.auth.views import LoginView
        from django.db import connections
        from django.urls import path, reverse
        from django.utils import timezone

        from server.app.dto import StartGameInput
        from server.paldo_web import urls
        from server.paldo_web.composition import configure_game_service
        from server.tests.support import make_service

        try:
            call_command("migrate", verbosity=0, interactive=False)
            username, password = PREVIEW_LOGIN
            user = User.objects.create_user(username, password=password)
            profile = SimpleNamespace(player_pseudonym=uuid4(), consented_at=timezone.now())
            service = make_service()
            configure_game_service(service)
            player = str(profile.player_pseudonym)
            game = service.start(StartGameInput(player, "base6", "test-session", "f"), player, True)
            game_path = reverse("game-detail", kwargs={"game_id": game.view.game_id})
            login = LoginView.as_view(template_name="preview_login.html", next_page=game_path)
            urls.urlpatterns.insert(0, path("accounts/login", login))
            print(f"Login: {origin}/accounts/login", flush=True)
            print(f"Preview account: {username}", flush=True)
            print(f"Preview password: {password}", flush=True)
            with patch.object(User, "paldo_profile", property(lambda account: profile if account.pk == user.pk else None),
                              create=True):
                call_command("runserver", host, use_reloader=False, use_static_handler=False)
        finally:
            connections.close_all()


if __name__ == "__main__":
    main()
