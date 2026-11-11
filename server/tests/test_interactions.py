"""추가된 API 5개의 권한·수명주기·비소비·마스킹·렌더 폴백 회귀."""
import json
import uuid
from dataclasses import replace
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import Client, TestCase
from engine.domain.model import Command, CommandKind, ReactionIntent
from engine.ports import CommandCandidate, ParseResult, RenderRequest, RenderResult, RenderStatus, StoredCommand, UtteranceForm
from server.paldo_web.composition import configure_game_service
from server.app.dto import SubmitCommandInput
from .support import install_test_profiles, make_service


class InteractionsAPI(TestCase):
    """실제 Django 경로와 메모리 포트로 요청부터 아웃박스까지 확인한다."""

    def setUp(self):
        """동의한 계정과 독립된 녹화 판을 만든다."""
        self.user = User.objects.create_user(username="interaction-owner")
        self.register_profile = install_test_profiles(self)
        self.profile = self.register_profile(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.gid = self.send("POST", "/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}).json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    def send(self, method, path, body=None, client=None, **headers):
        """공통 JSON 요청 헬퍼."""
        return (client or self.client).generic(method, path, json.dumps(body or {}), content_type="application/json",
                                               HTTP_ACCEPT="application/json", **headers)

    def parse(self, text="지켜보", version=0):
        """해석 요청을 만들고 응답을 반환한다."""
        return self.send("POST", self.base + "/utterances", {"game_id": self.gid, "expected_state_version": version, "text": text})

    def react(self, attempt, reaction, **extra):
        """시도 ID를 URL과 DTO 양쪽에 넣는다."""
        return self.send("POST", self.base + "/utterances/" + attempt,
                         {"game_id": self.gid, "attempt_id": attempt, "reaction": reaction, **extra})

    def test_auth_csrf_ownership_for_all_new_routes(self):
        """미인증·CSRF·타 계정 검사를 다섯 API 전체에 적용한다."""
        routes = [("POST", "/utterances"), ("POST", "/utterances/foreign"), ("GET", "/renders/foreign"),
                  ("PUT", "/notebook"), ("POST", "/survey")]
        anonymous, csrf = Client(), Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        other = User.objects.create_user(username="interaction-other")
        self.register_profile(other)
        stranger = Client()
        stranger.force_login(other)
        for method, path in routes:
            with self.subTest(path=path):
                url = self.base + path
                self.assertEqual(self.send(method, url, client=anonymous).status_code, 401)
                self.assertEqual(self.send(method, url, client=stranger).status_code, 404)
                if method != "GET":
                    self.assertEqual(self.send(method, url, client=csrf).status_code, 403)
        self.assertEqual(self.client.get(self.base + "/renders/foreign").status_code, 404)

    def test_parse_masked_event_does_not_consume_action(self):
        """원문은 포트 입력에만 있고 저장·아웃박스는 마스킹된다."""
        output = self.parse("지켜보 010-1234-5678 person@example.com 900101-1234567 1234567890123456").json()
        self.assertTrue(output["needs_confirmation"])
        self.assertEqual(output["state_version"], 0)
        state, _ = self.service.repository.load(self.gid)
        self.assertEqual(state.state_version, 0)
        events = self.service.repository.unpublished(self.gid)
        intent = [e for e in events if e.event_type == "intent_attempt"][0]
        self.assertEqual(intent.topic, "paldo.intent")
        serialized = json.dumps(intent.payload)
        for raw in ("010-1234", "person@example", "900101", "1234567890123456"):
            self.assertNotIn(raw, serialized)
        self.assertIn("<MASK>", serialized)
        self.assertEqual(self.parse(version=1).status_code, 409)

    def test_confirm_replay_stale_and_closed_attempt(self):
        """확인된 한 카드만 실행하고 재전송은 버전을 추가로 소비하지 않는다."""
        candidate = CommandCandidate(Command(CommandKind.ADVANCE, "mudang"), 1, UtteranceForm.REQUEST)
        with patch.object(self.service.interactions.parser, "parse", return_value=ParseResult([candidate], None, True, {})):
            first = self.parse().json()["attempt_id"]
            stale = self.parse().json()["attempt_id"]
        body = {"command_id": str(uuid.uuid4()), "expected_state_version": 0, "chosen_index": 0}
        self.assertEqual(self.react(first, "confirm", **body).status_code, 200)
        self.assertTrue(self.react(first, "confirm", **body).json()["replayed"])
        self.assertEqual(self.react(first, "cancel").status_code, 409)
        self.assertEqual(self.react(stale, "confirm", **dict(body, command_id=str(uuid.uuid4()))).status_code, 409)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)

    def test_retry_cancel_and_other_non_consuming_reactions(self):
        """재입력은 새 시도에 연결하고 취소·전환·해당 없음은 소비하지 않는다."""
        first = self.parse().json()["attempt_id"]
        retry = self.react(first, "retry", retry_text="지켜보").json()
        self.assertNotEqual(first, retry["attempt_id"])
        self.assertEqual(self.react(first, "retry", retry_text="지켜보").json(), retry)
        for reaction in ("cancel", "switch_to_buttons", "none_of_these"):
            aid = self.parse().json()["attempt_id"]
            self.assertFalse(self.react(aid, reaction).json()["needs_confirmation"])
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 0)

    def test_unknown_fields_path_mismatch_card_bounds_and_edit(self):
        """잘못된 DTO·시도·카드 번호를 거부하고 수정 명령은 엔진에 전달한다."""
        data = {"game_id": self.gid, "expected_state_version": 0, "text": "지켜보", "unexpected": "x"}
        self.assertEqual(self.send("POST", self.base + "/utterances", data).status_code, 400)
        aid = self.parse().json()["attempt_id"]
        extra = {"command_id": str(uuid.uuid4()), "expected_state_version": 0}
        self.assertEqual(self.react(aid, "confirm", chosen_index=99, **extra).status_code, 400)
        self.assertEqual(self.react("unknown", "cancel").status_code, 404)
        self.assertEqual(self.react(aid, "edit", edited_command={"kind": "advance", "actor": "mudang"}, **extra).status_code, 200)
        event = [e for e in self.service.repository.unpublished(self.gid) if e.event_type == "intent_attempt"][-1]
        self.assertEqual(event.payload["label_source"], "player_edited")

    def test_notebook_saved_in_view_and_player_topic_without_version(self):
        """수첩은 재조회에 반영되고 동일 재전송은 이벤트를 늘리지 않는다."""
        view = self.client.get(self.base, HTTP_ACCEPT="application/json").json()["view"]
        npc = next(n for n in view["names"] if n != "mudang")
        body = {"game_id": self.gid, "entries": {npc: 74}, "vote_confidence": 80}
        for _ in range(2):
            self.assertEqual(self.send("PUT", self.base + "/notebook", body).status_code, 204)
        after = self.client.get(self.base, HTTP_ACCEPT="application/json").json()["view"]
        self.assertEqual(after["player"]["notebook"], {npc: 74})
        self.assertEqual(after["state_version"], 0)
        events = [e for e in self.service.repository.unpublished(self.gid) if e.event_type == "notebook_updated"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].topic, "paldo.player")
        self.assertEqual(self.send("PUT", self.base + "/notebook", dict(body, entries={"bogus": 2})).status_code, 400)

    def test_survey_end_required_duplicate_and_masking(self):
        """진행 중 설문은 거부하고 종료 후 재전송·마스킹을 확인한다."""
        body = {"game_id": self.gid, "fun": 5, "unfair": 1, "npc_plausible": 4, "comment": "abc@example.com"}
        self.assertEqual(self.send("POST", self.base + "/survey", body).status_code, 409)
        for step in self.service.engine.steps[1:]:
            self.assertEqual(self.send("POST", self.base + "/commands", dict(step["request"], game_id=self.gid,
                command_id=str(uuid.uuid4()))).status_code, 200)
        for _ in range(2):
            self.assertEqual(self.send("POST", self.base + "/survey", body).status_code, 204)
        self.assertEqual(self.send("POST", self.base + "/survey", dict(body, fun=3)).status_code, 409)
        events = [e for e in self.service.repository.unpublished(self.gid) if e.event_type == "survey_submitted"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["comment"], "<MASK>")
        self.assertEqual(events[0].topic, "paldo.player")

    def test_render_final_fallback_cancel_and_job_ownership(self):
        """완료·시간 초과·취소 결과는 고정하고 다른 판 작업은 조회하지 않는다."""
        state, _ = self.service.repository.load(self.gid)
        req = RenderRequest("render-1", self.gid, 0, "speech-1", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체 문장")
        self.service.interactions.register_renders(state, [req])
        self.service.interactions.renderer.mode = "timeout"
        url = self.base + "/renders/render-1"
        response = self.client.get(url, HTTP_ACCEPT="application/json").json()
        self.assertTrue(response["final"])
        self.assertEqual(response["text"], "대체 문장")
        self.service.interactions.renderer.mode = "done"
        self.assertEqual(self.client.get(url, HTTP_ACCEPT="application/json").json(), response)
        self.service.interactions.register_renders(state, [replace(req, job_id="render-2")])
        self.service.interactions.cancel_previous(state, replace(state, day=state.day + 1, state_version=1))
        cancelled = self.client.get(self.base + "/renders/render-2", HTTP_ACCEPT="application/json").json()
        self.assertEqual(cancelled["status"], "cancelled")
        events = [e for e in self.service.repository.unpublished(self.gid) if e.event_type == "render_job"]
        self.assertEqual(len(events), 2)
        second = self.send("POST", "/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "m"}).json()["view"]["game_id"]
        self.assertEqual(self.client.get(f"/games/{second}/renders/render-1").status_code, 404)

    def test_unwired_interactions_fail_closed(self):
        """저장 인수 전 운영 설정은 메모리로 대체하지 않고 503을 반환한다."""
        self.service.interactions = None
        self.assertEqual(self.parse().status_code, 503)

    def test_command_saved_before_reaction_failure_is_recovered(self):
        """명령 저장 뒤 반응 이벤트가 실패해도 같은 명령 재시도가 복구된다."""
        aid = self.parse().json()["attempt_id"]
        body = {"command_id": str(uuid.uuid4()), "expected_state_version": 0,
                "edited_command": {"kind": "advance", "actor": "mudang"}}
        with patch.object(self.service.repository, "append_events", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.react(aid, "edit", **body)
        recovered = self.react(aid, "edit", **body)
        self.assertEqual(recovered.status_code, 200)
        self.assertTrue(recovered.json()["replayed"])
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)

    def test_render_deadline_and_submit_failure_finalize_with_event(self):
        """렌더가 계속 실행 중이거나 제출에 실패해도 폴백과 최종 이벤트를 남긴다."""
        state, _ = self.service.repository.load(self.gid)
        req = RenderRequest("deadline", self.gid, 0, "s", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체", deadline_ms=10)
        with patch("server.app.interactions.time.time", return_value=100):
            self.service.interactions.register_renders(state, [req])
        # 마감은 결과의 완료 시각으로 판정한다(E-11-13): 마감이 지나도 아직 처리 중이면 시간 초과로 닫는다.
        running = RenderResult("deadline", RenderStatus.RUNNING, None)
        with patch("server.app.interactions.time.time", return_value=101),                 patch.object(self.service.interactions.renderer, "poll", return_value=running):
            output = self.client.get(self.base + "/renders/deadline", HTTP_ACCEPT="application/json").json()
        self.assertEqual(output["status"], "timeout")
        with patch.object(self.service.interactions.renderer, "submit", side_effect=ConnectionError):
            self.service.interactions.register_renders(state, [replace(req, job_id="submit-failed")])
        output = self.client.get(self.base + "/renders/submit-failed", HTTP_ACCEPT="application/json").json()
        self.assertEqual(output["status"], "failed")
        self.assertEqual(len([e for e in self.service.repository.unpublished(self.gid) if e.event_type == "render_job"]), 2)

    def test_render_done_within_deadline_is_kept_and_event_has_no_text(self):
        """완료 시각으로 마감을 지키는 렌더러면 마감 뒤 폴링이어도 결과를 인정하고, 그 밖의 렌더러는 기존대로 시간 초과다.
        render_job 이벤트에는 생성 본문을 넣지 않는다(E-11-13)."""
        state, _ = self.service.repository.load(self.gid)
        renderer = self.service.interactions.renderer
        done = RenderResult("x", RenderStatus.DONE, "다듬은 사실이오.")
        for job, enforces, expect in (("late-other", False, "timeout"), ("late-kanana", True, "done")):
            req = RenderRequest(job, self.gid, 0, "s", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체", deadline_ms=10)
            with patch("server.app.interactions.time.time", return_value=100):
                self.service.interactions.register_renders(state, [req])
            with patch("server.app.interactions.time.time", return_value=105),                     patch.object(renderer, "poll", return_value=done),                     patch.object(type(renderer), "enforces_deadline", enforces, create=True):
                output = self.client.get(self.base + f"/renders/{job}", HTTP_ACCEPT="application/json").json()
            self.assertEqual(output["status"], expect, job)
            self.assertEqual(output["text"], "다듬은 사실이오." if enforces else "대체")
        events = [e for e in self.service.repository.unpublished(self.gid) if e.event_type == "render_job"]
        self.assertTrue(events)
        self.assertTrue(all("text" not in e.payload for e in events))

    def test_linked_command_uses_attempt_and_load_test_flag_is_server_only(self):
        """commands의 시도 연결을 검사하고 부하 표시는 서버 설정만으로 기록한다."""
        self.service.load_test = True
        aid = self.parse().json()["attempt_id"]
        body = {"game_id": self.gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                "intent_attempt_id": aid, "command": {"kind": "advance", "actor": "mudang"}}
        self.assertEqual(self.send("POST", self.base + "/commands", body).status_code, 200)
        self.assertTrue(self.send("POST", self.base + "/commands", body).json()["replayed"])
        self.assertTrue(self.service.repository.unpublished(self.gid)[-1].load_test)
        self.assertEqual(self.send("POST", self.base + "/commands", dict(body, load_test=True)).status_code, 400)

    def test_command_replay_across_reaction_routes_and_versions(self):
        """같은 명령 ID는 반응 경로·버전이 달라도 저장 응답이며 라벨은 유지한다."""
        candidate = CommandCandidate(Command(CommandKind.ADVANCE, "mudang"), 1, UtteranceForm.REQUEST)
        with patch.object(self.service.interactions.parser, "parse", return_value=ParseResult([candidate], None, True, {})):
            aid = self.parse().json()["attempt_id"]
        command = {"kind": "advance", "actor": "mudang"}
        cid = str(uuid.uuid4())
        first = self.react(aid, "edit", command_id=cid, expected_state_version=0, edited_command=command).json()
        events = len(self.service.repository.unpublished(self.gid))
        with patch.object(self.service.engine, "apply", side_effect=AssertionError("no repeated engine")):
            for version in (0, 1):
                linked = self.send("POST", self.base + "/commands", dict(game_id=self.gid,
                    command_id=cid, expected_state_version=version, command=command, intent_attempt_id=aid))
                self.assertEqual(linked.status_code, 200)
                self.assertEqual(linked.json(), dict(first, replayed=True))
                reaction = self.react(aid, "confirm", command_id=cid, expected_state_version=version, chosen_index=0)
                self.assertEqual(reaction.status_code, 200)
                self.assertEqual(reaction.json(), dict(first, replayed=True))
        self.assertEqual(len(self.service.repository.unpublished(self.gid)), events)
        self.assertEqual(self.service.interactions.store.records[self.gid].attempts[aid]["payload"]["label_source"], "player_edited")
        self.assertEqual(self.react(aid, "confirm", command_id=str(uuid.uuid4()), expected_state_version=0,
                                   chosen_index=0).status_code, 409)

    def test_retry_masks_chat_phone_and_preserves_exact_request_comparison(self):
        """채팅 연락처는 모든 보조 기록·이벤트에서 가리고 재전송 비교는 유지한다."""
        for phone in ("010-1234-5678", "01012345678", "010 1234 5678", "+82 10 1234 5678"):
            with self.subTest(format=phone):
                original = self.parse("지켜보 " + phone).json()["attempt_id"]
                text = "다시 지켜보 " + phone + " someone@example.invalid"
                response = self.react(original, "retry", retry_text=text)
                self.assertEqual(response.status_code, 200)
                snapshot = repr(self.service.interactions.store.records[self.gid])
                outbox = repr(self.service.repository.unpublished(self.gid))
                for raw in (phone, "someone@example.invalid", text):
                    self.assertNotIn(raw, snapshot)
                    self.assertNotIn(raw, outbox)
                self.assertEqual(self.react(original, "retry", retry_text=text).json(), response.json())
                # 가린 결과가 같아도 서로 다른 요청을 같은 재전송으로 취급하지 않는다.
                different = text.replace("someone@", "another@")
                self.assertEqual(self.react(original, "retry", retry_text=different).status_code, 409)

    def test_stale_attempt_allows_retry_and_closing_but_not_execution(self):
        """상태 변경 뒤 재입력·닫기는 허용하고 옛 카드 confirm/edit만 409다."""
        ids = {reaction: self.parse().json()["attempt_id"] for reaction in
               ("retry", "cancel", "switch_to_buttons", "none_of_these", "confirm", "edit")}
        command = {"game_id": self.gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                   "command": {"kind": "advance", "actor": "mudang"}}
        self.assertEqual(self.send("POST", self.base + "/commands", command).status_code, 200)
        retry = self.react(ids["retry"], "retry", retry_text="지켜보")
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["state_version"], 1)
        self.assertNotEqual(retry.json()["attempt_id"], ids["retry"])
        record = self.service.interactions.store.records[self.gid].attempts[ids["retry"]]
        self.assertEqual(record["payload"]["superseded_by"], retry.json()["attempt_id"])
        for reaction in ("cancel", "switch_to_buttons", "none_of_these"):
            response = self.react(ids[reaction], reaction)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["needs_confirmation"])
            self.assertEqual(response.json()["state_version"], 1)
            self.assertTrue(self.service.interactions.store.records[self.gid].attempts[ids[reaction]]["closed"])
        for reaction in ("confirm", "edit"):
            response = self.react(ids[reaction], reaction, command_id=str(uuid.uuid4()), expected_state_version=0,
                                  **({"chosen_index": 0} if reaction == "confirm" else {"edited_command": command["command"]}))
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)

    def test_retry_racing_a_new_command_returns_conflict_without_closing(self):
        """재입력 시작 직후 판이 바뀌어도 500 대신 현재 화면 409를 반환한다."""
        aid = self.parse().json()["attempt_id"]
        parse = self.service.interactions.parse
        def advance_before_parse(data, player):
            self.service.command(SubmitCommandInput(self.gid, str(uuid.uuid4()), 0,
                Command(CommandKind.ADVANCE, "mudang")), player)
            return parse(data, player)
        with patch.object(self.service.interactions, "parse", side_effect=advance_before_parse):
            response = self.react(aid, "retry", retry_text="지켜보")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["current_state_version"], 1)
        self.assertFalse(self.service.interactions.store.records[self.gid].attempts[aid]["closed"])
        self.assertEqual(self.react(aid, "retry", retry_text="지켜보").status_code, 200)

    def test_lost_commit_reply_completes_render_effects_once(self):
        """저장 응답 유실 뒤에도 옛 렌더 취소·새 렌더 조회·재전송이 정상이다."""
        state, _ = self.service.repository.load(self.gid)
        request = RenderRequest("before", self.gid, 0, "s-before", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체")
        self.service.interactions.register_renders(state, [request])
        apply, commit = self.service.engine.apply, self.service.repository.commit
        def with_render(*args):
            result = apply(*args)
            return replace(result, render_requests=[replace(request, job_id="after", speech_id="s-after",
                                                           state_version=result.state.state_version)])
        def lost_reply(*args):
            commit(*args)
            raise TimeoutError
        payload = {"game_id": self.gid, "command_id": str(uuid.uuid4()), "expected_state_version": 0,
                   "command": {"kind": "advance", "actor": "mudang"}}
        renderer = self.service.interactions.renderer
        with patch.object(self.service.engine, "apply", side_effect=with_render) as applied, patch.object(
                self.service.repository, "commit", side_effect=lost_reply), patch.object(
                renderer, "submit", wraps=renderer.submit) as submitted:
            response = self.send("POST", self.base + "/commands", payload)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["replayed"])
            self.assertEqual(applied.call_count, 1)
            self.assertEqual(submitted.call_count, 1)
        old = self.client.get(self.base + "/renders/before", HTTP_ACCEPT="application/json")
        new = self.client.get(self.base + "/renders/after", HTTP_ACCEPT="application/json")
        self.assertEqual(old.json()["status"], "cancelled")
        self.assertEqual(new.status_code, 200)
        self.assertEqual(new.json()["status"], "done")
        with patch.object(renderer, "submit", side_effect=AssertionError("no duplicate render")), patch.object(
                self.service.engine, "apply", side_effect=AssertionError("no repeated command")):
            self.assertTrue(self.send("POST", self.base + "/commands", payload).json()["replayed"])
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)
        cancellations = [e for e in self.service.repository.unpublished(self.gid)
                         if e.event_type == "render_job" and e.payload["status"] == "cancelled"]
        self.assertEqual(len(cancellations), 1)

    def test_two_commit_timeouts_replay_recovers_render_without_engine(self):
        """저장 후 연속 통신 실패도 다음 요청에서 렌더까지 복구한다."""
        for step in self.service.engine.steps[1:3]:
            self.assertEqual(self.send("POST", self.base + "/commands", dict(
                step["request"], game_id=self.gid, command_id=str(uuid.uuid4()))).status_code, 200)
        body = dict(self.service.engine.steps[3]["request"], game_id=self.gid, command_id=str(uuid.uuid4()))
        commit = self.service.repository.commit
        calls = 0
        def unavailable(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                commit(*args)
            raise TimeoutError
        with patch.object(self.service.repository, "commit", side_effect=unavailable):
            with self.assertRaises(TimeoutError):
                self.send("POST", self.base + "/commands", body)
        # 서비스 객체를 다시 만들어도 저장 경계에 남긴 계획으로 복구한다.
        old = self.service
        self.service = make_service(old.repository, old.progress)
        self.service.interactions.store = old.interactions.store
        configure_game_service(self.service)
        with patch.object(self.service.repository, "find_command", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.send("POST", self.base + "/commands", body)
        with patch.object(self.service.engine, "apply", side_effect=AssertionError("no repeated engine")):
            replay = self.send("POST", self.base + "/commands", body)
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(replay.json()["current_state_version"], 3)
            self.assertEqual(self.client.get(self.base + "/renders/job_001", HTTP_ACCEPT="application/json").status_code, 200)
            self.assertTrue(self.send("POST", self.base + "/commands", body).json()["replayed"])
        self.assertEqual(calls, 2)

    def test_uncommitted_plan_never_submits_render(self):
        """저장 실패 전 만든 계획은 성공 확인 전까지 외부 렌더를 실행하지 않는다."""
        for step in self.service.engine.steps[1:3]:
            self.send("POST", self.base + "/commands", dict(step["request"], game_id=self.gid,
                                                           command_id=str(uuid.uuid4())))
        body = dict(self.service.engine.steps[3]["request"], game_id=self.gid, command_id=str(uuid.uuid4()))
        renderer = self.service.interactions.renderer
        with patch.object(self.service.repository, "commit", side_effect=TimeoutError) as commit, patch.object(
                renderer, "submit", side_effect=AssertionError("uncommitted render")):
            with self.assertRaises(TimeoutError):
                self.send("POST", self.base + "/commands", body)
            self.assertEqual(commit.call_count, 2)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 2)
        self.assertIsNone(self.service.repository.find_command(self.gid, body["command_id"]))
        self.assertEqual(self.client.get(self.base + "/renders/job_001", HTTP_ACCEPT="application/json").status_code, 404)
        self.assertEqual(self.send("POST", self.base + "/commands", body).status_code, 200)
        self.assertEqual(self.client.get(self.base + "/renders/job_001", HTTP_ACCEPT="application/json").status_code, 200)

    def test_render_storage_failure_is_recovered_by_command_replay(self):
        """명령 저장 이후 후속 처리 중 실패해도 재전송이 누락 작업을 복구한다."""
        for step in self.service.engine.steps[1:3]:
            self.send("POST", self.base + "/commands", dict(step["request"], game_id=self.gid,
                                                           command_id=str(uuid.uuid4())))
        body = dict(self.service.engine.steps[3]["request"], game_id=self.gid, command_id=str(uuid.uuid4()))
        with patch.object(self.service.interactions, "register_renders", side_effect=ConnectionError):
            with self.assertRaises(ConnectionError):
                self.send("POST", self.base + "/commands", body)
        with patch.object(self.service.engine, "apply", side_effect=AssertionError("no repeated engine")):
            self.assertTrue(self.send("POST", self.base + "/commands", body).json()["replayed"])
        self.assertEqual(self.client.get(self.base + "/renders/job_001", HTTP_ACCEPT="application/json").status_code, 200)

    def test_linked_timeout_keeps_render_recovery_outside_attempt_rollback(self):
        """카드 반응 저장이 롤백돼도 이미 저장된 명령의 렌더 복구 기록은 남는다."""
        aid = self.parse().json()["attempt_id"]
        apply, commit = self.service.engine.apply, self.service.repository.commit
        def rendered(*args):
            result = apply(*args)
            request = RenderRequest("linked-recovery", self.gid, result.state.state_version,
                "s-recovery", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체")
            return replace(result, render_requests=[request])
        calls = 0
        def unavailable(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                commit(*args)
            raise TimeoutError
        body = dict(command_id=str(uuid.uuid4()), expected_state_version=0,
                    edited_command={"kind": "advance", "actor": "mudang"})
        with patch.object(self.service.engine, "apply", side_effect=rendered), patch.object(
                self.service.repository, "commit", side_effect=unavailable):
            with self.assertRaises(TimeoutError):
                self.react(aid, "edit", **body)
        with patch.object(self.service.engine, "apply", side_effect=AssertionError("no repeated engine")):
            self.assertTrue(self.react(aid, "edit", **body).json()["replayed"])
        self.assertEqual(self.client.get(self.base + "/renders/linked-recovery", HTTP_ACCEPT="application/json").status_code, 200)

    def test_repeated_transition_cleanup_preserves_new_phase_render(self):
        """같은 전이의 후속 처리가 중복되어도 새 단계 렌더는 취소하지 않는다."""
        previous, _ = self.service.repository.load(self.gid)
        old = RenderRequest("old-phase", self.gid, 0, "s-old", "npc", "", next(iter(ReactionIntent)), "사실", [], "대체")
        self.service.interactions.register_renders(previous, [old])
        player = str(self.profile.player_pseudonym)
        self.service.command(SubmitCommandInput(self.gid, str(uuid.uuid4()), 0, Command(CommandKind.ADVANCE, "mudang")), player)
        current, _ = self.service.repository.load(self.gid)
        new = replace(old, job_id="new-phase", speech_id="s-new", state_version=current.state_version)
        renderer = self.service.interactions.renderer
        with patch.object(renderer, "submit", wraps=renderer.submit) as submitted:
            self.service.interactions.register_renders(current, [new])
            self.service.interactions.cancel_previous(previous, current)
            self.service.interactions.register_renders(current, [new])
            self.assertEqual(submitted.call_count, 1)
        self.assertNotIn("new-phase", renderer.cancelled)
        self.assertEqual(self.client.get(self.base + "/renders/new-phase", HTTP_ACCEPT="application/json").json()["status"], "done")
        cancellations = [e for e in self.service.repository.unpublished(self.gid)
                         if e.event_type == "render_job" and e.payload["status"] == "cancelled"]
        self.assertEqual(len(cancellations), 1)

    def test_delayed_cleanup_preserves_earlier_job_in_current_phase(self):
        """현재 단계에서 버전만 증가해도 늦은 옛 요청이 현재 렌더를 취소하지 않는다."""
        register, commit = self.service.interactions.register_renders, self.service.repository.commit
        def delayed(state, requests):
            register(state, requests)
            if state.state_version != 1:
                return
            for step in self.service.engine.steps[2:4]:
                self.assertEqual(self.send("POST", self.base + "/commands", dict(
                    step["request"], game_id=self.gid, command_id=str(uuid.uuid4()))).status_code, 200)
            current, truth = self.service.repository.load(self.gid)
            # domain.md: 같은 캐묻기 단계에서 하루 2회 행동 가능.
            later = replace(current, state_version=4)
            commit(self.gid, 3, later, truth, [], StoredCommand("second-interrogation", "{}"))
        with patch.object(self.service.interactions, "register_renders", side_effect=delayed):
            response = self.send("POST", self.base + "/commands", dict(
                self.service.engine.steps[1]["request"], game_id=self.gid, command_id=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("job_001", self.service.interactions.renderer.cancelled)
        self.assertEqual(self.client.get(self.base + "/renders/job_001", HTTP_ACCEPT="application/json").json()["status"], "done")

    def test_render_registered_after_another_phase_change_is_cancelled(self):
        """저장 응답 복구 중 다음 단계까지 진행된 작업은 늦게 등록돼도 취소된다."""
        apply, commit = self.service.engine.apply, self.service.repository.commit
        register = self.service.interactions.register_renders
        def with_render(*args):
            result = apply(*args)
            request = RenderRequest("late", self.gid, result.state.state_version, "s-late", "npc", "",
                                    next(iter(ReactionIntent)), "사실", [], "대체")
            return replace(result, render_requests=[request])
        def lost_reply(*args):
            commit(*args)
            raise TimeoutError
        def register_then_advance(state, requests):
            register(state, requests)
            _, truth = self.service.repository.load(self.gid)
            later = replace(state, state_version=state.state_version + 1, day=state.day + 1)
            commit(self.gid, state.state_version, later, truth, [], StoredCommand("later", "{}"))
        with patch.object(self.service.engine, "apply", side_effect=with_render), patch.object(
                self.service.repository, "commit", side_effect=lost_reply), patch.object(
                self.service.interactions, "register_renders", side_effect=register_then_advance):
            response = self.send("POST", self.base + "/commands", {"game_id": self.gid, "command_id": str(uuid.uuid4()),
                "expected_state_version": 0, "command": {"kind": "advance", "actor": "mudang"}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["replayed"])
        result = self.client.get(self.base + "/renders/late", HTTP_ACCEPT="application/json")
        self.assertEqual(result.json()["status"], "cancelled")
        self.assertEqual(result.json()["text"], "대체")
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 2)
