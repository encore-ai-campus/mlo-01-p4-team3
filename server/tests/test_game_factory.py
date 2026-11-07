"""A-07-03 운영 게임 서비스 조립 시험. 실제 엔진(규칙 v0)으로 6인·창귀 1 판을 웹 버튼 명령만으로 끝까지 진행한다.

- 기본 시험은 Mongo 없이 운영과 같은 엔진·의심 모델(`engine_parts`)과 같은 부착 함수(`attach_interactions`)에
  메모리 저장소를 끼운다. 콘텐츠는 운영 묶음 `content/bundle_v1.json`.
- Mongo 시험은 `PALDO_TEST_MONGO_URI`(replica set)가 있을 때만 돈다. B 실행 자원(`open_mongo_runtime`)에 고유 접두어를
  주고 운영 조립(`build_from_runtime`)을 그대로 쓴다. 이 실행이 만든 컬렉션만 지운다.
"""
import copy
import json
import os
import random
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from engine.domain import params
from engine.domain.model import Phase
from engine.domain.serde import from_jsonable, to_jsonable
from engine.ports import EngineDeps, PlayerView, RenderRequest, RenderStatus
from engine.testing.fakes import InMemoryGameRepository, InMemoryProgressRepository
from server.accounts.models import AccountProfile
from server.app.interactions import InteractionState, InteractionUnavailable
from server.app.test_sessions import TestSessionService
from server.infra import game_factory
from server.app.usecases import GameService
from server.infra.game_factory import (INTERACTIONS, ContentPolicyEngine, TemplateLineRenderer, attach_interactions,
                                       build_from_runtime, build_game_service, engine_parts)
from server.infra.interaction_store import MongoInteractionStore, decode, encode
from server.paldo_web.composition import GameServiceUnavailable, configure_game_service, configure_session_service
from tools.check_content import load_bundle
from tools.perf.engine_bench import bench_player_command
from .interaction_store import MemoryInteractionStore
from .session_store import MemorySessionStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = load_bundle(ROOT / "content/bundle_v1.json")
FORBIDDEN = ("claim_truths", "npc_states", "beliefs", "private", "night_plans")
MAX_COMMANDS = 400
_PARSER = None


def baseline_parser():
    """기준선 해석기는 학습에 몇 초 걸려 시험 프로세스에서 한 번만 만든다(운영도 프로세스당 한 번)."""
    global _PARSER
    if _PARSER is None:
        from intent.parser import BaselineIntentParser
        _PARSER = BaselineIntentParser()
    return _PARSER


def keys_of(value):
    """JSON 값의 모든 키."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys_of(item)


class WebGameDriver:
    """참가자 화면의 버튼과 같은 JSON 요청으로 판을 진행한다."""

    def __init__(self, test, session_id):
        self.test, self.client, self.session_id = test, test.client, session_id

    def post(self, path, data, method="post"):
        """JSON 요청을 보내고 500·503이 아니며 응답에 금지 키가 없는지 확인한다."""
        response = getattr(self.client, method)(path, data=json.dumps(data), content_type="application/json",
                                                HTTP_ACCEPT="application/json")
        self.test.assertNotIn(response.status_code, (500, 503), response.content)
        if response.content:
            self.test.assertFalse(set(keys_of(response.json())) & set(FORBIDDEN))
        return response

    def start(self):
        """고정 회차의 다음 판을 시작하고 첫 화면 정보를 돌려준다."""
        response = self.post("/games", {"preset_id": "base6", "session_id": self.session_id, "mudang_variant": "f"})
        self.test.assertEqual(response.status_code, 200, response.content)
        return response.json()["view"]

    def view(self, game_id):
        """판 화면 정보를 JSON으로 다시 읽고 금지 키가 없는지 확인한다."""
        response = self.client.get(f"/games/{game_id}", HTTP_ACCEPT="application/json")
        self.test.assertEqual(response.status_code, 200)
        self.test.assertFalse(set(keys_of(response.json())) & set(FORBIDDEN))
        return response.json()["view"]

    def meeting_html(self, game_id):
        """회의 화면 HTML. 비공개 키 이름이 없다."""
        response = self.client.get(f"/games/{game_id}", HTTP_ACCEPT="text/html")
        self.test.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        for key in FORBIDDEN:
            self.test.assertNotIn(key, html)
        return html

    def command_body(self, view, rng):
        """화면 정보의 합법 명령 중 하나(`bench_player_command`)를 명령 API 요청 본문으로 만든다."""
        cmd = bench_player_command(from_jsonable(PlayerView, view), rng)
        return {"game_id": view["game_id"], "command_id": str(uuid.uuid4()),
                "expected_state_version": view["state_version"], "command": to_jsonable(cmd)}

    def command(self, view, rng, body=None):
        """명령 API로 명령을 내고 수락 응답을 돌려준다."""
        response = self.post(f"/games/{view['game_id']}/commands", body or self.command_body(view, rng))
        self.test.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.test.assertTrue(body["accepted"], body)
        return body

    def renders(self, body):
        """수락 응답의 추궁 답변 작업은 템플릿 문장으로 바로 끝난다."""
        texts = []
        for job_id in body.get("render_jobs") or body["view"].get("pending_render_jobs", []):
            response = self.client.get(f"/games/{body['view']['game_id']}/renders/{job_id}",
                                       HTTP_ACCEPT="application/json")
            self.test.assertEqual(response.status_code, 200, response.content)
            render = response.json()
            self.test.assertEqual((render["status"], render["final"]), ("failed", True))
            self.test.assertTrue(render["text"])
            texts.append(render["text"])
        return texts

    def play(self, view, rng, limit=MAX_COMMANDS):
        """판이 끝나거나 `limit` 명령을 낼 때까지 진행하고 (화면, 명령 수, 렌더 문장 수)를 돌려준다."""
        commands = rendered = 0
        while view["result"] is None and commands < limit:
            body = self.command(view, rng)
            rendered += len(self.renders(body))
            if body["view"]["day"] != view["day"]:
                self.check_morning(body["view"])
            view = body["view"]
            commands += 1
        return view, commands, rendered

    def check_morning(self, view):
        """아침 화면에 지금까지 공개된 실종 기록(0일째 밤부터)마다 피해 장소 문장이 있다. 공개된 밤 목록을 돌려준다."""
        html = self.meeting_html(view["game_id"])
        nights = [d["night"] for d in view["public"]["disappearances"]]
        self.test.assertEqual(nights, list(range(len(nights))))
        for night in nights:
            self.test.assertIn(f"{night + 1}일째 아침:", html)
        self.test.assertIn("에서 발견됐다", html)
        return nights


def make_player(username):
    """동의한 계정 프로필을 만들고 (사용자, 가명 ID)를 돌려준다."""
    user = User.objects.create_user(username=username)
    profile = AccountProfile.objects.create(user=user, consented_at=timezone.now())
    return user, str(profile.player_pseudonym)


def register_session(sessions, session_id, player, seeds):
    """참가자 한 명의 base6 고정 회차를 운영 버전으로 등록한다."""
    sessions.create(session_id, [{"preset_id": "base6", "seed": seed} for seed in seeds], [player],
                    {"rule_version": params.RULE_PARAMS_VERSION, "engine_version": "paldo-engine",
                     "content_version": BUNDLE.content_version})


def bundle_loader(version):
    """운영 콘텐츠 묶음 하나만 제공하는 콘텐츠 읽기 함수."""
    if version != BUNDLE.content_version:
        raise KeyError(version)
    return BUNDLE


class RealEngineWebGame(TestCase):
    """운영 조립 경로 + 메모리 저장소로 실제 엔진 판을 웹에서 끝까지 진행한다(INT-07 #1·#3·#4·#6 자동 재현)."""

    def setUp(self):
        """시험마다 독립된 계정·회차·조립을 준비한다."""
        self.user, self.player = make_player("int07-player")
        self.client.force_login(self.user)
        self.sessions = TestSessionService(MemorySessionStore())
        register_session(self.sessions, "int07", self.player, (70301, 70302, 70303))
        self.store = MemoryInteractionStore()
        engine, suspicion = engine_parts()
        service = GameService(engine, InMemoryGameRepository(), InMemoryProgressRepository(),
                              lambda meta: EngineDeps(bundle_loader(meta.content_version), suspicion))
        self.service = attach_interactions(service, self.store, baseline_parser())
        configure_game_service(self.service)
        configure_session_service(self.sessions)
        self.addCleanup(configure_game_service, None)
        self.addCleanup(configure_session_service, None)
        self.driver = WebGameDriver(self, "int07")

    def test_three_real_games_finish_with_buttons_only(self):
        """고정 회차 3판을 버튼 명령만으로 완주한다(#1). 아침 피해 장소(#3), 중복·오래된 명령(#4), 금지 키 0(#6),
        자유 입력·수첩·설문·진척이 운영 조립에서 동작한다."""
        finished, second_mornings = [], 0
        for number in (1, 2, 3):
            view = self.driver.start()
            self.assertEqual((len(view["active"]), view["day"], view["phase"]), (6, 1, "morning"))
            self.assertEqual(len(view["missing"]), 1)
            gid = view["game_id"]
            self.assertEqual(self.driver.check_morning(view), [0])
            if number == 1:
                body = self.driver.command_body(view, random.Random(0))
                first = self.driver.command(view, None, body)
                replay = self.driver.command(view, None, body)
                self.assertTrue(replay.get("replayed"), replay)
                self.assertEqual(replay["view"], first["view"])
                stale = self.driver.post(f"/games/{gid}/commands", dict(body, command_id=str(uuid.uuid4())))
                self.assertEqual(stale.status_code, 409, stale.content)
                view = first["view"]
                parsed = self.driver.post(f"/games/{gid}/utterances", {
                    "game_id": gid, "expected_state_version": view["state_version"],
                    "text": "주모는 자정에 어디 있었소?"})
                self.assertEqual(parsed.status_code, 200, parsed.content)
                self.assertIn("attempt_id", parsed.json())
                saved = self.driver.post(f"/games/{gid}/notebook", {
                    "game_id": gid, "entries": {view["active"][1]: 70}}, method="put")
                self.assertEqual(saved.status_code, 204, saved.content)
            view, commands, rendered = self.driver.play(view, random.Random(number))
            second_mornings += max(d["night"] for d in view["public"]["disappearances"]) >= 1
            self.assertIsNotNone(view["result"], f"판 {number}이 {MAX_COMMANDS}명령 안에 끝나지 않음")
            self.assertEqual(self.driver.view(gid)["result"], view["result"])
            survey = self.driver.post(f"/games/{gid}/survey", {"game_id": gid, "fun": 4, "unfair": 2, "npc_plausible": 4})
            self.assertIn(survey.status_code, (200, 204), survey.content)
            finished.append((gid, commands, rendered, view["result"]["winner"]))
        self.assertEqual(len({gid for gid, *_ in finished}), 3)
        self.assertGreater(second_mornings, 0)  # 1일째 밤을 지나 2일째 아침 피해 장소까지 본 판이 있다
        self.assertGreater(sum(rendered for _, _, rendered, _ in finished), 0)
        progress = self.client.get("/players/me/progress", HTTP_ACCEPT="application/json")
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(set(progress.json()["progress"]["game_ids"]), {gid for gid, *_ in finished})
        # 운영 Mongo 저장소가 쓰는 형식 표시 직렬화가 실제 판의 보조 기록을 그대로 되살리는지 확인한다.
        for gid, *_ in finished:
            self.assertEqual(decode(encode(self.store.records[gid].__dict__)), self.store.records[gid].__dict__)
            self.assertEqual(decode(encode(self.store.effects.get(gid, {}))), self.store.effects.get(gid, {}))


class AssemblyUnits(unittest.TestCase):
    """조립 함수·템플릿 렌더러의 단위 시험."""

    def test_template_renderer_closes_with_fallback(self):
        """템플릿 렌더러는 생성 없이 폴백으로 바로 끝난다."""
        renderer = TemplateLineRenderer()
        self.assertIsNone(renderer.submit(MagicMock(spec=RenderRequest)))
        result = renderer.poll("job-1")
        self.assertEqual((result.status, result.text, result.fallback, result.failure_reason),
                         (RenderStatus.FAILED, None, True, "runtime_llm_disabled"))
        renderer.cancel("job-1")

    @override_settings(SESSION_MONGO_URI="")
    def test_build_requires_mongo_uri(self):
        """연결 문자열이 없으면 B 실행 자원이 연결 전에 거부한다."""
        with patch("pymongo.MongoClient") as client, \
                patch("intent.parser.BaselineIntentParser", return_value=MagicMock()):
            with self.assertRaises(GameServiceUnavailable):
                build_game_service()
            client.assert_not_called()

    def test_build_uses_b_runtime_and_attaches_a_parts(self):
        """운영 조립은 B 실행 자원에 실제 엔진·가짜 의심 모델을 넘기고, 그 게임 서비스에 보조 기록·렌더러를 끼운다."""
        from engine.testing.fakes import FakeSuspicionModel
        runtime = MagicMock(collection_names=["games", "progress", "content", "b_test_sessions", "b_ad_identities"])
        service = runtime.factory_service.return_value
        service.interactions = None
        parser = MagicMock()
        with patch("server.paldo_web.mongo.open_mongo_runtime", return_value=runtime) as opened, \
                patch("intent.parser.BaselineIntentParser", return_value=parser):
            built = build_game_service()
        kwargs = opened.call_args.kwargs
        self.assertIsInstance(kwargs["engine"], ContentPolicyEngine)
        self.assertIsInstance(kwargs["suspicion"], FakeSuspicionModel)
        self.assertNotIn("collection_prefix", kwargs)
        self.assertIs(built, service)
        runtime.database.__getitem__.assert_called_once_with(INTERACTIONS)
        self.assertIsInstance(built.interactions.renderer, TemplateLineRenderer)
        self.assertIsInstance(built.interactions.store, MongoInteractionStore)
        self.assertIs(built.interactions.parser, parser)

    def test_interactions_collection_follows_b_prefix(self):
        """시험 접두어를 주면 보조 기록 컬렉션도 같은 접두어를 쓴다."""
        runtime = MagicMock(collection_names=["t_games", "t_progress", "t_content", "t_b_test_sessions", "t_b_ad_identities"])
        runtime.factory_service.return_value.interactions = None
        build_from_runtime(runtime, MagicMock())
        runtime.database.__getitem__.assert_called_once_with("t_" + INTERACTIONS)
        with self.assertRaises(ValueError):
            build_from_runtime(MagicMock(collection_names=["other"]), MagicMock())


class FakeCollection:
    """`find_one`·`update_one`의 필터(같음·`$exists`·`$lt`·`$or`, 점 경로)와 `$set`·`$unset`만 흉내 내는 컬렉션 대역."""

    def __init__(self):
        self.docs, self.writes = {}, 0

    def with_options(self, **_):
        """읽기·쓰기 설정은 무시하고 자기 자신을 돌려준다."""
        return self

    @staticmethod
    def _get(doc, path):
        for part in path.split("."):
            if not isinstance(doc, dict) or part not in doc:
                return False, None
            doc = doc[part]
        return True, doc

    def _match(self, doc, query):
        for key, cond in query.items():
            if key == "$or":
                if not any(self._match(doc, q) for q in cond):
                    return False
                continue
            found, value = self._get(doc, key)
            if isinstance(cond, dict) and "$exists" in cond:
                if found != cond["$exists"]:
                    return False
            elif isinstance(cond, dict) and "$lt" in cond:
                if not found or not value < cond["$lt"]:
                    return False
            elif not found or value != cond:
                return False
        return True

    def find_one(self, query, projection=None):
        """_id로 문서 사본을 돌려준다."""
        doc = self.docs.get(query["_id"])
        return copy.deepcopy(doc) if doc is not None else None

    def update_one(self, query, update, upsert=False):
        """필터가 맞으면 갱신하고, 없는 문서는 upsert로 만들며, 있는데 안 맞으면 중복 키 오류를 낸다."""
        from pymongo.errors import DuplicateKeyError
        doc = self.docs.get(query["_id"])
        result = MagicMock(matched_count=0, upserted_id=None)
        if doc is not None and self._match(doc, query):
            result.matched_count = 1
        elif doc is None and upsert:
            doc = self.docs[query["_id"]] = {"_id": query["_id"]}
            result.upserted_id = query["_id"]
        elif doc is not None and upsert:
            raise DuplicateKeyError("dup")
        else:
            return result
        for key, value in copy.deepcopy(update.get("$set", {})).items():
            target, *rest = key.split(".")
            if rest:
                doc.setdefault(target, {})[rest[0]] = value
            else:
                doc[key] = value
        for key in update.get("$unset", {}):
            doc.pop(key, None)
        if set(update.get("$set", {})) & {"state", "effects"}:
            self.writes += 1
        return result


class MongoInteractionStoreUnits(unittest.TestCase):
    """중첩·롤백·독립 계획·경합 거절의 의미가 시험 대역과 같은지 확인한다."""

    def setUp(self):
        """시험마다 독립된 계정·회차·조립을 준비한다."""
        self.collection = FakeCollection()
        self.store = MongoInteractionStore(self.collection)

    def test_nested_edits_share_object_and_save_once(self):
        """같은 스레드의 중첩 호출은 같은 객체를 쓰고 한 번만 저장한다."""
        with self.store.edit("g1") as outer:
            outer.renders["j1"] = {"phase": Phase.ROUND1, "point": (1, Phase.ROUND1, 3)}
            with self.store.edit("g1") as inner:
                self.assertIs(inner, outer)
                inner.notebook = {"entries": {"jumo": 40}}
        self.assertEqual(self.collection.writes, 1)
        with self.store.edit("g1") as again:
            self.assertEqual(again.renders["j1"], {"phase": Phase.ROUND1, "point": (1, Phase.ROUND1, 3)})
            self.assertEqual(again.notebook, {"entries": {"jumo": 40}})
        self.assertEqual(self.collection.writes, 1)

    def test_rollback_and_independent_effects(self):
        """예외는 보조 기록만 되돌리고, 후속 계획은 독립 보존하며, 중첩 예외는 그 변경만 되돌린다."""
        with self.assertRaises(ValueError):
            with self.store.edit("g1") as records:
                records.notebook = {"entries": {"jumo": 1}}
                with self.store.edit_effects("g1") as plans:
                    plans["c1"] = {"k": {"previous": (1, Phase.MORNING, 0)}}
                raise ValueError
        with self.store.edit("g1") as records:
            self.assertEqual(records, InteractionState())
        with self.store.edit_effects("g1") as plans:
            self.assertEqual(plans, {"c1": {"k": {"previous": (1, Phase.MORNING, 0)}}})
        with self.store.edit("g1") as outer:
            outer.notebook = {"a": 1}
            with self.assertRaises(KeyError):
                with self.store.edit("g1") as inner:
                    inner.notebook = {"b": 2}
                    raise KeyError
            self.assertEqual(outer.notebook, {"a": 1})

    def test_concurrent_writer_conflict_is_refused(self):
        """다른 쓰기가 끼어들어 버전이 바뀌면 덮어쓰지 않고 거절한다."""
        with self.store.edit("g1") as records:
            records.notebook = {"v": 1}
        with self.assertRaises(InteractionUnavailable):
            with self.store.edit("g1") as records:
                self.collection.docs["g1"]["state_v"] = 99
                records.notebook = {"v": 2}

    def test_lease_serializes_processes(self):
        """다른 프로세스(같은 컬렉션의 다른 저장소)는 임대가 풀릴 때까지 변경 본문을 시작하지 못한다."""
        other = MongoInteractionStore(self.collection, wait_s=0.05)
        with self.store.edit("g1") as records:
            records.notebook = {"v": 1}
            with self.assertRaises(InteractionUnavailable):
                with other.edit("g1"):
                    self.fail("임대 중에 다른 프로세스의 본문이 실행됨")
            with self.assertRaises(InteractionUnavailable):
                with other.edit_effects("g1"):
                    self.fail("임대 중에 다른 프로세스의 본문이 실행됨")
        self.assertNotIn("lease", self.collection.docs["g1"])
        with other.edit("g1") as records:
            self.assertEqual(records.notebook, {"v": 1})

    def test_save_after_losing_lease_is_refused(self):
        """임대가 만료돼 다른 프로세스가 잡았으면 원래 프로세스의 늦은 저장은 거절한다."""
        with self.assertRaises(InteractionUnavailable):
            with self.store.edit("g1") as records:
                records.notebook = {"late": 1}
                self.collection.docs["g1"]["lease"]["owner"] = "other-process"
        self.assertNotIn("state", self.collection.docs["g1"])

    def test_lease_is_renewed_while_edit_runs(self):
        """임대 시간보다 오래 걸리는 변경도 도는 동안 임대를 연장해 다른 프로세스가 끼어들지 못하고, 저장도 된다."""
        import time
        store = MongoInteractionStore(self.collection, lease_s=0.3)
        other = MongoInteractionStore(self.collection, wait_s=0.05)
        with store.edit("g1") as records:
            records.notebook = {"slow": 1}
            for _ in range(4):                      # 0.2초씩 네 번 = 임대 시간의 두 배 넘게
                time.sleep(0.2)
                with self.assertRaises(InteractionUnavailable):
                    with other.edit("g1"):
                        self.fail("연장 중인 임대를 다른 프로세스가 잡음")
        self.assertNotIn("lease", self.collection.docs["g1"])
        self.assertEqual(self.collection.docs["g1"]["state_v"], 1)

    def test_renewal_retries_transient_errors(self):
        """연장 중 Mongo 일시 오류는 다시 시도하고, 임대를 잃었을 때만 멈춘다."""
        import threading
        from pymongo.errors import AutoReconnect
        store = MongoInteractionStore(self.collection, lease_s=0.3)
        calls = []
        outcomes = iter([AutoReconnect("blip"), MagicMock(matched_count=1), MagicMock(matched_count=0)])

        def flaky(query, update, upsert=False):
            calls.append(update)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        with patch.object(store.collection, "update_one", side_effect=flaky):
            store._renew_loop("g1", "token", threading.Event())
        self.assertEqual(len(calls), 3)

    def test_expired_lease_is_taken_over(self):
        """풀지 못하고 끝난 임대는 만료 뒤 다른 프로세스가 잡는다."""
        from datetime import datetime, timedelta, timezone
        self.collection.docs["g1"] = {"_id": "g1", "lease": {
            "owner": "dead", "until": datetime.now(timezone.utc) - timedelta(seconds=1)}}
        with self.store.edit("g1") as records:
            records.notebook = {"v": 2}
        self.assertNotIn("lease", self.collection.docs["g1"])

    def test_codec_refuses_unlisted_types(self):
        """허용 모듈 밖 형식 표시와 모르는 값은 거부한다."""
        with self.assertRaises(ValueError):
            decode({"__dc__": "os:PathLike", "v": {}})
        with self.assertRaises(TypeError):
            encode(object())


@unittest.skipUnless(os.environ.get("PALDO_TEST_MONGO_URI"), "actual replica set URI required")
class MongoGameServiceTests(TestCase):
    """실제 Mongo 저장소로 판을 이어 하고, 서비스를 다시 조립(재시작)한 뒤 끝까지 진행한다."""

    @classmethod
    def setUpClass(cls):
        """실행마다 고유 접두어를 정하고 운영 콘텐츠를 B 실행 자원의 콘텐츠 컬렉션에 적재한다."""
        super().setUpClass()
        cls.prefix = "paldo_a0703_" + uuid.uuid4().hex + "_"
        cls.runtimes = []
        runtime = cls.open_runtime()
        runtime.content.insert(BUNDLE)
        cls.collections = runtime.collection_names + [cls.prefix + INTERACTIONS]
        cls.database = runtime.database

    @classmethod
    def open_runtime(cls):
        """새 클라이언트로 B 실행 자원을 연다(프로세스 재시작과 같다)."""
        from server.paldo_web.mongo import open_mongo_runtime
        engine, suspicion = engine_parts()
        runtime = open_mongo_runtime(engine=engine, suspicion=suspicion, uri=os.environ["PALDO_TEST_MONGO_URI"],
                                     collection_prefix=cls.prefix)
        cls.runtimes.append(runtime)
        return runtime

    @classmethod
    def tearDownClass(cls):
        """이 실행이 만든 접두 컬렉션만 지운다."""
        for name in cls.collections:
            if not name.startswith(cls.prefix):
                raise AssertionError("unsafe_cleanup_target")
            cls.database.drop_collection(name)
        for runtime in cls.runtimes:
            runtime.close()
        super().tearDownClass()

    def build(self):
        """새 B 실행 자원 위에 운영 조립을 다시 해 웹에 끼운다."""
        runtime = self.open_runtime()
        service = build_from_runtime(runtime, baseline_parser())
        configure_game_service(service)
        return runtime

    def test_save_resume_after_reassembly(self):
        """판 진행 → 재조립(재시작) → 같은 화면·수첩으로 이어 하기 → 완주."""
        user, player = make_player("int07-mongo")
        self.client.force_login(user)
        self.addCleanup(configure_session_service, None)
        self.addCleanup(configure_game_service, None)
        driver = WebGameDriver(self, "int07m")
        runtime = self.build()
        register_session(runtime.sessions, "int07m", player, (70311,))  # 운영과 같이 Mongo 회차 저장소에 등록
        view = driver.start()
        gid = view["game_id"]
        saved = driver.post(f"/games/{gid}/notebook", {"game_id": gid, "entries": {view["active"][1]: 55}}, method="put")
        self.assertEqual(saved.status_code, 204, saved.content)
        rng = random.Random(3)
        view, first, _ = driver.play(view, rng, limit=8)
        self.build()  # 프로세스 재시작과 같은 새 조립: 메모리 캐시 없이 Mongo에서 이어 읽는다.
        resumed = driver.view(gid)
        self.assertEqual(resumed, dict(view, player=resumed["player"]))
        self.assertEqual(resumed["player"]["notebook"], {view["active"][1]: 55})
        again = driver.start()
        self.assertEqual((again["game_id"], again["state_version"]), (gid, view["state_version"]))
        view, rest, _ = driver.play(resumed, rng)
        self.assertIsNotNone(view["result"])
        self.assertGreater(first + rest, 8)
