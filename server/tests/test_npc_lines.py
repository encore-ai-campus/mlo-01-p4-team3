"""NPC 대사 LLM(E-11-13) 응용·인프라·표현 회귀. 가짜 생성기·메모리 작업 저장소·고정 설정으로 돈다."""
import json
import re
import uuid
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import Client, TestCase
from pymongo.errors import PyMongoError

from engine.domain.model import ReactionIntent
from engine.ports import RenderRequest, RenderStatus
from server.app.npc_lines import NpcLineService, NpcLlmConfig, resolve_mode
from server.app.review_narration import NarratorError, NarratorReply
from server.infra.kanana_line_renderer import KananaLineRenderer, job_key
from server.infra.npc_line_jobs import MemoryNpcLineStore
from server.infra.ops_settings import MongoOpsSettings, StaticOpsSettings
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class FakeNarrator:
    """마지막 user 메시지의 원문을 돌려준다(다듬기는 원문 그대로, 실시간은 사실 문장 그대로라 검증을 통과한다)."""

    def __init__(self, transform=lambda s: s, fail=False):
        self.transform, self.fail, self.calls = transform, fail, []

    def complete(self, body, timeout):
        self.calls.append((body, timeout))
        if self.fail:
            raise NarratorError("unreachable")
        last = body["messages"][-1]["content"].split(": ", 1)[1]
        return NarratorReply(self.transform(last), "stop", 10, 5, "kanana-test")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class NpcLinesFlow(TestCase):
    """녹화 판 1일째: 1단계(round1)에 훈장의 의심 발언이 생긴다."""

    def setUp(self):
        self.user = User.objects.create_user(username="npc-lines-owner")
        self.register = install_test_profiles(self)
        self.register(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.store, self.settings, self.clock = MemoryNpcLineStore(), StaticOpsSettings(NpcLlmConfig("polish")), Clock()
        self.narrator = FakeNarrator(lambda s: "허, " + s)
        self.service.npc_lines = NpcLineService(self.service, self.store, self.narrator, self.settings, clock=self.clock)
        self.gid = self.send("POST", "/games", {"preset_id": "base6", "session_id": "test-session",
                                                "mudang_variant": "f"}).json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    def send(self, method, path, body=None, client=None):
        return (client or self.client).generic(method, path, json.dumps(body or {}), content_type="application/json",
                                               HTTP_ACCEPT="application/json")

    def step(self, n=1):
        for _ in range(n):
            version = self.service.repository.load(self.gid)[0].state_version
            body = dict(self.service.engine.steps[version + 1]["request"], game_id=self.gid,
                        command_id=str(uuid.uuid4()), expected_state_version=version)
            self.assertEqual(self.send("POST", self.base + "/commands", body).status_code, 200)

    def lines(self):
        return {l["speech_id"]: l for l in self.send("GET", self.base + "/lines").json()["lines"]}

    def shown(self, speech_id, variant, rev=1, client=None):
        return self.send("POST", f"{self.base}/lines/{speech_id}/shown", {"variant": variant, "job_rev": rev}, client)

    def test_off_screen_is_identical_to_no_feature(self):
        """모드가 off면 회의 화면 HTML이 기능이 없을 때와 같고 선조회는 빈 목록이다."""
        self.step()
        self.settings.config = NpcLlmConfig("off")
        off = self.client.get(self.base, HTTP_HX_REQUEST="true").content
        self.service.npc_lines = None
        none = self.client.get(self.base, HTTP_HX_REQUEST="true").content
        token = re.compile(rb'value="[A-Za-z0-9]{64}"|"X-CSRFToken": "[A-Za-z0-9]{64}"')
        self.assertEqual(token.sub(b"T", off), token.sub(b"T", none))
        self.assertNotIn(b"data-npc-lines", off)
        self.assertNotIn(b"Powered by Kanana", self.client.get(self.base, HTTP_ACCEPT="text/html").content)
        self.service.npc_lines = NpcLineService(self.service, self.store, self.narrator, self.settings)
        self.assertEqual(self.send("GET", self.base + "/lines").json(), {"mode": "off", "lines": []})
        self.assertEqual(self.store.docs, {})

    def test_polish_register_ready_fix_first_wins(self):
        """화면 요청이 등록(복구 포함)하고, 작업 프로세스가 다듬고, 띄운 문장이 한 번만 고정된다."""
        self.step()
        page = self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('data-npc-lines="/games/', page)
        self.assertIn("Powered by Kanana", self.client.get(self.base, HTTP_ACCEPT="text/html").content.decode())
        (sid, line), = self.lines().items()
        self.assertEqual((line["status"], line["final"]), ("pending", None))
        self.assertEqual(self.service.npc_lines.process_next().status, "done")
        self.assertIsNone(self.service.npc_lines.process_next())
        line = self.lines()[sid]
        self.assertEqual((line["status"], line["text"], line["job_rev"]), ("ready", "허, 약초꾼이 수상하오.", 1))
        first = self.shown(sid, "polished").json()
        self.assertEqual((first["variant"], first["text"]), ("polished", "허, 약초꾼이 수상하오."))
        second = self.shown(sid, "template").json()   # 다른 탭이 늦게 템플릿을 띄워도 먼저 고정된 값
        self.assertEqual(second["variant"], "polished")
        self.assertEqual(self.lines()[sid]["final"]["variant"], "polished")
        self.assertIn('data-npc-final="허, 약초꾼이 수상하오."', self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode())

    def test_fixed_template_before_done_discards_late_result(self):
        """템플릿으로 먼저 고정되면 늦게 끝난 결과는 저장되지 않는다."""
        self.step()
        (sid, _), = self.lines().items()
        self.assertEqual(self.shown(sid, "template", 0).json()["variant"], "template")
        self.assertIsNone(self.service.npc_lines.process_next())   # 고정과 함께 닫혀 꺼낼 작업이 없다
        doc = self.store.get(f"{self.gid}:{sid}")
        token = "t"
        doc.update(status="running", claim_token=token)
        self.store.docs[doc["_id"]] = doc
        self.assertFalse(self.store.finish(doc["_id"], token, self.clock(), {"text": "늦은 문장"}))

    def test_unfixed_reconnect_and_mode_off_before_fix(self):
        """고정 요청 전 재접속은 다시 고를 수 있고, 모드가 꺼진 뒤의 다듬기 고정 요청은 템플릿으로 고정된다."""
        self.step()
        (sid, _), = self.lines().items()
        outcome = self.service.npc_lines.process_next()
        self.assertEqual(outcome.status, "done", outcome)
        self.assertEqual(self.lines()[sid]["status"], "ready")    # 재접속(다시 선조회)해도 미고정
        self.settings.config = NpcLlmConfig("off")
        self.assertEqual(self.shown(sid, "polished").json()["variant"], "template")

    def test_missing_registration_is_filled_by_next_screen_request(self):
        """등록이 빠진 줄(작업 문서 없음)은 다음 화면 요청이 다시 등록하고, 이미 있는 줄은 두 번 넣지 않는다."""
        self.step()
        (sid, _), = self.lines().items()
        self.store.docs.pop(f"{self.gid}:{sid}")
        self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertEqual(self.store.get(f"{self.gid}:{sid}")["status"], "queued")
        self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertEqual(len([d for d in self.store.docs.values() if d["game_id"] == self.gid]), 1)

    def test_phase_change_fixes_previous_phase_as_template(self):
        """단계가 넘어가면 이전 단계의 미고정 줄은 템플릿으로 고정한다."""
        self.step()
        (sid, _), = self.lines().items()
        self.step(3)    # round1 → interrogation → round2
        line = self.lines()[sid]
        self.assertEqual(line["final"]["variant"], "template")
        self.assertIsNone(self.service.npc_lines.process_next() and None)

    def test_rejected_output_and_unreachable_stay_template(self):
        """검증에 걸린 출력과 연결 실패는 템플릿으로 남고 사유만 저장한다."""
        self.narrator.transform = lambda s: s + " 실은 반대요."
        self.step()
        (sid, _), = self.lines().items()
        outcome = self.service.npc_lines.process_next()
        self.assertEqual((outcome.status, outcome.reason), ("rejected", "added_sentence"))
        self.assertEqual(self.lines()[sid]["status"], "template")
        self.assertNotIn("실은", json.dumps(self.store.get(f"{self.gid}:{sid}"), ensure_ascii=False))

    def test_worker_rechecks_mode_and_queue_cap(self):
        """꺼내기 직전 모드가 꺼졌으면 닫고, 대기 상한을 넘으면 등록 대신 건너뛴 줄로 남긴다."""
        self.step()
        self.lines()
        self.settings.config = NpcLlmConfig("off")
        self.assertEqual(self.service.npc_lines.process_next().status, "closed")
        self.assertEqual(self.narrator.calls, [])
        self.store.docs.clear()
        self.settings.config = NpcLlmConfig("polish", polish_queue_max=0)
        (sid, line), = self.lines().items()
        self.assertEqual(line["status"], "template")
        self.assertEqual(self.store.get(f"{self.gid}:{sid}")["reason"], "queue_full")

    def test_delete_then_late_finish_does_not_revive(self):
        """판 삭제 뒤 늦은 완료가 문서를 되살리지 않는다."""
        self.step()
        self.lines()
        doc = self.store.claim_next(self.clock(), self.clock() + 30, "tok")
        self.assertEqual(self.service.npc_lines.delete([self.gid]), 1)
        self.assertFalse(self.store.finish(doc["_id"], "tok", self.clock(), {"text": "x"}))
        self.assertEqual(self.store.docs, {})

    def test_routes_check_auth_csrf_and_ownership(self):
        self.step()
        (sid, _), = self.lines().items()
        anonymous, csrf = Client(), Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        other = User.objects.create_user(username="npc-lines-other")
        self.register(other)
        stranger = Client()
        stranger.force_login(other)
        self.assertEqual(self.send("GET", self.base + "/lines", client=anonymous).status_code, 401)
        self.assertEqual(self.send("GET", self.base + "/lines", client=stranger).status_code, 404)
        self.assertEqual(self.shown(sid, "polished", client=stranger).status_code, 404)
        self.assertEqual(self.shown(sid, "polished", client=csrf).status_code, 403)
        self.assertEqual(self.send("POST", f"{self.base}/lines/{sid}/shown", {"variant": "x"}).status_code, 400)


class LiveRendererTest(TestCase):
    """캐묻기 답 실시간 생성: 등록·마감·완료 시각 판정."""

    def setUp(self):
        self.store, self.clock = MemoryNpcLineStore(), Clock()
        self.settings = StaticOpsSettings(NpcLlmConfig("live", live_deadline_ms=2000))
        self.renderer = KananaLineRenderer(self.store, self.settings, clock=self.clock)
        self.service = make_service()
        self.lines = NpcLineService(self.service, self.store, FakeNarrator(), self.settings, clock=self.clock)

    def req(self, job="j1"):
        return RenderRequest(job, "g1", 3, "s1", "jumo", "", ReactionIntent.EXPLAIN, "그날 한밤엔 우물가에 있었소.",
                             ["주모"], "그날 한밤엔 우물가에 있었소.")

    def test_done_within_deadline_even_if_polled_late(self):
        self.assertEqual(self.renderer.deadline_ms(self.req()), 2000)
        self.assertEqual(NpcLlmConfig().live_deadline_ms, 3000)     # 기본 마감(11/11 실측으로 확정)
        self.renderer.submit(self.req())
        self.clock.now += 1.1
        self.assertEqual(self.lines.process_next().status, "done")
        self.clock.now += 5     # 마감 뒤 폴링이어도 마감 안에 끝난 결과는 인정
        result = self.renderer.poll("j1", game_id="g1")
        self.assertEqual((result.status, result.text), (RenderStatus.DONE, "그날 한밤엔 우물가에 있었소."))

    def test_unfinished_after_deadline_is_timeout_and_not_claimed(self):
        self.renderer.submit(self.req())
        self.clock.now += 2.5
        self.assertEqual(self.renderer.poll("j1", game_id="g1").status, RenderStatus.TIMEOUT)
        self.assertIsNone(self.lines.process_next())

    def test_finish_after_deadline_is_discarded(self):
        self.renderer.submit(self.req())
        doc = self.store.claim_next(self.clock(), self.clock() + 30, "tok")
        self.assertFalse(self.store.finish(doc["_id"], "tok", self.clock() + 3, {"text": "x"}))

    def test_not_live_or_busy_falls_back_immediately(self):
        self.settings.config = NpcLlmConfig("polish")
        self.renderer.submit(self.req())
        self.assertEqual(self.renderer.poll("j1", game_id="g1").status, RenderStatus.FAILED)
        self.assertEqual(self.renderer.deadline_ms(self.req()), 15000)
        self.settings.config = NpcLlmConfig("live")
        self.renderer.submit(self.req("a"))
        self.renderer.submit(self.req("b"))     # 대기 1개가 있으면 바로 템플릿
        self.assertIsNone(self.store.get(job_key("g1", "b")))

    def test_expired_queued_job_does_not_block_next_submit(self):
        self.renderer.submit(self.req("a"))
        self.clock.now += 3     # 꺼내지 못한 채 마감이 지난 대기 줄
        self.renderer.submit(self.req("b"))
        self.assertIsNotNone(self.store.get(job_key("g1", "b")))

    def test_stale_worker_cannot_close_reclaimed_job(self):
        self.renderer.submit(self.req())
        doc = self.store.claim_next(self.clock(), self.clock() + 0.5, "old")
        self.clock.now += 1     # 임대가 끝나 다른 작업 프로세스가 다시 잡음
        self.assertEqual(self.store.claim_next(self.clock(), self.clock() + 30, "new")["_id"], doc["_id"])
        self.assertFalse(self.store.close(doc["_id"], "failed", {"reason": "timeout"}, "old"))
        self.assertTrue(self.store.finish(doc["_id"], "new", self.clock(), {"text": "x"}))

    def test_same_job_id_in_two_games_does_not_share_result(self):
        """엔진은 판마다 job_001부터 매긴다. 다른 판의 같은 번호는 다른 작업이다."""
        other = RenderRequest("j1", "g2", 3, "s1", "jumo", "", ReactionIntent.EXPLAIN, "그날 해 질 무렵엔 주막에 있었소.",
                              ["주모"], "그날 해 질 무렵엔 주막에 있었소.")
        self.renderer.submit(self.req())
        self.assertEqual(self.lines.process_next().status, "done")
        self.renderer.submit(other)
        self.assertEqual(self.renderer.poll("j1", game_id="g2").status, RenderStatus.QUEUED)
        self.assertEqual(self.renderer.poll("j1", game_id="g1").status, RenderStatus.DONE)

    def test_live_before_polish_priority(self):
        self.store.insert_if_absent({"_id": "g1:p", "game_id": "g1", "kind": "polish", "priority": 1, "status": "queued",
                                     "created_at": 0.0, "deadline_at": 9e9, "attempts": 0, "final": None})
        self.renderer.submit(self.req())
        self.assertEqual(self.store.claim_next(self.clock(), self.clock() + 30, "t")["kind"], "live")


class OpsSettingsTest(TestCase):
    def test_resolve_mode(self):
        self.assertEqual(resolve_mode("", "live", "polish"), "off")
        self.assertEqual(resolve_mode("http://x", None, "polish"), "polish")
        self.assertEqual(resolve_mode("http://x", "live", "polish"), "live")
        self.assertEqual(resolve_mode("http://x", "weird", "polish"), "off")

    def test_cache_failure_and_stale(self):
        clock = Clock(0.0)
        coll = SimpleNamespace(doc={"_id": "npc_llm", "mode": "polish"}, fail=False)

        def find_one(_q):
            if coll.fail:
                raise PyMongoError("down")
            return coll.doc
        coll.find_one = find_one
        ops = MongoOpsSettings(coll, "http://x", "off", clock=clock)
        self.assertEqual(ops.current().mode, "polish")
        coll.doc = {"_id": "npc_llm", "mode": "live"}
        clock.now = 5
        self.assertEqual(ops.current().mode, "polish")     # 10초 캐시
        self.assertEqual(ops.fresh().mode, "live")
        coll.fail = True
        clock.now = 30
        self.assertEqual(ops.current().mode, "live")       # 실패하면 마지막 값
        clock.now = 100
        self.assertEqual(ops.current().mode, "off")        # 60초 넘게 못 읽으면 off
        clock.now = 105
        self.assertEqual(ops.current().mode, "off")        # 캐시 안에서도 옛 켜짐 값으로 돌아가지 않는다
        coll.fail = False
        clock.now = 120
        self.assertEqual(ops.current().mode, "live")       # 다시 읽히면 복구
        self.assertEqual(MongoOpsSettings(coll, "", "live").current().mode, "off")
