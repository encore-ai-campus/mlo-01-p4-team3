"""웹 테스트에서만 사용하는 포트 조립과 승인된 시드 배정 대역."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from django.test import RequestFactory
from django.utils import timezone

from engine.domain.model import DataSource, GameMeta, PRESETS
from engine.ports import EngineDeps
from engine.testing.fakes import (FixtureContentSource, FakeSuspicionModel,
                                  InMemoryGameRepository, InMemoryProgressRepository, ScriptedGameEngine)
from server.app.usecases import GameService
from server.app.interactions import InteractionService
from engine.testing.fakes import FakeIntentParser, FakeLineRenderer
from .interaction_store import MemoryInteractionStore


@contextmanager
def temporary_test_profiles():
    """시험이 끝날 때까지 Django 사용자에 시험용 가명과 동의 시각을 연결한다."""
    profiles = {}

    def register(user):
        profile = SimpleNamespace(player_pseudonym=uuid4(), consented_at=timezone.now())
        profiles[user.pk] = profile
        return profile

    with patch.object(User, "paldo_profile", property(lambda user: profiles.get(user.pk)), create=True):
        yield register


def install_test_profiles(test):
    """Django 세션의 실제 사용자에 시험 전용 프로필 관계를 연결한다."""
    return test.enterContext(temporary_test_profiles())


def issue_test_csrf(client):
    """계정이나 화면 URL 없이 Django CSRF 쿠키와 제출 토큰을 준비한다."""
    request = RequestFactory().get("/")
    token = get_token(request)
    client.cookies[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"]
    return token


def make_service(repository=None, progress=None):
    """녹화 판용 서비스. 운영 테스트 세션 정책으로 사용하지 않는다."""
    content = FixtureContentSource().load("*")
    def prepare(data, player):
        if data.preset_id != "base6" or data.session_id != "test-session":
            raise ValueError("unknown_test_session")
        return GameMeta("", PRESETS["base6"], 7, DataSource.HUMAN,
                        "test-rule", "scripted", content.content_version,
                        player_pseudonym=player, session_id=data.session_id)
    service = GameService(ScriptedGameEngine(), InMemoryGameRepository() if repository is None else repository,
                       InMemoryProgressRepository() if progress is None else progress,
                       lambda meta: EngineDeps(content, FakeSuspicionModel()), prepare)
    service.interactions = InteractionService(service, MemoryInteractionStore(), FakeIntentParser(), FakeLineRenderer())
    return service
