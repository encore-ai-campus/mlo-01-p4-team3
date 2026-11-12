"""씐 자 전략 밴딧 운영 연결 시험(D-09-02, 11/12 사용자 결정 동결 예외).

판 시작 때 버전·팔 고정(응용), 판 기록에서 팔 복원(인프라 혼합 공급자), 게시 상태 읽기, 하루 갱신 배치의
동의·삭제 처리, 개인정보 삭제 연결, 운영 조립 스위치를 가짜 저장소로 검사한다.
"""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.test import override_settings

from engine.domain.events import DomainEvent, EventType
from engine.domain.model import DataSource, GameMeta, PRESETS, Phase
from engine.domain.npc.policy_possessed import StrategyMix
from engine.domain.params import RULE_BASE_VERSION
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, InMemoryGameRepository, InMemoryProgressRepository
from ml import bandit
from server.app.dto import StartGameInput
from server.app.model_versions import BANDIT_ARM_KEY, BANDIT_KEY, BanditPinning
from server.app.usecases import GameService
from server.infra.game_factory import ContentPolicyEngine
from server.infra.possessed_bandit import (MongoBanditChooser, PinnedMixSource, attach_possessed_bandit,
                                           forget_bandit_games, run_update)
from tools.check_content import load_bundle

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = load_bundle(ROOT / "content/bundle_v1.json")
ARMS = bandit.load_arms()
ARM = sorted(ARMS)[3]


def meta(game_id="g-1", source=DataSource.HUMAN, session_id=None, **values):
    """버전 고정 전 새 판 메타(운영 콘텐츠·기본 규칙)."""
    return GameMeta(game_id, PRESETS["base6"], 1, source, RULE_BASE_VERSION, "engine", BUNDLE.content_version,
                    session_id=session_id, **values)


class FixedChooser:
    """정해진 (버전, 팔)을 돌려주거나 실패하는 선택기 대역."""

    def __init__(self, result=("bandit-2", ARM), error=None):
        self.result, self.error, self.calls = result, error, []

    def choose(self, game_id):
        self.calls.append(game_id)
        if self.error:
            raise self.error
        return self.result


class Pinning(TestCase):
    """판 시작 고정 규칙(응용 계층)."""

    def test_open_human_game_gets_version_and_arm_after_inner_pinning(self):
        inner = lambda m: replace(m, model_versions={"suspicion": "s1"})
        pinned = BanditPinning(inner, FixedChooser())(meta())
        self.assertEqual(pinned.model_versions, {"suspicion": "s1", BANDIT_KEY: "bandit-2", BANDIT_ARM_KEY: ARM})

    def test_excluded_games_keep_uniform_without_record(self):
        """끔·부하 시험·회차 판·튜토리얼(SCRIPTED)·이미 고정된 판은 고르지 않는다."""
        fixed = {BANDIT_KEY: "bandit-1", BANDIT_ARM_KEY: ARM}
        cases = [(BanditPinning(None, FixedChooser(), enabled=lambda: False), meta()),
                 (BanditPinning(None, FixedChooser(), load_test=lambda: True), meta()),
                 (BanditPinning(None, FixedChooser()), meta(session_id="nov10")),
                 (BanditPinning(None, FixedChooser()), meta(source=DataSource.SCRIPTED)),
                 (BanditPinning(None, FixedChooser()), meta(model_versions=fixed))]
        for pinning, m in cases:
            with self.subTest(m=m):
                self.assertIs(pinning(m), m)
                self.assertEqual(pinning.chooser.calls, [])

    def test_chooser_failure_starts_uniform_and_logs(self):
        m = meta()
        with self.assertLogs("paldo.web.model_versions", "WARNING") as logs:
            self.assertIs(BanditPinning(None, FixedChooser(error=RuntimeError("down")))(m), m)
        self.assertIn("possessed_bandit_unavailable", logs.output[0])


class FakeStateCollection:
    """밴딧 상태 문서 하나를 가진 컬렉션 대역."""

    def __init__(self, doc=None):
        self.doc, self.reads = doc, 0

    def find_one(self, query, *args):
        self.reads += 1
        return copy.deepcopy(self.doc)


class Chooser(TestCase):
    """게시 상태 읽기(인프라)."""

    def test_missing_state_uses_bandit_zero_uniform_over_fixed_arms(self):
        expected = bandit.VersionedMixSource(bandit.initial_state(ARMS), "bandit-0").arm_for("g-1")
        self.assertEqual(MongoBanditChooser(FakeStateCollection(), ARMS).choose("g-1"), ("bandit-0", expected))

    def test_latest_published_ratios_and_ttl_cache(self):
        state = bandit.initial_state(ARMS)
        state["versions"].append(dict(version="bandit-1", ratios={a: float(a == ARM) for a in ARMS}))
        now = [0.0]
        collection = FakeStateCollection(state)
        chooser = MongoBanditChooser(collection, ARMS, ttl=60, clock=lambda: now[0])
        self.assertEqual([chooser.choose(f"g{i}") for i in range(5)], [("bandit-1", ARM)] * 5)
        self.assertEqual(collection.reads, 1)
        now[0] = 61
        chooser.choose("g9")
        self.assertEqual(collection.reads, 2)

    def test_different_arms_in_state_fail_so_game_starts_uniform(self):
        other = bandit.initial_state()
        other["arms"] = {ARM: other["arms"][ARM]}
        with self.assertRaises(ValueError):
            MongoBanditChooser(FakeStateCollection(other), ARMS).choose("g-1")


class PinnedMix(TestCase):
    """판 기록에서 팔 복원(인프라 혼합 공급자)."""

    def test_recorded_arm_is_used_and_unknown_or_missing_is_uniform(self):
        source = PinnedMixSource(ARMS)
        source.note(meta("a", model_versions={BANDIT_KEY: "bandit-1", BANDIT_ARM_KEY: ARM}))
        source.note(meta("b", model_versions={BANDIT_KEY: "bandit-1", BANDIT_ARM_KEY: "nope"}))
        source.note(meta("c"))
        self.assertEqual(source.mix_for("a"), StrategyMix(**{**ARMS[ARM], "mix_id": ARM}))
        self.assertEqual(source.mix_for("b"), StrategyMix())
        self.assertEqual(source.mix_for("c"), StrategyMix())

    def test_table_is_bounded(self):
        source = PinnedMixSource(ARMS, limit=2)
        for gid in ("a", "b", "c"):
            source.note(meta(gid, model_versions={BANDIT_KEY: "bandit-1", BANDIT_ARM_KEY: ARM}))
        self.assertEqual(source.mix_for("a"), StrategyMix())
        self.assertEqual(source.mix_for("c").mix_id, ARM)


class ServiceWiring(TestCase):
    """운영 엔진 조립 + 메모리 저장소: 시작 고정, 이벤트 봉투, 다른 프로세스 복원."""

    @staticmethod
    def service():
        engine = ContentPolicyEngine(mix_source=PinnedMixSource(ARMS))
        return GameService(engine, InMemoryGameRepository(), InMemoryProgressRepository(),
                           lambda m: EngineDeps(BUNDLE, FakeSuspicionModel()),
                           lambda data, player: meta("", player_pseudonym=player))

    def test_start_records_arm_and_events_carry_it_and_other_process_restores(self):
        service = self.service()
        service.pin_versions = BanditPinning(service.pin_versions, FixedChooser())
        out = service.start(StartGameInput("p1", "base6", None, "f"), "p1", True)
        gid = out.view.game_id
        state, _ = service.repository.load(gid)
        self.assertEqual(state.meta.model_versions, {BANDIT_KEY: "bandit-2", BANDIT_ARM_KEY: ARM})
        outbox = service.repository.docs[gid]["outbox"]
        self.assertTrue(outbox and all(e.model_versions[BANDIT_ARM_KEY] == ARM for e in outbox))
        ended = service._events(state, [DomainEvent(EventType.GAME_ENDED, 2, Phase.ENDED, {"winner": "village"}),
                                        DomainEvent(EventType.SURVEY_SUBMITTED, 2, Phase.ENDED, {})])
        self.assertEqual([e.model_versions[BANDIT_KEY] for e in ended], ["bandit-2", "bandit-2"])
        policy = service.engine._for(BUNDLE, state.meta.rule_version).policy
        self.assertEqual(policy.possessed.mix(gid).mix_id, ARM)
        # 다른 웹 프로세스(새 조립, 빈 캐시): 판 기록만으로 같은 팔을 쓴다.
        other = ContentPolicyEngine(mix_source=PinnedMixSource(ARMS))
        other.player_view(state, BUNDLE)
        self.assertEqual(other._for(BUNDLE, state.meta.rule_version).policy.possessed.mix(gid).mix_id, ARM)

    def test_attach_reads_missing_state_as_bandit_zero(self):
        service = attach_possessed_bandit(self.service(), {"possessed_bandit": FakeStateCollection()}, "", ARMS,
                                          lambda: True)
        out = service.start(StartGameInput("p1", "base6", None, "f"), "p1", True)
        state, _ = service.repository.load(out.view.game_id)
        self.assertEqual(state.meta.model_versions[BANDIT_KEY], "bandit-0")
        self.assertIn(state.meta.model_versions[BANDIT_ARM_KEY], ARMS)

    def test_engine_without_mix_source_is_uniform(self):
        policy = ContentPolicyEngine()._for(BUNDLE, RULE_BASE_VERSION).policy
        self.assertEqual(policy.possessed.mix("x"), StrategyMix())


START = datetime(2026, 11, 12, tzinfo=timezone.utc)


def game_doc(i, player, arm=ARM, survey=True):
    """판 저장소 문서 하나(아웃박스 봉투: 종료·밤·설문)."""
    versions = {"suspicion": "s", BANDIT_KEY: "bandit-0", BANDIT_ARM_KEY: arm}
    base = dict(game_id=f"g{i:03}", source="human", load_test=False, player_pseudonym=player,
                model_versions=versions, occurred_at=(START + timedelta(seconds=i)).isoformat())
    rows = [dict(base, event_id=f"g{i:03}:1", event_type="game_ended", topic="paldo.game", phase="ended",
                 payload={"winner": "village" if i % 2 else "changgwi"}),
            dict(base, event_id=f"g{i:03}:0", event_type="night_resolved", topic="paldo.game", phase="night",
                 payload={})]
    if survey:
        rows.append(dict(base, event_id=f"g{i:03}:2", event_type="survey_submitted", topic="paldo.player",
                         phase="ended", payload=dict(skipped=False, fun=4, unfair=2, npc_plausible=4)))
    return {"_id": f"g{i:03}", "outbox": [{"envelope": e} for e in rows]}


class FakeGames:
    """판 컬렉션 대역(조회 조건을 기록)."""

    def __init__(self, docs):
        self.docs, self.queries = docs, []

    def find(self, query, projection):
        self.queries.append(query)
        return iter(copy.deepcopy(self.docs))


class MemoryStore:
    """`MongoBanditStore`와 같은 동작의 메모리 대역(ml.bandit 규칙 그대로)."""

    def __init__(self, arms):
        self.state = bandit.initial_state(arms)

    def load(self):
        return copy.deepcopy(self.state)

    def forget_games(self, ids):
        self.state = bandit.forget_games(self.state, ids)

    def update(self, games, *, seed=7):
        updated = bandit.propose_update(self.state, games, seed=seed)
        if updated is None:
            return None
        self.state = updated
        return updated["versions"][-1]


class DailyUpdate(TestCase):
    """하루 갱신 배치(인프라 조립): 동의 판만, 삭제·동의 철회 판은 먼저 뺀다."""

    def test_publishes_from_consented_games_only_and_filters_query(self):
        docs = [game_doc(i, f"u{i}") for i in range(22)] + [game_doc(99, "stranger")]
        games, store = FakeGames(docs), MemoryStore(ARMS)
        self.assertEqual(run_update(games, store, {f"u{i}" for i in range(22)}), (True, 0, 22))
        self.assertEqual(store.state["versions"][-1]["version"], "bandit-1")
        self.assertNotIn("g099", store.state["game_fingerprints"])
        self.assertEqual(games.queries[0]["state.meta.source"], "human")

    def test_below_twenty_responses_does_not_publish(self):
        store = MemoryStore(ARMS)
        result = run_update(FakeGames([game_doc(i, f"u{i}") for i in range(5)]), store, {f"u{i}" for i in range(5)})
        self.assertEqual(result, (False, 0, 5))
        self.assertEqual(store.state["versions"], [])

    def test_deleted_or_revoked_games_are_forgotten_before_next_update(self):
        store = MemoryStore(ARMS)
        users = {f"u{i}" for i in range(22)}
        run_update(FakeGames([game_doc(i, f"u{i}") for i in range(22)]), store, users)
        # g000 판 삭제, u1 동의 철회(잠금). 그대로 두면 propose_update가 "과거 판 누락"으로 거부한다.
        remaining = [game_doc(i, f"u{i}") for i in range(1, 22)]
        self.assertEqual(run_update(FakeGames(remaining), store, users - {"u1"}), (False, 2, 20))
        self.assertFalse({"g000", "g001"} & set(store.state["game_fingerprints"]))
        self.assertFalse({"g000", "g001"} & set(store.state["versions"][0]["game_ids"]))


class PrivacyForget(TestCase):
    """개인정보 삭제 단계에서 밴딧 상태의 판도 바로 뺀다."""

    def test_missing_state_is_noop_and_known_ids_are_forgotten(self):
        empty = MagicMock()
        empty.__getitem__.return_value.find_one.return_value = None
        self.assertEqual(forget_bandit_games(empty, ["g1"]), 0)
        state = bandit.initial_state(ARMS)
        state["game_fingerprints"] = {"g1": "x", "g2": "y"}
        database = MagicMock()
        database.__getitem__.return_value.find_one.return_value = state
        with patch("ml.mongo_bandit.MongoBanditStore") as store:
            self.assertEqual(forget_bandit_games(database, ["g1", "g9"]), 1)
        store.return_value.forget_games.assert_called_once_with({"g1"})


class Composition(TestCase):
    """운영 조립: 스위치와 관계없이 혼합 공급자를 끼우고, 새 판 고정만 스위치를 따른다."""

    def build(self):
        from server.infra.game_factory import build_game_service
        runtime = MagicMock(collection_names=["games", "progress", "content", "b_test_sessions", "b_ad_identities"])
        runtime.factory_service.return_value.interactions = None
        with patch("server.paldo_web.mongo.open_mongo_runtime", return_value=runtime) as opened, \
                patch("intent.parser.BaselineIntentParser", return_value=MagicMock()):
            built = build_game_service()
        return built, opened.call_args.kwargs["engine"]

    def test_switch_controls_new_pinning_only(self):
        for flag in (False, True):
            with self.subTest(flag=flag), override_settings(POSSESSED_BANDIT=flag):
                built, engine = self.build()
                self.assertIsInstance(engine.mix_source, PinnedMixSource)
                self.assertIsInstance(built.pin_versions, BanditPinning)
                self.assertIs(built.pin_versions.enabled(), flag)
