"""복기 해설 LLM 요약(E-10-01): 작업 등록·임대·마감·늦은 결과·삭제와 조회 API·폴링 조각·Kanana 표기.

생성기는 고정 응답을 돌려주는 가짜다(검사 결과를 고정해 보기 위해). 실제 생성 서버 실측은
`python server/manage.py review_narration_perf`(docs/evidence/perf/E-10-01/)에서 한다.
"""
import json
from copy import deepcopy
from dataclasses import replace
from unittest import TestCase as UnitTestCase

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from engine.domain.model import EndReason, Faction, GameResult, PublicRecord, Role
from engine.testing.fakes import FixtureContentSource
from server.app.dto import ReviewItem, ReviewOutput, StartGameInput
from server.app.review_narration import (NarrationSettings, NarratorError, NarratorReply, NarratorTimeout,
                                         ReviewNarrationService, narration_input)
from server.infra.review_jobs import MemoryReviewJobStore
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service

CONTENT = FixtureContentSource().load("*")
GOOD = {"jumo": "주모는 지금까지 나온 말과 표를 따져 훈장에게 표를 던졌습니다.",
        "meosum": "머슴은 다른 사람들이 찍을 것 같은 주모에게 따라서 표를 던졌습니다."}


class Clock:
    """시험용 시계. 초를 직접 옮긴다."""
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeNarrator:
    """요청 본문에서 NPC·인용을 읽어 미리 정한 문장을 돌려준다. 호출마다 시계를 `cost`초 진행한다."""

    def __init__(self, clock, texts=None, cost=5.0, errors=()):
        self.clock, self.texts, self.cost, self.errors = clock, dict(texts or GOOD), cost, list(errors)
        self.bodies = []
        self.on_call = lambda: None

    def complete(self, body, timeout):
        """정해 둔 응답이나 예외를 차례로 돌려주고 요청 본문을 기록한다."""
        self.bodies.append(body)
        self.on_call()
        self.clock.now += self.cost
        if self.errors:
            raise self.errors.pop(0)
        npc = body["response_format"]["json_schema"]["schema"]["properties"]["npcs"]["prefixItems"][0]
        npc_id = npc["properties"]["npc"]["const"]
        cites = [s["properties"]["cite"]["const"] for s in npc["properties"]["sentences"]["prefixItems"]]
        text = json.dumps({"npcs": [{"npc": npc_id, "sentences": [{"cite": c, "text": self.texts[npc_id]}
                                                                   for c in cites]}]}, ensure_ascii=False)
        return NarratorReply(text, "stop", 100, 30, "kanana-test")


def review_output(game_id="g1", completed=True):
    """NPC 두 명의 결정 항목을 가진 복기 출력을 만든다."""
    timeline = [ReviewItem("d5", "jumo", 1, "vote", "vote", ["model_estimate"], [], "rv_model_estimate",
                           "주모는 훈장에게 표를 던졌습니다.", target_npc="hunjang"),
                ReviewItem("d6", "meosum", 1, "vote", "vote", ["vote_conform"], [], "rv_vote_conform",
                           "머슴은 주모에게 따라 표를 던졌습니다.", target_npc="jumo")]
    result = GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 2, completed=completed)
    roles = {"jumo": Role.VILLAGER, "meosum": Role.POSSESSED, "hunjang": Role.VILLAGER}
    return ReviewOutput(game_id, result, roles, {}, timeline)


class NarrationService(UnitTestCase):
    """복기 해설 응용 서비스: 등록·처리·대체·임대·마감."""
    def setUp(self):
        """메모리 저장소·가짜 생성기·시계로 서비스를 만든다."""
        self.clock = Clock()
        self.store = MemoryReviewJobStore()
        self.narrator = FakeNarrator(self.clock)
        self.slept = []
        self.service = ReviewNarrationService(self.store, self.narrator, clock=self.clock,
                                              sleep=self.slept.append, settings=NarrationSettings())

    def test_input_keeps_only_allowed_fields(self):
        """생성 요청에는 허용한 항목 칸만 들어가고 가명·세션·명령 ID는 없다."""
        payload = narration_input(review_output(), CONTENT, PublicRecord(), seed=42)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(payload["seed"], 42)
        self.assertIn("창귀에 씐 사람", text)
        for hidden in ("g1", "player", "session", "command_id", "snapshot"):
            self.assertNotIn(hidden, text)
        prompt = json.dumps(self.narrator_bodies_after_run(), ensure_ascii=False)
        self.assertNotIn("주모는 훈장에게 표를 던졌습니다.", prompt)       # 템플릿 해설은 요청에 넣지 않는다

    def narrator_bodies_after_run(self):
        """작업 하나를 처리하고 생성기에 보낸 요청 본문들을 돌려준다."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.service.process_next()
        return self.narrator.bodies

    def test_register_is_idempotent_and_skips_aborted_and_disabled(self):
        """등록은 멱등이고, 중단된 판과 기능 꺼짐이면 등록하지 않는다."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.clock.now += 50
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.assertEqual(self.store.get("g1")["created_at"], 1000.0)
        self.service.ensure(review_output("g2", completed=False), CONTENT, PublicRecord(), 1)
        self.assertIsNone(self.store.get("g2"))
        off = ReviewNarrationService(self.store, None)
        off.ensure(review_output("g3"), CONTENT, PublicRecord(), 1)
        self.assertIsNone(self.store.get("g3"))
        self.assertEqual((off.status("g1").status, off.status("g1").final), ("off", True))

    def test_pending_then_llm_sentences_per_npc(self):
        """처리 전 pending, 처리 뒤 NPC별 LLM 문장."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        pending = self.service.status("g1")
        self.assertEqual((pending.status, pending.final, pending.npcs), ("queued", False, []))
        outcome = self.service.process_next()
        self.assertEqual((outcome.status, outcome.llm_npcs, outcome.template_npcs, outcome.saved), ("done", 2, 0, True))
        self.assertEqual(len(self.narrator.bodies), 2)                     # NPC마다 요청 1회
        done = self.service.status("g1")
        self.assertTrue(done.final)
        self.assertEqual([(n.npc, n.source, n.sentences[0].text) for n in done.npcs],
                         [("jumo", "llm", GOOD["jumo"]), ("meosum", "llm", GOOD["meosum"])])
        self.assertIsNone(self.service.process_next())

    def test_failed_check_falls_back_for_that_npc_only(self):
        """검사에 걸린 NPC만 템플릿으로 바뀐다."""
        self.narrator.texts["meosum"] = "머슴은 주모를 감쌌습니다."
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        outcome = self.service.process_next()
        self.assertEqual(outcome.rejects, {"action_mismatch": 1})
        npcs = {n.npc: n for n in self.service.status("g1").npcs}
        self.assertEqual(npcs["jumo"].source, "llm")
        self.assertEqual((npcs["meosum"].source, npcs["meosum"].sentences[0].text),
                         ("template", "머슴은 주모에게 따라 표를 던졌습니다."))

    def test_overload_closes_new_job_as_timeout(self):
        """대기 상한을 넘으면 새 작업은 과부하 시간 초과로 닫혀 템플릿이다."""
        for n in range(10):
            self.service.ensure(review_output(f"q{n}"), CONTENT, PublicRecord(), 1)
        self.service.ensure(review_output("late"), CONTENT, PublicRecord(), 1)
        status = self.service.status("late")
        self.assertEqual((status.status, status.final), ("timeout", True))
        self.assertEqual({n.source for n in status.npcs}, {"template"})

    def test_deadline_shows_template_and_late_result_is_discarded(self):
        """마감이 지나면 템플릿이 보이고 늦게 온 결과는 저장하지 않는다."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.narrator.cost = 100.0                                          # 두 번째 요청 중 마감(180 s) 경과
        outcome = self.service.process_next()
        self.assertFalse(outcome.saved)
        status = self.service.status("g1")
        self.assertEqual((status.status, status.final), ("timeout", True))
        self.assertEqual({n.source for n in status.npcs}, {"template"})
        self.assertEqual(self.service.status("g1"), status)                 # 다시 봐도 같다(최종 고정)

    def test_late_start_and_attempt_limit(self):
        """마감까지 여유가 모자라거나 시도 횟수를 넘으면 생성하지 않고 닫는다."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.clock.now += 160                                               # 남은 시간 20 s < 30 s
        self.assertEqual(self.service.process_next().status, "timeout")
        self.service.ensure(review_output("g2"), CONTENT, PublicRecord(), 1)
        doc = self.store.docs["g2"]
        doc.update(status="running", attempts=2, lease_until=self.clock.now - 1)   # 작업자가 두 번 죽었다
        self.assertEqual(self.service.process_next().status, "failed")

    def test_lease_takeover_discards_old_worker_result(self):
        """임대가 끝나 다른 작업자가 잡으면 앞 작업자의 결과는 버린다."""
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        first = self.store.claim_next(self.clock.now, self.clock.now + 120, "old")
        self.assertEqual(first["attempts"], 1)
        self.clock.now += 121                                               # 임대 만료 → 다른 작업자가 가져감
        outcome = self.service.process_next()
        self.assertTrue(outcome.saved)
        self.assertFalse(self.store.finish("g1", "old", self.clock.now, {"npcs": []}))

    def test_unreachable_retries_once_then_fails_with_template(self):
        """생성 서버에 닿지 않으면 한 번 더 시도한 뒤 실패로 닫고 템플릿을 보인다."""
        self.narrator.errors = [NarratorError("x"), NarratorError("x")]
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.assertEqual(self.service.process_next().status, "failed")
        self.assertEqual(self.slept, [5.0])
        self.assertEqual({n.source for n in self.service.status("g1").npcs}, {"template"})

    def test_timeout_keeps_finished_npcs_and_templates_the_rest(self):
        """요청 시간 초과면 끝난 NPC는 남기고 나머지는 템플릿이다."""
        original, calls = self.narrator.complete, []

        def complete(body, timeout):                                        # 두 번째 NPC 요청만 응답 지연
            calls.append(1)
            if len(calls) == 2:
                raise NarratorTimeout
            return original(body, timeout)
        self.narrator.complete = complete
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        outcome = self.service.process_next()
        self.assertEqual((outcome.llm_npcs, outcome.template_npcs, outcome.rejects), (1, 1, {"timeout": 1}))

    def test_deleted_game_leaves_no_job(self):
        """판이 지워지면 등록 중이든 처리 전이든 처리 중이든 작업 문서(판에서 나온 입력)가 남지 않는다."""
        alive = {"g1": False}
        self.service.game_exists = lambda gid: alive.get(gid, False)
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)        # 등록과 삭제가 겹침
        self.assertIsNone(self.store.get("g1"))
        alive["g1"] = True
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        alive["g1"] = False                                                     # 처리 전에 삭제
        self.assertEqual(self.service.process_next().status, "failed")
        self.assertIsNone(self.store.get("g1"))
        alive["g1"] = True
        self.service.ensure(review_output(), CONTENT, PublicRecord(), 1)
        self.narrator.on_call = lambda: alive.update(g1=False)                  # 생성 중 삭제
        self.assertFalse(self.service.process_next().saved)
        self.assertIsNone(self.store.get("g1"))


class NarrationAPI(TestCase):
    """종료 전 403, JSON 계약, 폴링 조각, 기능 켜짐일 때만 출처 영역에 Kanana 표기."""

    def setUp(self):
        """로그인한 참가자와 메모리 해설 서비스를 끼운 게임 서비스를 준비한다."""
        self.user = User.objects.create_user(username="narration-owner")
        self.profile = install_test_profiles(self)(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.pid = str(self.profile.player_pseudonym)
        self.gid = self.service.start(StartGameInput(self.pid, "base6", "test-session", "f"), self.pid, True).view.game_id
        self.document = self.service.repository.docs[self.gid]
        self.document["truth"].roles = {"jumo": Role.VILLAGER, "meosum": Role.POSSESSED}
        self.clock = Clock()
        self.narrator = FakeNarrator(self.clock)
        self.service.narration = ReviewNarrationService(MemoryReviewJobStore(), self.narrator, clock=self.clock)
        self.path = f"/games/{self.gid}/review/narration"

    def finish(self):
        """판을 끝낸다."""
        end = deepcopy(self.service.engine.views[-1])
        self.document["state"] = replace(self.document["state"], result=end.result, phase=end.phase,
                                         public=end.public, state_version=end.state_version)
        template = self.document["outbox"][0]
        self.service.repository._append(self.document, [replace(
            template, event_type="decision", state_version=self.document["state"].state_version, day=1,
            phase="vote", payload={"actor": "jumo", "snapshot_id": "s", "kind": "vote", "target": "hunjang",
                                   "reason_codes": ["model_estimate"], "evidence": [], "policy_version": "m"})])

    def test_before_end_is_403_and_after_end_polls_then_shows_sentences(self):
        """종료 전 403, 종료 뒤 2초 폴링 조각에서 생성 중 문구 다음 문장이 보이고 폴링이 멈춘다."""
        self.assertEqual(self.client.get(self.path, HTTP_ACCEPT="application/json").status_code, 403)
        self.finish()
        panel = self.client.get(f"/games/{self.gid}/review", HTTP_HX_REQUEST="true").content.decode()
        self.assertIn("마을 사람들의 속마음", panel)
        self.assertIn("족제비가 해설을 다듬고 있어요…", panel)
        self.assertIn(f'hx-get="{self.path}"', panel)
        self.assertIn('hx-trigger="every 2s"', panel)
        body = self.client.get(self.path, HTTP_ACCEPT="application/json").json()
        self.assertEqual(body, {"status": "queued", "final": False, "npcs": []})
        self.service.narration.process_next()
        body = self.client.get(self.path, HTTP_ACCEPT="application/json").json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["npcs"], [{"npc": "jumo", "sentences": [{"cite": body["npcs"][0]["sentences"][0]["cite"],
                                                                       "text": GOOD["jumo"]}], "source": "llm"}])
        fragment = self.client.get(self.path, HTTP_HX_REQUEST="true").content.decode()
        self.assertIn(GOOD["jumo"], fragment)
        self.assertNotIn("hx-trigger", fragment)                            # 확정되면 폴링을 멈춘다
        self.assertNotIn("Kanana 생성", fragment)                           # 문장별 표시 없음

    def test_all_template_hides_group_and_off_shows_nothing(self):
        """전부 템플릿이면 묶음을 숨기고, 기능 꺼짐이면 아무것도 보이지 않는다."""
        self.narrator.texts["jumo"] = "주모는 훈장을 감쌌습니다."
        self.finish()
        self.client.get(f"/games/{self.gid}/review", HTTP_HX_REQUEST="true")
        self.service.narration.process_next()
        fragment = self.client.get(self.path, HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn("마을 사람들의 속마음", fragment)
        self.service.narration = None
        self.assertEqual(self.client.get(self.path, HTTP_ACCEPT="application/json").json(),
                         {"status": "off", "final": True, "npcs": []})

    def test_store_failure_keeps_template_review(self):
        """작업 저장소가 죽어도 복기 화면은 템플릿 해설로 열리고 해설 조회는 off다."""
        self.finish()
        broken = MemoryReviewJobStore()
        def fail(*_args, **_kwargs):
            raise ConnectionError("store down")
        broken.get = broken.insert_if_absent = fail
        self.service.narration.store = broken
        page = self.client.get(f"/games/{self.gid}/review", HTTP_HX_REQUEST="true")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("마을 사람들의 속마음", page.content.decode())
        self.assertEqual(self.client.get(self.path, HTTP_ACCEPT="application/json").json(),
                         {"status": "off", "final": True, "npcs": []})

    def test_kanana_label_only_when_enabled(self):
        """"Powered by Kanana"는 기능이 켜졌을 때만 출처 영역에 한 번 보인다."""
        from server.api.template_tags import asset_attribution
        from django.template.loader import render_to_string
        with override_settings(REVIEW_LLM_URL=""):
            self.assertNotIn("Powered by Kanana", render_to_string("asset_credits.html", asset_attribution()))
        with override_settings(REVIEW_LLM_URL="http://127.0.0.1:9"):
            html = render_to_string("asset_credits.html", asset_attribution())
            self.assertIn('<p class="kanana-credit">Powered by Kanana</p>', html)
