"""웹 테스트에서만 사용하는 포트 조립과 승인된 시드 배정 대역."""
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.utils import timezone

from engine.domain.model import DataSource, GameMeta, PRESETS
from engine.ports import EngineDeps
from engine.testing.fakes import (FixtureContentSource, FakeSuspicionModel,
                                  InMemoryGameRepository, InMemoryProgressRepository, ScriptedGameEngine)
from server.app.usecases import GameService
from server.app.interactions import InteractionService
from engine.testing.fakes import FakeIntentParser, FakeLineRenderer
from .interaction_store import MemoryInteractionStore


def install_test_profiles(test):
    """Django 세션의 실제 사용자에 시험 전용 프로필 관계를 연결한다."""
    profiles = {}
    test.enterContext(patch.object(User, "paldo_profile", property(lambda user: profiles.get(user.pk)), create=True))

    def register(user):
        profile = SimpleNamespace(player_pseudonym=uuid4(), consented_at=timezone.now())
        profiles[user.pk] = profile
        return profile

    return register


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
