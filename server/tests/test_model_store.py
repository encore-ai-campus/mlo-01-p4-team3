"""B-09-01 1번 게임 웹 프로세스별 의심 모델 보관소 시험(가짜 worker 모델)."""
from unittest import TestCase
from unittest.mock import patch

from engine.ports import EngineDeps, FeatureVersionMismatch, ModelUnavailable
from engine.testing.fakes import FakeSuspicionModel
from server.app.model_versions import SUSPICION_KEY, pinned_dependencies
from server.infra.game_factory import suspicion_models
from server.infra.model_selector import ProcessSuspicionModels


class FakeWorkerModel:
    """worker를 가진 어댑터 대역. `fail_next`만큼 timeout처럼 실패하고 그 뒤 같은 버전으로 다시 답한다."""

    def __init__(self, version):
        self.model_version = version
        self.feature_version = "suspicion-1"
        self.fail_next = 0
        self.closed = False
        self.calls = 0

    def estimate(self, obs):
        """정해진 횟수만 실패하고 그 뒤 같은 버전으로 답한다."""
        self.calls += 1
        if self.closed:
            raise ModelUnavailable("closed")
        if self.fail_next:
            self.fail_next -= 1
            raise ModelUnavailable("suspicion inference unavailable")
        return ("estimate", self.model_version, obs)

    def close(self):
        """worker 종료 흉내."""
        self.closed = True


class Clock:
    """시험용 단조 시계. `now`를 바꿔 30초 대기를 흉내 낸다."""
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        """현재 시각."""
        return self.now


class Loader:
    """버전별 모델을 만들고, 지정한 실패를 차례로 낸다."""

    def __init__(self, failures=None):
        self.failures = dict(failures or {})
        self.made = []
        self.calls = []

    def __call__(self, version):
        """지정한 실패를 먼저 내고, 없으면 새 모델을 만든다."""
        self.calls.append(version)
        queue = self.failures.get(version) or []
        if queue:
            raise queue.pop(0)
        model = FakeWorkerModel(version)
        self.made.append(model)
        return model


def store_with(loader, active=("v1",), clock=None):
    """동기 로드 보관소와 바꿀 수 있는 운영 버전 집합."""
    active_set = set(active)
    store = ProcessSuspicionModels(loader, lambda: active_set, clock=clock or Clock(), background=False)
    return store, active_set


class ProcessSuspicionModelsTest(TestCase):
    """보관소의 버전별 worker 공유·폴백·닫기 규칙."""
    def test_one_worker_per_version_shared_by_games(self):
        """같은 버전의 판들은 한 모델(worker)을 빌려 쓰고, 판의 close는 worker를 닫지 않는다."""
        loader = Loader()
        store, _ = store_with(loader)
        a, b = store.for_game("g1", "v1"), store.for_game("g2", "v1")
        self.assertEqual(a.estimate("o")[1], "v1")
        self.assertEqual(b.estimate("o")[1], "v1")
        a.close()
        self.assertEqual(len(loader.made), 1)
        self.assertFalse(loader.made[0].closed)
        self.assertEqual(store.in_use(), {"v1": {"g1", "g2"}})

    def test_timeout_falls_back_only_that_call_and_same_version_answers_next(self):
        """timeout은 그 호출만 폴백하고, 다음 호출은 같은 버전 worker가 답한다(다른 버전으로 바꾸지 않음)."""
        loader = Loader()
        store, _ = store_with(loader)
        game = store.for_game("g1", "v1")
        loader.made[0].fail_next = 1
        with self.assertRaises(ModelUnavailable):
            game.estimate("o")
        self.assertEqual(game.estimate("o")[1], "v1")
        self.assertEqual(loader.calls, ["v1"])

    def test_transient_load_failure_falls_back_then_retries_with_cooldown(self):
        """일시적 로드 실패는 그동안만 폴백하고, 3회 연속 실패부터 30초 쉰 뒤 같은 버전을 다시 띄운다."""
        clock = Clock()
        loader = Loader({"v1": [ModelUnavailable("cannot load suspicion v1")] * 3})
        store, _ = store_with(loader, clock=clock)
        game = store.for_game("g1", "v1")          # 1회 실패
        for _ in range(2):                          # 다음 추론마다 즉시 재시도: 2·3회 실패
            with self.assertRaises(ModelUnavailable):
                game.estimate("o")
        self.assertEqual(len(loader.calls), 3)
        with self.assertRaises(ModelUnavailable):   # 30초 대기 중에는 다시 띄우지 않는다
            game.estimate("o")
        self.assertEqual(len(loader.calls), 3)
        clock.now += 30
        with self.assertRaises(ModelUnavailable):   # 대기가 끝나면 이 호출이 재기동을 시작한다(그 호출은 폴백)
            game.estimate("o")
        self.assertEqual(game.estimate("o")[1], "v1")
        self.assertEqual(loader.calls, ["v1"] * 4)

    def test_permanently_unloadable_version_falls_back_to_game_end(self):
        """파일 없음·특징 버전 불일치 버전은 다시 시도하지 않고 그 판 끝까지 폴백한다."""
        for error in (ModelUnavailable("missing suspicion-v0/v9/models.pkl"), FeatureVersionMismatch("x")):
            loader = Loader({"v9": [error]})
            store, _ = store_with(loader, active=("v1",))
            game = store.for_game("g1", "v9")
            for _ in range(5):
                with self.assertRaises(ModelUnavailable):
                    game.estimate("o")
            self.assertEqual(loader.calls, ["v9"])
            self.assertEqual(game.model_version, "v9")

    def test_running_game_keeps_pinned_version_after_rollback(self):
        """운영 버전이 바뀌어도(롤백) 진행 중 판은 고정 버전을 끝까지 쓰고, 그 판이 끝나면 옛 버전을 닫는다."""
        loader = Loader()
        store, active = store_with(loader, active=("v2",))
        old = store.for_game("g-old", "v2")
        old.estimate("o")
        active.clear()
        active.add("v1")                            # 롤백: 새 판은 v1
        new = store.for_game("g-new", "v1")
        self.assertEqual(new.estimate("o")[1], "v1")
        store.release("g-other")
        self.assertEqual(old.estimate("o")[1], "v2")
        store.release("g-old")
        v2 = next(m for m in loader.made if m.model_version == "v2")
        self.assertTrue(v2.closed)
        self.assertNotIn("v2", store.in_use())
        self.assertFalse(next(m for m in loader.made if m.model_version == "v1").closed)

    def test_game_end_keeps_worker_for_other_games_of_same_version(self):
        """한 판이 끝나도 같은 버전을 쓰는 다른 판이 있으면 worker를 닫지 않는다."""
        loader = Loader()
        store, active = store_with(loader, active=("v2",))
        a, b = store.for_game("g1", "v1"), store.for_game("g2", "v1")
        a.estimate("o")
        b.estimate("o")
        store.release("g1")
        self.assertFalse(loader.made[0].closed)
        self.assertEqual(b.estimate("o")[1], "v1")
        store.release("g2")
        self.assertTrue(loader.made[0].closed)

    def test_active_version_stays_open_without_games(self):
        """새 판에 배정하는 운영 버전은 쓰는 판이 없어도 닫지 않는다."""
        loader = Loader()
        store, _ = store_with(loader, active=("v1",))
        store.for_game("g1", "v1").estimate("o")
        store.release("g1")
        self.assertFalse(loader.made[0].closed)

    def test_close_closes_all_and_later_calls_fall_back(self):
        """프로세스 종료 때 모두 닫고, 그 뒤 추론은 폴백한다."""
        loader = Loader()
        store, _ = store_with(loader, active=("v1", "v2"))
        games = [store.for_game("g1", "v1"), store.for_game("g2", "v2")]
        store.close()
        store.close()
        self.assertTrue(all(m.closed for m in loader.made))
        for game in games:
            with self.assertRaises(ModelUnavailable):
                game.estimate("o")

    def test_pinned_dependencies_borrow_with_game_id(self):
        """판 메타의 의심 모델 버전으로 보관소에서 빌리고, 판 ID를 함께 넘겨 사용 판으로 센다."""
        from dataclasses import dataclass, field

        @dataclass
        class Meta:
            game_id: str
            model_versions: dict = field(default_factory=dict)

        loader = Loader()
        store, _ = store_with(loader)
        resolve = pinned_dependencies(lambda m: EngineDeps(object(), None), store)
        deps = resolve(Meta("g7", {SUSPICION_KEY: "v1"}))
        self.assertEqual(deps.suspicion.model_version, "v1")
        deps.suspicion.estimate("o")
        self.assertEqual(store.in_use(), {"v1": {"g7"}})
        self.assertIsNone(resolve(Meta("g8")).suspicion)


class SuspicionModelsSwitchTest(TestCase):
    """운영 조립의 실제 모델 켜기 설정."""
    def test_no_setting_uses_fake_only(self):
        """설정이 없으면 운영 버전은 규칙 의심도 모델이고 실제 모델을 읽지 않는다."""
        fake = FakeSuspicionModel()
        with patch("server.infra.model_selector.LocalModelSelector.suspicion_model") as load:
            store, production = suspicion_models(fake, "")
        self.assertEqual(production, fake.model_version)
        self.assertIs(store.for_game("g", fake.model_version)._store, store)
        load.assert_not_called()
        store.close()

    def test_setting_preloads_real_version(self):
        """설정 버전을 시작 때 미리 띄우면 그 버전이 새 판 운영 버전이 된다."""
        fake = FakeSuspicionModel()
        model = FakeWorkerModel("suspicion-logistic-abc")
        with patch("server.infra.model_selector.LocalModelSelector.suspicion_model", return_value=model) as load:
            store, production = suspicion_models(fake, "suspicion-logistic-abc", "mlruns")
        self.assertEqual(production, "suspicion-logistic-abc")
        load.assert_called_once_with("suspicion-logistic-abc", in_process_logistic=True)
        game = store.for_game("g1", production)
        self.assertEqual(game.estimate("o")[1], "suspicion-logistic-abc")
        store.close()
        self.assertTrue(model.closed)

    def test_setting_with_unloadable_model_starts_with_fake(self):
        """설정 버전을 못 띄우면 경고를 남기고 지금처럼 규칙 의심도로 시작한다."""
        fake = FakeSuspicionModel()
        with patch("server.infra.model_selector.LocalModelSelector.suspicion_model",
                   side_effect=ModelUnavailable("missing suspicion-v0/x/report.json")):
            store, production = suspicion_models(fake, "x", "mlruns")
        self.assertEqual(production, fake.model_version)
        store.close()


class GameEndReleaseTest(TestCase):
    """게임 서비스가 판 종료를 보관소에 넘김."""
    def test_game_end_releases_game_from_store(self):
        """녹화 판을 끝까지 진행하면 게임 서비스가 판 종료 때 한 번 보관소 `release`를 부른다."""
        import uuid
        from server.api.input_validation import parse_game_input
        from server.app.dto import StartGameInput, SubmitCommandInput
        from .support import make_service
        service = make_service()
        released = []
        service.release_game = released.append
        pid = str(uuid.uuid4())
        game = service.start(StartGameInput(pid, "base6", "test-session", "f"), pid, True).view.game_id
        for step in service.engine.steps[1:]:
            body = dict(step["request"], game_id=game, command_id=str(uuid.uuid4()))
            out = service.command(parse_game_input(SubmitCommandInput, body), pid)
            self.assertTrue(out.accepted)
            if out.view.result is None:
                self.assertEqual(released, [])
        self.assertEqual(released, [game])

    def test_release_failure_does_not_change_response(self):
        """보관소 정리 실패는 판 종료 응답을 막지 않는다."""
        from .support import make_service
        service = make_service()
        service.release_game = lambda game_id: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertLogs("paldo.progress", level="ERROR"):
            service._release("g1")


class StoreRaceAndRollbackTest(TestCase):
    """동시 로드와 설정 롤백."""
    def test_concurrent_resolves_load_one_worker(self):
        """같은 버전을 동시에 요청해도 로드 중에는 다시 띄우지 않아 worker가 하나만 생긴다."""
        import threading
        gate, started = threading.Event(), threading.Event()
        made = []

        def slow(version):
            started.set()
            gate.wait(5)
            model = FakeWorkerModel(version)
            made.append(model)
            return model
        store = ProcessSuspicionModels(slow, lambda: {"v1"}, background=True)
        store.for_game("g1", "v1")
        started.wait(5)
        for n in range(5):
            store.for_game(f"g{n + 2}", "v1")
        gate.set()
        for _ in range(100):
            if made and store.for_game("gx", "v1") and store._entries["v1"].model is not None:
                break
            threading.Event().wait(0.01)
        self.assertEqual(len(made), 1)
        store.close()

    def test_rollback_setting_keeps_pinned_real_version_loadable(self):
        """운영 설정을 비워도(롤백) 이미 실제 버전을 고정한 판은 그 버전을 다시 띄울 수 있다."""
        fake = FakeSuspicionModel()
        model = FakeWorkerModel("suspicion-logistic-abc")
        with patch("server.infra.model_selector.LocalModelSelector.suspicion_model", return_value=model) as load:
            store, production = suspicion_models(fake, "", "mlruns")
            store._background = False
            game = store.for_game("g-old", "suspicion-logistic-abc")
        self.assertEqual(production, fake.model_version)
        load.assert_called_once_with("suspicion-logistic-abc", in_process_logistic=True)
        self.assertEqual(game.estimate("o")[1], "suspicion-logistic-abc")
        store.close()
