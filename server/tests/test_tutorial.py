"""튜토리얼 판 웹 시험(튜토리얼 설계 10절 2~7).

운영 조립 함수(`engine_parts`·`attach_interactions`·`attach_model_versions`·`scenario_dependencies`)와 메모리 저장소로
`POST /tutorial` → 단계표 명령 → 승리까지 웹 경로로 진행한다.
- 직접 입력창 켬/끔 두 설정에서 끝까지 승리하고, 두 경우 판 결과·공개 기록이 같다.
- 허용 외 명령은 422 `tutorial_step`, 상태 버전·이벤트 불변. 자유 입력 단계의 버튼·카드 수정도 거부.
- NPC 판단 모델: 운영 모델 보관소를 끼워도 튜토리얼 판은 대여 0회, 매 의존성 `suspicion is None`.
- 대사 LLM: 튜토리얼 판은 템플릿 렌더러만 쓰고 일반 렌더러는 부르지 않는다.
- 진척·설문·이벤트 가명 제외, 시작 입력 검증, 같은 시작 UUID 재요청.
"""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlencode
from types import SimpleNamespace

from django.test import TestCase, override_settings

from engine.domain import tutorial
from engine.domain.model import CommandKind, RejectCode
from engine.domain.serde import to_jsonable
from engine.ports import EngineDeps
from engine.testing.fakes import InMemoryGameRepository, InMemoryProgressRepository
from server.app.model_versions import SUSPICION_KEY
from server.app.tutorial_start import TutorialStartPolicy
from server.app.usecases import GameService
from server.infra.game_factory import attach_interactions, attach_model_versions, engine_parts
from server.paldo_web.composition import configure_game_service
from tests.engine.test_tutorial import command_of, wrong_commands
from .interaction_store import MemoryInteractionStore
from .test_game_factory import BUNDLE, baseline_parser, bundle_loader, make_player


class CountingModels:
    """운영 의심 모델 보관소 대역. 불리면 기록하고 규칙 의심도 모델을 빌려 준다."""

    def __init__(self, model):
        self.model, self.calls = model, []

    def for_game(self, game_id, version):
        self.calls.append((game_id, version))
        return self.model


def Registry():
    """운영 버전이 실제 의심 모델인 레지스트리."""
    from server.infra.model_registry import StaticModelRegistry
    return StaticModelRegistry({SUSPICION_KEY: "suspicion-real-v1"})


class CountingRenderer:
    """일반 판 렌더러 대역(실시간 생성 자리). 튜토리얼 판에서 불리면 안 된다."""

    def __init__(self):
        self.calls = []

    def submit(self, req):
        self.calls.append(("submit", req.job_id))

    def poll(self, job_id):
        self.calls.append(("poll", job_id))
        from engine.ports import RenderResult, RenderStatus
        return RenderResult(job_id, RenderStatus.DONE, "생성 문장", latency_ms=1, queue_ms=0, model_version="llm")

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))


class TutorialWeb(TestCase):
    def setUp(self):
        self.user, self.player = make_player("tutorial-player")
        self.client.force_login(self.user)
        engine, fake = engine_parts()
        self.deps_seen = []
        base = lambda meta: EngineDeps(bundle_loader(meta.content_version), fake)   # noqa: E731
        service = GameService(engine, InMemoryGameRepository(), InMemoryProgressRepository(), base)
        self.renderer = CountingRenderer()
        attach_interactions(service, MemoryInteractionStore(), baseline_parser(), self.renderer)
        self.models = CountingModels(fake)
        attach_model_versions(service, Registry(), self.models)
        resolve = service.dependencies

        def watched(meta):
            deps = resolve(meta)
            self.deps_seen.append((meta.scenario_id, deps.suspicion))
            return deps
        service.dependencies = watched
        service.prepare_tutorial = TutorialStartPolicy(lambda: BUNDLE.content_version)
        self.service = service
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)

    # ---- 요청 도우미
    def post(self, path, data, status=None):
        response = self.client.post(path, data=json.dumps(data), content_type="application/json",
                                    HTTP_ACCEPT="application/json")
        if status is not None:
            self.assertEqual(response.status_code, status, response.content)
        return response

    def start(self, token=None):
        response = self.post("/tutorial", {"start_token": token or str(uuid.uuid4()), "mudang_variant": "f"}, 201)
        return response.json()["view"]

    def view(self, gid):
        return self.client.get(f"/games/{gid}", HTTP_ACCEPT="application/json").json()["view"]

    def command(self, gid, version, cmd, status=None):
        return self.post(f"/games/{gid}/commands", {"game_id": gid, "command_id": str(uuid.uuid4()),
                                                    "expected_state_version": version,
                                                    "command": to_jsonable(cmd)}, status)

    def utter(self, gid, version, text=tutorial.UTTERANCE_TEXT):
        return self.post(f"/games/{gid}/utterances", {"game_id": gid, "expected_state_version": version,
                                                      "text": text}, 200).json()

    def confirm(self, gid, version, attempt, status=None, reaction="confirm", edited=None):
        body = {"game_id": gid, "attempt_id": attempt, "reaction": reaction, "command_id": str(uuid.uuid4()),
                "expected_state_version": version}
        if reaction == "confirm":
            body["chosen_index"] = 0
        else:
            body["edited_command"] = to_jsonable(edited)
        return self.post(f"/games/{gid}/utterances/{attempt}", body, status)

    def play(self, free_text=True, check_wrong=False):
        """단계표대로 끝까지 둔다. 마지막 화면 정보."""
        view = self.start()
        gid = view["game_id"]
        for i, step in enumerate(tutorial.STEPS):
            version = view["state_version"]
            self.assertEqual(version, tutorial.START_VERSION + i)
            self.assertEqual((view["day"], view["phase"]), (step.day, step.phase.value), i)
            cmd = command_of(step.expected)
            if check_wrong:
                for wrong in wrong_commands(step.expected):
                    out = self.command(gid, version, wrong, 422).json()
                    self.assertEqual(out["reject"], RejectCode.TUTORIAL_STEP.value, (i, wrong))
                    self.assertEqual(self.view(gid)["state_version"], version)
            if tutorial.utterance_step(step.expected, free_text):
                self.command(gid, version, cmd, 422)          # 켬: 같은 명령도 버튼으로는 받지 않는다
                other = self.utter(gid, version, "대장장이를 의심하오")
                self.assertEqual(other["cards"], [])           # 대본 문장이 아니면 카드 없음
                parsed = self.utter(gid, version)
                self.assertEqual(len(parsed["cards"]), 1, parsed)
                edited = self.confirm(gid, version, parsed["attempt_id"], 422, "edit", cmd).json()
                self.assertEqual(edited["reject"], RejectCode.TUTORIAL_STEP.value)
                view = self.confirm(gid, version, parsed["attempt_id"], 200).json()["view"]
            else:
                view = self.command(gid, version, cmd, 200).json()["view"]
        self.assertIsNotNone(view["result"])
        return view

    # ---- 시험
    def test_full_script_wins_with_free_text_on_and_off(self):
        with override_settings(FREE_TEXT_INPUT=True):
            on = self.play(free_text=True)
        with override_settings(FREE_TEXT_INPUT=False):
            off = self.play(free_text=False)
        for view in (on, off):
            self.assertEqual((view["result"]["winner"], view["result"]["day"]), ("village", 2))
        strip = lambda v: {k: v["public"][k] for k in ("votes", "locks", "confessions", "disappearances")}  # noqa: E731
        self.assertEqual(strip(on), strip(off))   # 엔진 명령이 같아 판 흐름이 같다

    def test_trust_line_follows_trust_cue_and_end_hides_story(self):
        """2일 아침 족제비 신뢰 줄은 신뢰 단서 줄 바로 뒤에 읽히도록 표시되고, 종료 화면은 일반 결말·복기 안내를 뺀다(2회차 시운전)."""
        view = self.start()
        gid = view["game_id"]
        i = next(i for i, s in enumerate(tutorial.STEPS) if s.day == 2)
        for step in tutorial.STEPS[:i]:
            view = self.command(gid, view["state_version"], command_of(step.expected), 200).json()["view"]
        page = self.client.get(f"/games/{gid}", HTTP_ACCEPT="text/html")
        after = page.context["tutorial_after"]
        self.assertEqual(list(after.values()), ["trust-cue-2"])
        html = page.content.decode()
        self.assertIn('data-line-id="trust-cue-2"', html)
        line_id = next(iter(after))
        self.assertRegex(html, rf'data-line-id="{line_id}"[^>]*data-vn-after="trust-cue-2"')
        for step in tutorial.STEPS[i:]:
            with override_settings(FREE_TEXT_INPUT=False):
                view = self.command(gid, view["state_version"], command_of(step.expected), 200).json()["view"]
        end = self.client.get(f"/games/{gid}", HTTP_ACCEPT="text/html").content.decode()
        self.assertIn("data-tutorial-end", end)
        self.assertIn('id="game-result-title"', end)
        self.assertNotIn('id="game-ending-title"', end)
        self.assertNotIn('class="end-hint"', end)

    def test_htmx_card_confirm_redraws_next_tutorial_step(self):
        """자유 입력 카드 확인(htmx) 뒤 다시 그린 회의 화면도 튜토리얼 단계 정보를 싣는다. 수정 거부는 단계 위반 코드."""
        view = self.start()
        gid = view["game_id"]
        i = next(i for i, s in enumerate(tutorial.STEPS) if s.expected.via_utterance)
        for step in tutorial.STEPS[:i]:
            view = self.command(gid, view["state_version"], command_of(step.expected), 200).json()["view"]
        version = view["state_version"]
        parsed = self.utter(gid, version)
        body = {"game_id": gid, "attempt_id": parsed["attempt_id"], "command_id": str(uuid.uuid4()),
                "expected_state_version": version}
        url = f"/games/{gid}/utterances/{parsed['attempt_id']}"
        edit = self.client.post(url, json.dumps({**body, "reaction": "edit",
                                                 "edited_command": to_jsonable(command_of(tutorial.STEPS[i].expected))}),
                                content_type="application/json", HTTP_HX_REQUEST="true")
        self.assertEqual(edit.status_code, 422)
        self.assertEqual(edit.context["reject_code"], RejectCode.TUTORIAL_STEP.value)
        self.assertEqual(edit.context["tutorial"]["step"], i)
        ok = self.client.post(url, json.dumps({**body, "reaction": "confirm", "chosen_index": 0}),
                              content_type="application/json", HTTP_HX_REQUEST="true")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.context["tutorial_mode"])
        self.assertEqual(ok.context["tutorial"]["step"], i + 1)

    def test_wrong_commands_are_rejected_without_consuming(self):
        view = self.play(check_wrong=True)
        self.assertEqual((view["result"]["winner"], view["result"]["day"]), ("village", 2))

    def test_no_model_no_llm_no_progress_and_anonymous_events(self):
        view = self.play()
        gid = view["game_id"]
        self.assertEqual(self.models.calls, [])                     # 의심 모델 보관소 대여 0회
        tutorial_deps = [s for sid, s in self.deps_seen if sid]
        self.assertTrue(tutorial_deps)
        self.assertTrue(all(s is None for s in tutorial_deps))      # 매 의존성 모델 없음
        state, _ = self.service.repository.load(gid)
        self.assertNotIn(SUSPICION_KEY, state.meta.model_versions)
        self.assertEqual((state.meta.source.value, state.meta.scenario_id, state.meta.seed),
                         ("scripted", tutorial.SCENARIO_ID, tutorial.SEED))
        self.assertEqual(state.meta.rule_version, tutorial.rule_version())
        self.assertEqual(self.renderer.calls, [])                   # 일반(LLM 자리) 렌더러를 부르지 않음
        progress = self.service.progress.load(self.player)
        self.assertEqual((progress.game_ids, progress.clues), ([], {}))
        envelopes = self.service.repository.events(gid)
        self.assertTrue(all(e.player_pseudonym is None and e.session_id is None for e in envelopes))
        self.assertTrue(all(e.source.value == "scripted" for e in envelopes))
        survey = self.post(f"/games/{gid}/survey", {"game_id": gid, "fun": 4, "unfair": 2, "npc_plausible": 4})
        self.assertEqual(survey.status_code, 404, survey.content)
        ended = self.client.get(f"/games/{gid}", HTTP_ACCEPT="text/html")
        self.assertEqual(ended.status_code, 200)
        self.assertTrue(ended.context["tutorial"]["ended"])
        self.assertTrue(ended.context["tutorial"]["won"])
        self.assertEqual(ended.context["tutorial"]["lines"][0]["line_id"], "tutorial-end-0")

    def test_general_game_still_uses_model(self):
        """일반 판 회귀: 같은 조립에서 일반 판 시작은 운영 모델 버전을 고정하고 보관소를 부른다(튜토리얼을 마친 계정)."""
        self.service.progress.mark_tutorial_completed(self.player)
        from server.app.open_start import StartPolicy, StartVersions
        self.service.prepare_start = StartPolicy(lambda *a: None, lambda: StartVersions(
            tutorial.rule_version(), "paldo-engine", BUNDLE.content_version), self.service.repository.load)
        response = self.post("/games", {"preset_id": "base6", "session_id": None, "mudang_variant": "f",
                                        "start_token": str(uuid.uuid4())}, 200)
        gid = response.json()["view"]["game_id"]
        state, _ = self.service.repository.load(gid)
        self.assertEqual(state.meta.model_versions.get(SUSPICION_KEY), "suspicion-real-v1")
        self.assertTrue(self.models.calls)
        self.assertEqual(self.service.progress.load(self.player).game_ids, [gid])

    # ---- 튜토리얼 강제(11/11 사용자 결정): 튜토리얼을 시작하지 않은 계정의 일반 판 새로 시작은 튜토리얼 판
    def use_open_policy(self):
        from dataclasses import replace
        from server.app.open_start import StartPolicy, StartVersions

        def session_prepare(data, player):   # 회차 판 대역: 고정 시드, 회차 ID 있음
            meta = self.service.prepare_start.open_meta(replace(data, session_id=None, start_token=str(uuid.uuid4())), player)
            return replace(meta, session_id=data.session_id, seed=7)
        self.service.prepare_start = StartPolicy(session_prepare, lambda: StartVersions(
            tutorial.rule_version(), "paldo-engine", BUNDLE.content_version), self.service.repository.load)

    def open_start(self, token=None, status=200, **extra):
        return self.post("/games", {"preset_id": "base6", "session_id": None, "mudang_variant": "f",
                                    "start_token": token or str(uuid.uuid4()), **extra}, status)

    def test_new_account_open_start_becomes_tutorial_once(self):
        """새 계정: JSON 일반 판 시작이 튜토리얼 판(scenario_id로 알림). 같은 시작 UUID 재요청은 같은 판, 다음 시작은 일반 판."""
        self.use_open_policy()
        token = str(uuid.uuid4())
        first = self.open_start(token).json()
        self.assertEqual(first["scenario_id"], tutorial.SCENARIO_ID)
        progress = self.service.progress.load(self.player)
        self.assertEqual((progress.tutorial_completed, progress.game_ids), (True, []))   # 판 목록에는 넣지 않는다
        self.assertEqual(self.open_start(token).json()["view"]["game_id"], first["view"]["game_id"])
        normal = self.open_start().json()
        self.assertIsNone(normal["scenario_id"])
        self.assertEqual(self.service.progress.load(self.player).game_ids, [normal["view"]["game_id"]])

    def test_html_open_start_redirects_to_tutorial_game(self):
        self.use_open_policy()
        response = self.client.post("/games", urlencode({"preset_id": "base6", "mudang_variant": "f",
                                                         "start_token": str(uuid.uuid4())}),
                                    content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 302, response.content[:300])
        gid = response["Location"].rstrip("/").split("/")[-1]
        state, _ = self.service.repository.load(gid)
        self.assertEqual(state.meta.scenario_id, tutorial.SCENARIO_ID)

    def test_existing_account_with_open_game_keeps_it(self):
        """기존 계정(진행 판 있음, 튜토리얼 전): 이어 하기는 그대로, 새로 시작은 튜토리얼 판이고 진행 판은 중단하지 않는다.
        튜토리얼 뒤 새로 시작은 기존 양자택일(409 → 중단하고 새로 시작)로 돌아간다."""
        self.use_open_policy()
        legacy = self.service.start(self._open_input(), self.player, True).view.game_id \
            if False else self._legacy_open_game()
        self.assertEqual(self.client.get(f"/games/{legacy}", HTTP_ACCEPT="application/json").status_code, 200)
        routed = self.open_start(replace_open=True).json()
        self.assertEqual(routed["scenario_id"], tutorial.SCENARIO_ID)
        self.assertEqual(self.service.unfinished_open_games(self.player), [legacy])
        self.assertEqual(self.open_start(status=409).json()["game_id"], legacy)
        replaced = self.open_start(replace_open=True).json()
        self.assertIsNone(replaced["scenario_id"])
        self.assertEqual(self.service.unfinished_open_games(self.player), [replaced["view"]["game_id"]])

    def _legacy_open_game(self):
        """튜토리얼 칸이 생기기 전 계정의 진행 판: 완료 표시를 잠시 켜고 일반 판을 만든 뒤 칸을 지운다."""
        self.service.progress.mark_tutorial_completed(self.player)
        gid = self.open_start().json()["view"]["game_id"]
        self.service.progress.items[self.player].tutorial_completed = None
        return gid

    def test_tutorial_card_start_then_exit_counts_as_done(self):
        """튜토리얼 카드로 시작만 하고 끝내기(판을 떠남)해도 완료: 다음 새로 시작은 일반 판."""
        self.use_open_policy()
        self.start()
        self.assertTrue(self.service.progress.load(self.player).tutorial_completed)
        self.assertIsNone(self.open_start().json()["scenario_id"])

    def test_session_start_is_not_routed(self):
        """회차 판(고정 시드 테스트)은 가로채지 않고, 튜토리얼 완료 표시도 바꾸지 않는다."""
        self.use_open_policy()
        out = self.open_start(session_id="round-1").json()
        self.assertIsNone(out["scenario_id"])
        state, _ = self.service.repository.load(out["view"]["game_id"])
        self.assertEqual((state.meta.session_id, state.meta.seed), ("round-1", 7))
        self.assertIsNone(self.service.progress.load(self.player).tutorial_completed)

    def test_first_game_guide_off_after_tutorial_but_switch_remains(self):
        """튜토리얼을 마친 계정의 첫 실제 판: 족제비 첫 판 안내 기본 꺼짐, 도움말 '이번 판에서 안내 보기' 스위치는 남음.
        튜토리얼 칸 이전 계정의 첫 판은 그대로 켜짐."""
        self.use_open_policy()
        legacy = self._legacy_open_game()
        first, guide_k, _ = self.service.guide_info(legacy, self.player)
        self.assertEqual((first, guide_k is not None), (True, True))
        self.service.progress.mark_tutorial_completed(self.player)
        first, guide_k, _ = self.service.guide_info(legacy, self.player)
        self.assertEqual((first, guide_k is not None), (False, True))
        response = self.html(legacy)
        self.assertFalse(response.context["first_game"])
        self.assertTrue(response.context["guide_pool"])
        html = response.content.decode()
        self.assertIn("data-guide-toggle", html)
        self.assertNotIn("data-guide-first", html)

    def test_replay_repairs_completion_marker(self):
        """판 저장 뒤 완료 표시가 실패해도 같은 시작 UUID 재요청(POST /games·POST /tutorial)이 표시를 메운다(Codex 지적)."""
        self.use_open_policy()
        progress = self.service.progress
        original = progress.mark_tutorial_completed
        for path in ("/games", "/tutorial"):
            with self.subTest(path=path):
                progress.items.pop(self.player, None)
                token = str(uuid.uuid4())
                def fail(_player):
                    raise RuntimeError("progress down")
                progress.mark_tutorial_completed = fail
                with self.assertRaises(RuntimeError):
                    self.open_start(token) if path == "/games" else self.start(token)
                progress.mark_tutorial_completed = original
                self.assertIsNone(progress.load(self.player).tutorial_completed)
                again = self.open_start(token).json() if path == "/games" else {"scenario_id": tutorial.SCENARIO_ID}
                if path == "/tutorial":
                    self.start(token)
                self.assertEqual(again["scenario_id"], tutorial.SCENARIO_ID)
                self.assertTrue(progress.load(self.player).tutorial_completed)

    def test_home_notice_until_tutorial_started(self):
        """홈 일반 게임 칸: 튜토리얼 전에는 안내 한 줄(진행 판이 있으면 중단하지 않는다는 말까지), 시작 뒤에는 없다."""
        from django.urls import reverse
        self.use_open_policy()
        html = self.client.get(reverse("home")).content.decode()
        self.assertIn("data-tutorial-first", html)
        self.assertNotIn("진행 중인 판은 중단되지 않습니다", html)
        self._legacy_open_game()
        self.assertIn("진행 중인 판은 중단되지 않습니다", self.client.get(reverse("home")).content.decode())
        self.start()
        self.assertNotIn("data-tutorial-first", self.client.get(reverse("home")).content.decode())

    def test_start_validation_and_replay(self):
        token = str(uuid.uuid4())
        first = self.start(token)
        again = self.start(token)
        self.assertEqual(first["game_id"], again["game_id"])
        self.assertEqual(self.service.progress.load(self.player).game_ids, [])
        for extra in ({"seed": 1}, {"preset_id": "base8"}, {"session_id": "s"}):
            self.post("/tutorial", {"start_token": str(uuid.uuid4()), "mudang_variant": "f", **extra}, 400)
        self.post("/tutorial", {"start_token": "not-uuid", "mudang_variant": "f"}, 400)
        def form_post(data):
            return self.client.post("/tutorial", urlencode(data), content_type="application/x-www-form-urlencoded")
        form = form_post({"start_token": str(uuid.uuid4()), "mudang_variant": "m", "seed": "3"})
        self.assertEqual(form.status_code, 400)
        form = form_post({"start_token": str(uuid.uuid4()), "mudang_variant": "m"})
        self.assertEqual(form.status_code, 302)
        self.assertTrue(form["Location"].startswith("/games/"))

    def test_unverified_content_version_blocks_start(self):
        self.service.prepare_tutorial = TutorialStartPolicy(lambda: "content-9.9")
        self.post("/tutorial", {"start_token": str(uuid.uuid4()), "mudang_variant": "f"}, 503)

    def test_replay_survives_active_content_change(self):
        """판을 만든 뒤 운영 활성 버전이 검증 목록 밖으로 바뀌어도 같은 시작 UUID 재요청은 같은 판(503 아님)."""
        token = str(uuid.uuid4())
        first = self.start(token)
        self.service.prepare_tutorial = TutorialStartPolicy(lambda: "content-9.9")
        again = self.post("/tutorial", {"start_token": token, "mudang_variant": "f"}, 201).json()["view"]
        self.assertEqual(again["game_id"], first["game_id"])

    def test_meeting_context_has_tutorial_step(self):
        view = self.start()
        response = self.client.get(f"/games/{view['game_id']}", HTTP_ACCEPT="text/html")
        ctx = response.context
        self.assertTrue(ctx["tutorial_mode"])
        self.assertEqual(ctx["tutorial"]["step"], 0)
        self.assertEqual(ctx["tutorial"]["lines"][0]["line_id"], "tutorial-intro-0")
        self.assertEqual(ctx["tutorial"]["substeps"][-1]["action"], "submit")
        self.assertFalse(ctx["first_game"])
        self.assertEqual(ctx["guide_pool"], [])
        self.assertFalse(ctx["npc_polish"])
        rejected = self.client.post(f"/games/{view['game_id']}/commands", urlencode({
            "game_id": view["game_id"], "command_id": str(uuid.uuid4()), "state_version": "0",
            "kind": "divine", "target": "gwabu"}), content_type="application/x-www-form-urlencoded",
            HTTP_ACCEPT="text/html")
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(rejected.context["reject_code"], "tutorial_step")
        html = rejected.content.decode()
        self.assertIn("반짝이는 곳만 누를 수 있어요", html)
        self.assertNotIn("행동을 적용하지 못했습니다", html)

    # ---- 1회차 시운전 반영 시험(화면 단)
    def html(self, gid, **extra):
        return self.client.get(f"/games/{gid}", HTTP_ACCEPT="text/html", **extra)

    def assert_selector_in_html(self, html, sub, where):
        """하위 단계 선택자의 속성·ID 조각이 서버가 그린 HTML에 실제로 있다(화면 쪽 강조 대상이 존재)."""
        target = sub["target"]
        if target.startswith("dialog["):          # 무대 창은 ui-stage.js가 data-stage-panel 묶음으로 만든다
            self.assertRegex(html, rf'id="{sub["panel"]}"[^>]*data-stage-panel', where)
            return
        for attr, value in re.findall(r'\[([\w-]+)(?:="?([^"\]]*)"?)?\]', target):
            self.assertIn(f'{attr}="{value}"' if value else attr, html, (where, target))
        for ident in re.findall(r'#([\w-]+)', target):
            self.assertIn(f'id="{ident}"', html, (where, target))
        for value in ([sub["value"]] if "value" in sub else []) + list(sub.get("values", [])):
            self.assertIn(f'value="{value}"', html, (where, target))

    def test_screens_follow_script_with_free_text_on_and_off(self):
        """단계마다 그린 화면: 강조 대상이 HTML에 있고, 판 시작 인사는 아침 발표보다 앞, 2일째 족제비 줄은 신뢰 단서 뒤.
        직접 입력창 끔(운영 기본)도 12단계 [의심 제기] 화면 조작까지 같은 방식으로 본다."""
        from server.app.trust_cue import TRUST_CALM
        for free_text in (True, False):
            with self.subTest(free_text=free_text), override_settings(FREE_TEXT_INPUT=free_text):
                view = self.start()
                gid = view["game_id"]
                for i, step in enumerate(tutorial.STEPS):
                    response = self.html(gid)
                    html, info = response.content.decode(), response.context["tutorial"]
                    self.assertEqual((info["step"], info["off_script"]), (i, False))
                    self.assertTrue(info["confirm_say"])
                    says = {sub["say"] for sub in info["substeps"]}
                    self.assertFalse(says & {line["text"] for line in info["lines"]}, i)   # 말풍선 문장은 대사창에 미리 안 나옴
                    for sub in info["substeps"]:
                        if sub["key"] != "confirm_card":
                            self.assert_selector_in_html(html, sub, (free_text, i, sub["key"]))
                    if i == 0:
                        intro = html.index('data-line-id="tutorial-intro-0"')
                        first_other = min(m.start() for m in re.finditer(r'class="vn-source" data-line-id="(?!tutorial-)', html))
                        self.assertLess(intro, first_other)
                    if (step.day, step.phase.value) == (2, "morning"):
                        self.assertLess(html.index(TRUST_CALM), html.index(f'data-line-id="tutorial-{i}-0"'))
                    cmd = command_of(step.expected)
                    if tutorial.utterance_step(step.expected, free_text):
                        self.assertIn(f">{tutorial.UTTERANCE_TEXT}</textarea>", html)
                        parsed = self.utter(gid, view["state_version"])
                        cards = self.client.post(f"/games/{gid}/utterances", urlencode({
                            "game_id": gid, "state_version": view["state_version"], "text": tutorial.UTTERANCE_TEXT}),
                            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true").content.decode()
                        self.assert_selector_in_html(cards, info["substeps"][-1], (free_text, i, "confirm_card"))
                        view = self.confirm(gid, view["state_version"], parsed["attempt_id"], 200).json()["view"]
                    else:
                        if step.expected.via_utterance:
                            self.assertNotIn("data-utterance-form", html)   # 직접 입력창 끔: 입력 칸 없이 버튼으로
                            self.assertEqual([s["key"] for s in info["substeps"]], ["open_sheet", "select_target", "submit"])
                        view = self.command(gid, view["state_version"], cmd, 200).json()["view"]
                self.assertEqual(view["result"]["winner"], "village")

    def test_off_script_stops_guide_and_offers_restart(self):
        """각본 이탈: 튜토리얼 판에 NPC 판단 모델이 끼어 흐름이 어긋나면 안내를 멈추고 다시 하기를 권하며 로그를 남긴다."""
        from engine.testing.fakes import FakeSuspicionModel
        self.service.dependencies = lambda meta: EngineDeps(bundle_loader(meta.content_version), FakeSuspicionModel())
        view = self.start()
        gid = view["game_id"]
        off = None
        for step in tutorial.STEPS:
            if (view["day"], view["phase"]) != (step.day, step.phase.value) or view["result"]:
                off = view
                break
            view = self.command(gid, view["state_version"], command_of(step.expected), 200).json()["view"]
        self.assertIsNotNone(off, "가짜 모델에서도 대본대로 흘러가 이탈 시험이 무의미하다")
        with self.assertLogs("paldo.tutorial", level="WARNING") as logs:
            response = self.html(gid)
        info = response.context["tutorial"]
        self.assertTrue(info["off_script"])
        self.assertIsNone(info["step"])
        self.assertEqual([s["key"] for s in info["substeps"]], ["restart"])
        self.assertIn("어긋났어요", info["lines"][0]["text"])
        self.assertIn("data-tutorial-restart", response.content.decode())
        self.assertIn("tutorial_off_script", logs.output[0])

    def test_tutorial_review_hides_clues_and_ad(self):
        view = self.play()
        panel = self.client.get(f"/games/{view['game_id']}/review", HTTP_ACCEPT="text/html", HTTP_HX_REQUEST="true")
        self.assertEqual(panel.status_code, 200)
        html = panel.content.decode()
        self.assertNotIn("이번 판에서 얻은 원한 단서", html)
        self.assertNotIn("review_side", html)
        ended = self.html(view["game_id"]).content.decode()
        self.assertNotIn("'원한 단서'에서", ended)
        self.assertNotIn("설득하기", ended)


class TutorialScriptShape(TestCase):
    def test_every_say_key_is_a_real_substep(self):
        """대본 말풍선 키가 화면 하위 단계 키와 맞는다(오타면 말풍선이 엉뚱한 기본 문장으로 나온다)."""
        from server.api import tutorial_view
        for free_text in (True, False):
            for i in range(len(tutorial.STEPS)):
                script = tutorial_view.step_script(i, free_text)
                keys = {s["key"] for s in tutorial_view._substeps(i, script, free_text)}
                self.assertLessEqual(set(script["say"]), keys, (free_text, i))
                self.assertTrue(script["lines"], (free_text, i))

class ReplayProgressSkipsTutorial(TestCase):
    def test_query_and_loop_skip_scenario_games(self):
        from server.infra.replay_progress import replay_games
        seen = {}

        class Cursor(list):
            def sort(self, *_):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class Collection:
            def find(self, query, projection):
                seen["query"] = query
                return Cursor([{"_id": "g", "state": {"meta": {"player_pseudonym": "p", "scenario_id": "tutorial-v1",
                                                                "content_version": "x"}}, "outbox": []}])

        counts = replay_games(Collection(), SimpleNamespace(), SimpleNamespace(load=lambda v: None), {})
        self.assertIsNone(seen["query"]["state.meta.scenario_id"])
        self.assertEqual(counts["games"], 0)


class SerdeCompat(TestCase):
    def test_old_meta_without_scenario_id_loads(self):
        from engine.domain.model import GameMeta, PRESETS, DataSource
        from engine.domain.serde import from_jsonable
        meta = to_jsonable(GameMeta("g", PRESETS["base6"], 1, DataSource.HUMAN, "r", "e", "c"))
        self.assertNotIn("scenario_id", meta)               # 일반 판은 이전과 같은 키(저장 바이트·해시 유지)
        self.assertIsNone(from_jsonable(GameMeta, meta).scenario_id)
        tut = to_jsonable(GameMeta("g", PRESETS["base6"], 1, DataSource.SCRIPTED, "r", "e", "c",
                                   scenario_id="tutorial-v1"))
        self.assertEqual(from_jsonable(GameMeta, tut).scenario_id, "tutorial-v1")
