"""B-09-01 모델 버전 고정·카나리아·해석 버전 경보·폴백 지표·업데이트 내역 시험."""
from dataclasses import replace
from unittest import TestCase

from django.test import SimpleTestCase
from django.template.loader import render_to_string
from prometheus_client import REGISTRY, generate_latest

from engine.domain.events import DomainEvent, EventType
from engine.domain.model import Command, CommandKind, DataSource, GameMeta, PRESETS, Phase
from engine.ports import EngineDeps, ModelUnavailable
from engine.testing.fakes import FakeSuspicionModel
from server.app.dto import ParseUtteranceInput, StartGameInput, SubmitCommandInput
from server.app.model_versions import (INTENT_KEY, SUSPICION_KEY, PromotionRecord, VersionPinning, canary_bucket,
                                       parse_version_mismatch, pinned_dependencies, update_history,
                                       warn_parse_version_mismatch)
from server.infra.game_factory import attach_model_versions
from server.infra.model_registry import LoadedSuspicionModels, StaticModelRegistry, UnavailableSuspicionModel
from server.paldo_web.composition import configure_game_service
from server.paldo_web.engine_metrics import FALLBACK_METRIC, observe_engine_events
from .support import make_service

PROD = {INTENT_KEY: "intent-v1", SUSPICION_KEY: "fake-suspicion-1"}
CANARY = {SUSPICION_KEY: "suspicion-v2"}


def meta(game_id="g-1", session_id=None, **values):
    """버전 고정 전 새 판 메타."""
    return GameMeta(game_id, PRESETS["base6"], 1, DataSource.HUMAN, "rule", "engine", "content",
                    session_id=session_id, **values)


def counter_value():
    """폴백 카운터 현재 값(없으면 0)."""
    return REGISTRY.get_sample_value(FALLBACK_METRIC) or 0.0


def fallback_event():
    """의심 모델 폴백으로 정한 투표 결정 이벤트."""
    return DomainEvent(EventType.DECISION, 1, Phase.VOTE, {"policy_version": "rule-0.6.decision-1-fallback"})


class Pinning(TestCase):
    """판 시작 버전 고정과 카나리아 배정 규칙(ml_lifecycle.md 4절)."""

    def test_new_game_gets_production_versions_without_canary(self):
        """비율 0(기본)이면 운영 버전만 고정하고 카나리아 표시는 거짓이다."""
        pinned = VersionPinning(StaticModelRegistry(PROD, CANARY))(meta())
        self.assertEqual((pinned.model_versions, pinned.canary), (PROD, False))

    def test_existing_versions_are_never_reselected(self):
        """이미 버전이 있는 메타는 레지스트리가 바뀌어도 그대로다."""
        fixed = meta(model_versions={SUSPICION_KEY: "old"})
        self.assertIs(VersionPinning(StaticModelRegistry(PROD, CANARY), lambda: 1.0)(fixed), fixed)

    def test_user_test_session_disables_canary_even_at_full_ratio(self):
        """사용자 테스트 회차 판은 비율 1이어도 카나리아를 배정하지 않는다."""
        pinned = VersionPinning(StaticModelRegistry(PROD, CANARY), lambda: 1.0)(meta(session_id="nov10"))
        self.assertEqual((pinned.model_versions, pinned.canary), (PROD, False))

    def test_open_game_canary_merges_canary_versions(self):
        """일반 판이 카나리아에 배정되면 카나리아 버전이 운영 버전을 덮고 표시가 켜진다."""
        pinned = VersionPinning(StaticModelRegistry(PROD, CANARY), lambda: 1.0)(meta())
        self.assertEqual(pinned.model_versions, {**PROD, **CANARY})
        self.assertTrue(pinned.canary)

    def test_canary_needs_registered_canary_version(self):
        """카나리아 버전이 없으면 비율과 무관하게 운영 버전이다."""
        pinned = VersionPinning(StaticModelRegistry(PROD), lambda: 1.0)(meta())
        self.assertEqual((pinned.model_versions, pinned.canary), (PROD, False))

    def test_canary_bucket_is_stable_and_ratio_bounded(self):
        """같은 판 ID면 같은 배정, 비율 20%면 대략 20%가 카나리아다."""
        self.assertEqual(canary_bucket("same"), canary_bucket("same"))
        policy = VersionPinning(StaticModelRegistry(PROD, CANARY), lambda: 0.2)
        share = sum(policy(meta(str(i))).canary for i in range(2000)) / 2000
        self.assertTrue(0.15 < share < 0.25, share)

    def test_pinned_dependencies_use_meta_version(self):
        """엔진 의존성의 의심 모델은 판 메타의 버전으로 고른다. 없는 버전은 사용 불가 모델이다."""
        loaded = FakeSuspicionModel()
        deps = pinned_dependencies(lambda m: EngineDeps(object(), None), LoadedSuspicionModels(loaded))
        self.assertIs(deps(meta(model_versions={SUSPICION_KEY: loaded.model_version})).suspicion, loaded)
        missing = deps(meta(model_versions={SUSPICION_KEY: "rolled-away"})).suspicion
        self.assertIsInstance(missing, UnavailableSuspicionModel)
        with self.assertRaises(ModelUnavailable):
            missing.estimate(None)
        self.assertIsNone(deps(meta()).suspicion)


class ServiceVersions(TestCase):
    """GameService에 끼운 버전 고정과 관측 함수."""

    def setUp(self):
        """녹화 판 서비스에 버전 고정을 끼운다."""
        self.registry = StaticModelRegistry(dict(PROD))
        self.service = attach_model_versions(make_service(), self.registry,
                                             LoadedSuspicionModels(FakeSuspicionModel()), lambda: 1.0)
        self.events = []
        self.service.observe_events = self.events.append

    def start(self, player="player"):
        """테스트 회차 판을 시작한다."""
        return self.service.start(StartGameInput(player, "base6", "test-session", "f"), player, True)

    def test_start_pins_versions_and_rollback_keeps_running_game(self):
        """시작 때 고정한 버전은 레지스트리 롤백 뒤 명령을 처리해도 바뀌지 않는다."""
        game = self.start()
        state, _ = self.service.repository.load(game.view.game_id)
        self.assertEqual((state.meta.model_versions, state.meta.canary), (PROD, False))
        self.registry._production[SUSPICION_KEY] = "previous-suspicion"
        self.service.command(SubmitCommandInput(game.view.game_id, "c1", 0, Command(CommandKind.ADVANCE, "mudang")),
                             "player")
        after, _ = self.service.repository.load(game.view.game_id)
        self.assertEqual(after.meta.model_versions, PROD)
        self.assertGreater(after.state_version, 0)

    def test_observer_receives_saved_events_once_per_request(self):
        """시작·명령이 저장한 엔진 이벤트만 관측 함수에 넘기고, 재전송(replay)은 다시 넘기지 않는다."""
        game = self.start()
        data = SubmitCommandInput(game.view.game_id, "c1", 0, Command(CommandKind.ADVANCE, "mudang"))
        self.service.command(data, "player")
        self.service.command(data, "player")
        self.assertEqual(len(self.events), 2)

    def test_observer_failure_does_not_break_command(self):
        """지표 실패는 저장된 명령 응답을 막지 않는다."""
        def broken(events):
            raise RuntimeError("metrics down")
        self.service.observe_events = broken
        game = self.start()
        out = self.service.command(SubmitCommandInput(game.view.game_id, "c1", 0,
                                                      Command(CommandKind.ADVANCE, "mudang")), "player")
        self.assertTrue(out.accepted)

    def test_parse_warns_when_parser_model_differs_from_pinned(self):
        """해석기 모델(fake-intent-1)과 판에 고정한 의도 해석기 버전(intent-v1)이 다르면 경보 로그를 남긴다."""
        game = self.start()
        with self.assertLogs("paldo.web.model_versions", "WARNING") as logs:
            self.service.interactions.parse(ParseUtteranceInput(game.view.game_id, 0, "대장장이가 수상하다"), "player")
        self.assertIn("intent_model_version_mismatch", logs.output[0])


class ParseMismatch(TestCase):
    """해석 버전 비교 규칙."""

    def test_rules(self):
        """같거나, 판에 의도 해석기 버전이 없거나, 해석 결과에 모델 버전이 없으면 경보 없음."""
        pinned = meta(model_versions={INTENT_KEY: "v1"})
        self.assertIsNone(parse_version_mismatch(pinned, {"model": "v1"}))
        self.assertIsNone(parse_version_mismatch(meta(), {"model": "v2"}))
        self.assertIsNone(parse_version_mismatch(pinned, {}))
        self.assertEqual(parse_version_mismatch(pinned, {"model": "v2"}),
                         {"game_id": "g-1", "pinned_model": "v1", "parse_model": "v2"})

    def test_warning_level(self):
        """불일치 경보는 warning 수준이다."""
        with self.assertLogs("paldo.web.model_versions", "WARNING") as logs:
            self.assertTrue(warn_parse_version_mismatch(meta(model_versions={INTENT_KEY: "v1"}), {"model": "v2"}))
        self.assertEqual(logs.records[0].levelname, "WARNING")
        self.assertEqual(logs.records[0].fields["parse_model"], "v2")


class FallbackMetric(TestCase):
    """엔진 폴백 결정 수를 `/metrics` 카운터로 노출한다."""

    def test_counter_increases_by_fallback_decisions(self):
        """폴백 결정 2개와 일반 결정 1개면 카운터가 2 오른다."""
        before = counter_value()
        normal = DomainEvent(EventType.DECISION, 1, Phase.VOTE, {"policy_version": "rule-0.6.decision-1"})
        self.assertEqual(observe_engine_events([fallback_event(), normal, fallback_event()]), 2)
        self.assertEqual(counter_value() - before, 2)

    def test_metrics_endpoint_exposes_counter(self):
        """기본 레지스트리 출력에 카운터 이름이 있다."""
        self.assertIn(FALLBACK_METRIC.encode(), generate_latest())

    def test_composition_attaches_observer(self):
        """게임 서비스 조립이 관측 함수가 비어 있으면 폴백 지표 관측을 끼운다."""
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.assertIs(service.observe_events, observe_engine_events)
        before = counter_value()
        game = service.start(StartGameInput("p", "base6", "test-session", "f"), "p", True)
        engine_apply = service.engine.apply

        def apply_with_fallback(*args):
            result = engine_apply(*args)
            return replace(result, events=[*result.events, fallback_event()])
        service.engine.apply = apply_with_fallback
        service.command(SubmitCommandInput(game.view.game_id, "c1", 0, Command(CommandKind.ADVANCE, "mudang")), "p")
        self.assertEqual(counter_value() - before, 1)


class UpdateHistoryScreen(SimpleTestCase):
    """게임 화면 안 업데이트 내역 패널."""

    def test_history_sorted_recent_first_and_limited(self):
        """최근 승격부터, 개수 제한."""
        records = [PromotionRecord(SUSPICION_KEY, f"v{i}", "production", f"2026-11-0{i}T00:00:00Z") for i in range(1, 6)]
        history = update_history(StaticModelRegistry(PROD, promotions=records), limit=3)
        self.assertEqual([r.version for r in history], ["v5", "v4", "v3"])
        self.assertEqual(update_history(None), [])

    def test_panel_renders_world_phrase_and_records(self):
        """세계관 문구와 기록(날짜·모델·버전·단계)을 보이고, 없으면 빈 내역 안내."""
        html = render_to_string("update_history.html", {"records": [
            {"at": "2026-11-09T10:00:00Z", "model": "suspicion", "version": "v2", "stage": "canary",
             "label": "의심 판단", "stage_label": "시험 적용"}]})
        self.assertIn("창귀가 무당들의 수법을 기억했다", html)
        self.assertIn("2026-11-09 · 의심 판단 · v2 · 시험 적용", html)
        self.assertIn('id="update-history"', html)
        empty = render_to_string("update_history.html", {"records": []})
        self.assertIn("아직 기록된 업데이트가 없습니다.", empty)

    def test_tag_reads_service_registry(self):
        """화면 태그는 조립된 게임 서비스의 레지스트리에서 승격 기록을 읽는다."""
        from server.api.template_tags import update_history as tag
        service = make_service()
        service.model_registry = StaticModelRegistry(PROD, promotions=[
            PromotionRecord(INTENT_KEY, "intent-v2", "production", "2026-11-09T09:00:00Z")])
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.assertEqual(tag()["records"][0]["label"], "의도 해석")
        configure_game_service(None)
        self.assertEqual(tag()["records"], [])
