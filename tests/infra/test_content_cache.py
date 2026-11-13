"""콘텐츠 번들 버전별 프로세스 캐시 시험(E-09-09).

`MongoContentSource.load`가 같은 버전을 처음 한 번만 Mongo에서 읽는지, 없는 버전·조회 실패를 캐시하지
않는지, 여러 스레드가 동시에 읽어도 같은 번들을 받는지, 공유한 번들로 판을 돌려도 번들이 바뀌거나 판끼리
결과가 섞이지 않는지 본다. Mongo는 `find_one`만 흉내 내는 가짜 컬렉션으로 바꾼다.
"""
from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from engine.domain import serde
from engine.domain.engine import PaldoEngine
from engine.domain.npc import RuleV0Policy
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from server.infra.content_store import MongoContentSource
from server.infra.game_factory import ContentPolicyEngine
from tools.run_engine_games import run_game

BUNDLE = FixtureContentSource().load("*")


class FakeCollection:
    """`with_options`·`find_one`만 있는 가짜 Mongo 컬렉션. 조회 횟수를 센다."""

    def __init__(self, documents, fail_times=0, delay=None):
        self.documents = documents
        self.fail_times = fail_times
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def with_options(self, **_):
        return self

    def find_one(self, query):
        with self._lock:
            self.calls += 1
            if self.fail_times:
                self.fail_times -= 1
                raise ConnectionError("mongo_down")
        if self.delay is not None:
            self.delay.wait(0.05)
        return self.documents.get(query["_id"])


def documents(*bundles):
    return {b.content_version: {"_id": b.content_version, "bundle": serde.to_jsonable(b)} for b in bundles}


class ListSink:
    def __init__(self):
        self.events = []

    def append(self, envelopes):
        self.events.extend(envelopes)


class ContentCacheTests(unittest.TestCase):
    def test_same_version_reads_mongo_once(self):
        col = FakeCollection(documents(BUNDLE))
        source = MongoContentSource(col)
        first = source.load(BUNDLE.content_version)
        second = source.load(BUNDLE.content_version)
        self.assertIs(first, second)
        self.assertEqual(col.calls, 1)
        self.assertEqual(serde.to_jsonable(first), serde.to_jsonable(BUNDLE))

    def test_versions_are_cached_separately(self):
        other = replace(BUNDLE, content_version=BUNDLE.content_version + "-next")
        col = FakeCollection(documents(BUNDLE, other))
        source = MongoContentSource(col)
        a, b = source.load(BUNDLE.content_version), source.load(other.content_version)
        self.assertEqual((a.content_version, b.content_version), (BUNDLE.content_version, other.content_version))
        self.assertIs(source.load(other.content_version), b)
        self.assertEqual(col.calls, 2)

    def test_missing_version_is_not_cached(self):
        col = FakeCollection({})
        source = MongoContentSource(col)
        for _ in range(2):
            with self.assertRaises(KeyError):
                source.load("nope")
        self.assertEqual(col.calls, 2)
        col.documents.update(documents(replace(BUNDLE, content_version="nope")))
        self.assertEqual(source.load("nope").content_version, "nope")

    def test_lookup_failure_is_not_cached(self):
        col = FakeCollection(documents(BUNDLE), fail_times=1)
        source = MongoContentSource(col)
        with self.assertRaises(ConnectionError):
            source.load(BUNDLE.content_version)
        self.assertEqual(source.load(BUNDLE.content_version).content_version, BUNDLE.content_version)
        self.assertEqual(col.calls, 2)

    def test_concurrent_loads_share_one_bundle(self):
        gate = threading.Event()
        col = FakeCollection(documents(BUNDLE), delay=gate)
        source = MongoContentSource(col)
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(source.load, BUNDLE.content_version) for _ in range(32)]
            gate.set()
            results = [f.result() for f in futures]
        self.assertEqual(col.calls, 1)
        self.assertTrue(all(r is results[0] for r in results))


class SharedBundleGameTests(unittest.TestCase):
    """캐시가 돌려준 같은 번들 객체로 판을 여러 개 돌려도 번들이 그대로이고 판끼리 섞이지 않는다."""

    SEEDS = range(6)

    def setUp(self):
        self.source = MongoContentSource(FakeCollection(documents(BUNDLE)))
        self.content = self.source.load(BUNDLE.content_version)

    def play(self, engine, i):
        deps = EngineDeps(self.source.load(BUNDLE.content_version), FakeSuspicionModel())
        sink = ListSink()
        run = run_game(engine, deps, "base6", 7, i, "rule-v0", sink)
        return run.winner, run.steps, [serde.to_jsonable(e) for e in sink.events]

    def test_bundle_unchanged_after_games(self):
        before = serde.to_jsonable(self.content)
        engine = PaldoEngine(RuleV0Policy(self.content))
        for i in self.SEEDS:
            self.play(engine, i)
        self.assertEqual(serde.to_jsonable(self.content), before)

    def test_concurrent_games_match_sequential(self):
        alone = [self.play(PaldoEngine(RuleV0Policy(BUNDLE)), i) for i in self.SEEDS]
        shared = ContentPolicyEngine()
        engine = shared._for(self.content)
        with ThreadPoolExecutor(max_workers=len(self.SEEDS)) as pool:
            together = list(pool.map(lambda i: self.play(engine, i), self.SEEDS))
        self.assertEqual(together, alone)
        self.assertEqual(serde.to_jsonable(self.content), serde.to_jsonable(BUNDLE))


class RuleVersionPolicy(unittest.TestCase):
    """rule-0.8(A-10-01 1번): rule-0.7 이하 판은 무당 의심 상한을 decision-1 값으로 고정한 정책 엔진을 따로 쓴다."""

    def test_legacy_rule_gets_fixed_q_max(self):
        from engine.domain import params
        shared = ContentPolicyEngine()
        new = shared._for(BUNDLE, params.RULE_PARAMS_VERSION)
        old = shared._for(BUNDLE, "rule-0.7.decision-1")
        self.assertIsNot(new, old)
        self.assertIsNone(new.policy.villager.q_max)
        self.assertEqual(old.policy.villager.q_max, params.LEGACY_DECISION.q_max)
        self.assertIs(shared._for(BUNDLE, "rule-0.6.decision-1"), old)
        self.assertIs(shared._for(BUNDLE), new)

    def test_policy_factory_without_q_max(self):
        """q_max를 받지 않는 정책 공급자도 이전 판에서 그대로 동작한다."""
        shared = ContentPolicyEngine(make_policy=lambda content: RuleV0Policy(content))
        self.assertIsNone(shared._for(BUNDLE, "rule-0.7.decision-1").policy.villager.q_max)


if __name__ == "__main__":
    unittest.main()
