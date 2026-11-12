"""씐 자 전략 밴딧의 운영 연결(인프라, D-09-02 · 11/12 사용자 결정 동결 예외).

- `MongoBanditChooser`: 새 판 시작 때 Mongo에 게시된 밴딧 상태(없으면 `bandit-0`, 확정 팔 균등)를 읽어
  (버전, 팔)을 고른다. 응용의 `BanditPinning`이 이 값을 `GameMeta.model_versions`에 고정한다.
- `PinnedMixSource`: 엔진 정책(`RuleV0Policy(mix_source=...)`)에 끼우는 혼합 비율 공급자. 엔진 호출 직전에
  `ContentPolicyEngine`이 판 메타를 알려 주면(`note`), 그 판에 고정된 팔의 `StrategyMix`를 돌려준다.
  판 기록에서 복원하므로 웹 프로세스 2개·재시작·밴딧 갱신 뒤에도 같은 판은 같은 팔을 쓴다. 기록 없는 판은 균등.
- `run_update`: 매일 갱신 배치. 판 저장소의 아웃박스 이벤트에서 밴딧 기록이 있는 동의 사람 판만 모아
  `MongoBanditStore`로 게시한다. 지워졌거나 동의가 빠진 판은 먼저 `forget_games`로 뺀다.

계층: 인프라. 규칙(보상·게시·팔 고르기)은 `ml/bandit.py`가 갖고, 여기는 Mongo 읽기·쓰기와 조립만 한다.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

from engine.domain.npc.policy_possessed import StrategyMix
from ml.bandit import VersionedMixSource, initial_state, latest_version
from server.app.model_versions import BANDIT_ARM_KEY, BANDIT_KEY

STATE_ID = "bandit"


class MongoBanditChooser:
    """게시된 밴딧 상태로 새 판의 (버전, 팔)을 고른다. 상태는 `ttl`초 동안 프로세스에 보관한다."""

    def __init__(self, collection, arms: dict, ttl: float = 60.0, clock=time.monotonic):
        self.collection = collection
        self.arms = arms
        self.ttl = ttl
        self.clock = clock
        self._expected = initial_state(arms)["arms"]
        self._cached = None
        self._at = None
        self._lock = threading.Lock()

    def _state(self):
        with self._lock:
            now = self.clock()
            if self._cached is None or self._at is None or now - self._at >= self.ttl:
                doc = self.collection.find_one({"_id": STATE_ID})
                state = doc if doc is not None else initial_state(self.arms)
                if state["arms"] != self._expected:
                    raise ValueError("bandit_arms_mismatch")
                self._cached, self._at = state, now
            return self._cached

    def choose(self, game_id: str) -> tuple[str, str]:
        """(버전, 팔). 같은 판 ID·같은 게시 상태면 같은 결과다."""
        state = self._state()
        version = latest_version(state)
        return version, VersionedMixSource(state, version).arm_for(game_id)


class PinnedMixSource:
    """판 메타에 고정된 팔로 씐 자 혼합 비율을 돌려준다. 기록이 없거나 모르는 팔이면 균등(기존 동작).

    판별 팔 표는 최근 `limit`개 판만 둔다. 정책은 판마다 처음 한 번만 `mix_for`를 부르고 그 값을 보관하므로,
    오래된 판이 표에서 밀려나도 이미 시작한 판의 동작은 바뀌지 않는다.
    """

    def __init__(self, arms: dict, limit: int = 4096):
        self.arms = arms
        self.limit = limit
        self._by_game: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def note(self, meta) -> None:
        """엔진 호출 직전에 그 판의 메타를 알려 준다(`ContentPolicyEngine`)."""
        versions = meta.model_versions or {}
        arm = versions.get(BANDIT_ARM_KEY)
        if arm not in self.arms or not versions.get(BANDIT_KEY):
            return
        with self._lock:
            self._by_game[meta.game_id] = arm
            self._by_game.move_to_end(meta.game_id)
            while len(self._by_game) > self.limit:
                self._by_game.popitem(last=False)

    def mix_for(self, game_id: str) -> StrategyMix:
        """그 판의 혼합 비율. 정책이 판마다 한 번 부르고 판 동안 보관한다."""
        with self._lock:
            arm = self._by_game.get(game_id)
        if arm is None:
            return StrategyMix()
        return StrategyMix(**{**self.arms[arm], "mix_id": arm})


def bandit_envelopes(games_collection):
    """판 저장소에서 밴딧 기록이 있는 사람 판의 종료·설문 이벤트 봉투만 읽는다(읽기 전용)."""
    cursor = games_collection.find({"state.meta.source": "human",
                                    f"state.meta.model_versions.{BANDIT_ARM_KEY}": {"$exists": True}},
                                   {"outbox.envelope": 1})
    for document in cursor:
        for row in document.get("outbox") or []:
            envelope = row.get("envelope") or {}
            versions = envelope.get("model_versions") or {}
            if (envelope.get("event_type") in ("game_ended", "survey_submitted")
                    and versions.get(BANDIT_KEY) and versions.get(BANDIT_ARM_KEY)):
                yield envelope


def _eligible_ids(games_collection, consented) -> set[str]:
    """지금 판 저장소·동의 목록 기준 대상 판 ID."""
    from ml.bandit import games_from_events
    allow = consented() if callable(consented) else consented
    return {g["game_id"] for g in games_from_events(list(bandit_envelopes(games_collection)), allow)}


def run_update(games_collection, store, consented, *, seed: int = 7):
    """하루 갱신 1회. (게시 여부, 뺀 판 수, 대상 판 수)를 돌려준다.

    `store`는 `ml.mongo_bandit.MongoBanditStore`, `consented`는 동의 가명 집합 또는 그 집합을 돌려주는 함수.
    이미 반영된 판 중 지금 입력에 없는 판(삭제·동의 철회)은 먼저 `forget_games`로 빼야 `propose_update`의
    "과거 판 누락" 거부에 걸리지 않는다. 게시한 뒤에는 판 저장소와 동의 목록을 다시 읽어, 읽은 뒤 게시 전에
    삭제·철회된 판이 다시 들어갔으면 바로 뺀다(개인정보 삭제와 겹친 실행). 이 재확인이 실패해 다시 돌리면
    첫 단계의 "지금 입력에 없는 판 빼기"가 같은 판을 뺀다.
    """
    from ml.bandit import games_from_events
    allow = consented() if callable(consented) else consented
    games = games_from_events(list(bandit_envelopes(games_collection)), allow)
    present = {g["game_id"] for g in games}
    gone = set(store.load()["game_fingerprints"]) - present
    if gone:
        store.forget_games(gone)
    version = store.update(games, seed=seed)
    if version is not None:
        again = _eligible_ids(games_collection, consented)
        # 이번 실행이 읽은 판만 대조한다(동시에 다른 실행이 게시한 새 판은 건드리지 않는다).
        late = (set(store.load()["game_fingerprints"]) & present) - again
        if late:
            store.forget_games(late)
            gone |= late
    return version is not None, len(gone), len(games)

COLLECTION = "possessed_bandit"


def bandit_collection(database, prefix: str = ""):
    """웹과 갱신 배치가 같이 쓰는 밴딧 상태 컬렉션(`<접두어>possessed_bandit`, 문서 `_id: bandit` 하나)."""
    return database[prefix + COLLECTION]


def attach_possessed_bandit(service, database, prefix, arms, enabled):
    """게임 서비스의 판 시작 버전 고정 뒤에 씐 자 전략 팔 고정을 잇는다. `enabled`는 운영 스위치를 읽는 함수."""
    from server.app.model_versions import BanditPinning
    chooser = MongoBanditChooser(bandit_collection(database, prefix), arms)
    service.pin_versions = BanditPinning(service.pin_versions, chooser, enabled,
                                         lambda: bool(getattr(service, "load_test", False)))
    return service


def forget_bandit_games(database, game_ids, prefix: str = "") -> int:
    """개인정보 삭제(`privacy_game_cleanup purge-games`)에서 지운 판을 밴딧 상태에서도 바로 뺀다.

    상태 문서가 없으면(밴딧 미설치) 아무것도 하지 않는다. 반영돼 있던 판 수를 돌려준다. 여러 번 불러도 같다.
    하루 갱신도 입력에서 사라진 판을 빼지만, 삭제 요청은 다음 갱신을 기다리지 않는다.
    """
    from ml.mongo_bandit import MongoBanditStore
    collection = bandit_collection(database, prefix)
    state = collection.find_one({"_id": STATE_ID})
    if state is None:
        return 0
    ids = set(game_ids) & set(state["game_fingerprints"])
    if ids:
        MongoBanditStore(collection).forget_games(ids)
    return len(ids)
