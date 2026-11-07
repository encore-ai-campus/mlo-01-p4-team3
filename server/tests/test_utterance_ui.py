"""B-07-01 실제 htmx 경로의 카드·오류·폴링·계측 계약 검증."""
import json
import uuid
import importlib.util
from dataclasses import replace
from unittest.mock import patch
from unittest import skipUnless
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from engine.domain.model import Command, CommandKind, EvidenceRef, EvidenceType, ReactionIntent
from engine.ports import CommandCandidate, ParseFailure, ParseResult, RenderRequest, RenderResult, RenderStatus, UtteranceForm
from server.app.dto import RenderOutput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class UtteranceScreens(TestCase):
    """뷰에 가짜 해석기 결과를 주고 HTTP에서 저장 이벤트까지 대조한다."""

    def setUp(self):
        """각 시험마다 독립 로그인 계정과 녹화 판을 조립한다."""
        user = User.objects.create_user(username="utterance-ui")
        self.profile = install_test_profiles(self)(user)
        self.client.force_login(user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}),
                                    content_type="application/json", HTTP_ACCEPT="application/json")
        self.gid = response.json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    def parse(self, result=None, text="지켜보겠소"):
        """화면과 동일한 폼으로 해석한다."""
        data = {"game_id": self.gid, "state_version": "0", "text": text}
        if result is None:
            return self.client.post(self.base + "/utterances", urlencode(data), content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        with patch.object(self.service.interactions.parser, "parse", return_value=result):
            return self.client.post(self.base + "/utterances", urlencode(data), content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")

    def resolve(self, aid, reaction, **extra):
        """표시된 카드 반응을 폼으로 전송한다."""
        return self.client.post(self.base + "/utterances/" + aid,
            urlencode({"game_id": self.gid, "attempt_id": aid, "reaction": reaction, **extra}, doseq=True),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")

    def test_all_failure_states_and_no_consumption(self):
        """빈 입력은 화면 유지, 나머지 네 실패는 계약대로 안내한다."""
        messages = {ParseFailure.UNSUPPORTED_INTENT: "버튼 입력으로 전환", ParseFailure.MISSING_TARGET: "빠진 대상을",
                    ParseFailure.AMBIGUOUS_EVIDENCE: "근거 후보", ParseFailure.UNPARSEABLE: "다시 입력"}
        for failure in ParseFailure:
            with self.subTest(failure=failure):
                response = self.parse(ParseResult([], failure, True, {}))
                if failure == ParseFailure.EMPTY_INPUT:
                    self.assertEqual(response.status_code, 204)
                else:
                    self.assertContains(response, messages[failure])
                    self.assertTemplateUsed(response, "speech_cards.html")
                    if failure == ParseFailure.UNSUPPORTED_INTENT:
                        self.assertContains(response, "버튼 입력으로 전환", count=1)
                        self.assertContains(response, 'name="reaction" value="switch_to_buttons"')
                        self.assertNotContains(response, 'href="#meeting-actions"')
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 0)

    def test_multiple_cards_confirm_one_and_stale_retargets_meeting(self):
        """두 후보 중 하나만 실행하며 오래된 카드는 현재 회의 HTML 409다."""
        card = CommandCandidate(Command(CommandKind.ADVANCE, "mudang"), 1, UtteranceForm.REQUEST)
        parsed = ParseResult([card, card], None, True, {})
        first = self.parse(parsed)
        self.assertContains(first, 'class="speech-card"', count=2)
        aid = first.context["attempt_id"]
        old = self.parse(parsed).context["attempt_id"]
        response = self.resolve(aid, "confirm", command_id=str(uuid.uuid4()), state_version="0", chosen_index="1")
        self.assertContains(response, 'id="meeting"')
        self.assertEqual(response["HX-Retarget"], "#meeting")
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)
        stale = self.resolve(old, "confirm", command_id=str(uuid.uuid4()), state_version="0", chosen_index="0")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale["HX-Retarget"], "#meeting")
        self.assertContains(stale, 'data-state-version="1"', status_code=409)

    def test_missing_target_disabled_and_edit_has_player_edited_label(self):
        """빠진 슬롯은 확인 불가이고 수정 명령은 player_edited로 기록한다."""
        card = CommandCandidate(Command(CommandKind.SUSPECT, "mudang"), 1, UtteranceForm.ASSERTION, ["target"])
        response = self.parse(ParseResult([card], ParseFailure.MISSING_TARGET, True, {}))
        self.assertContains(response, 'name="target" required')
        self.assertContains(response, 'disabled>이 카드 하나 실행')
        aid = response.context["attempt_id"]
        response = self.resolve(aid, "edit", command_id=str(uuid.uuid4()), state_version="0", kind="advance")
        self.assertEqual(response.status_code, 200)
        attempts = self.service.interactions.store.records[self.gid].attempts
        self.assertEqual(attempts[aid]["payload"]["label_source"], "player_edited")

    def test_retry_and_close_reactions_preserve_state_and_mask(self):
        """재입력 연결과 세 닫기 반응은 소비하지 않고 원문 연락처를 보존하지 않는다."""
        for reaction in ("retry", "cancel", "switch_to_buttons", "none_of_these"):
            first = self.parse()
            aid = first.context["attempt_id"]
            response = self.resolve(aid, reaction, retry_text="지켜보 010-1234-5678")
            self.assertEqual(response.status_code, 200)
            record = self.service.interactions.store.records[self.gid].attempts[aid]
            self.assertTrue(record["closed"])
            if reaction == "retry":
                self.assertNotEqual(response.context["attempt_id"], aid)
                self.assertEqual(record["payload"]["superseded_by"], response.context["attempt_id"])
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 0)
        self.assertNotIn("010-1234-5678", repr(self.service.repository.unpublished(self.gid)))

    def test_fallback_poll_final_and_phase_cleanup(self):
        """폴백→1초 폴링→최종 중지 및 다음 단계 취소 이벤트를 확인한다."""
        for step in self.service.engine.steps[1:4]:
            response = self.client.post(self.base + "/commands", json.dumps(dict(step["request"], game_id=self.gid,
                                       command_id=str(uuid.uuid4()))), content_type="application/json", HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-trigger="every 1s"')
        original_claims = [claim.card.text for claim in response.context["claims"]]
        self.assertTrue(original_claims)
        record = self.service.interactions.store.records[self.gid].renders["job_001"]
        self.assertContains(response, record["request"].fallback_text)
        renderer = self.service.interactions.renderer
        with patch.object(renderer, "poll", return_value=RenderResult("job_001", RenderStatus.RUNNING, None)):
            running = self.client.get(self.base + "/renders/job_001", HTTP_HX_REQUEST="true")
        self.assertContains(running, 'hx-trigger="every 1s"')
        with patch.object(renderer, "poll", return_value=RenderResult("job_001", RenderStatus.DONE, "완성 문장", quantization="test")):
            final = self.client.get(self.base + "/renders/job_001", HTTP_HX_REQUEST="true")
        self.assertContains(final, "완성 문장")
        self.assertNotContains(final, "hx-trigger")
        detail = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertContains(detail, "완성 문장", count=1)
        self.assertNotContains(detail, 'hx-trigger="every 1s"')
        self.assertEqual([claim.card.text for claim in detail.context["claims"]], original_claims)
        state, _ = self.service.repository.load(self.gid)
        request = replace(record["request"], job_id="not-polled", speech_id="not-polled", state_version=state.state_version)
        self.service.interactions.register_renders(state, [request])
        self.service.interactions.cancel_previous(state, replace(state, day=state.day + 1, state_version=state.state_version + 1))
        for _ in range(2):
            cancelled = self.client.get(self.base + "/renders/not-polled", HTTP_HX_REQUEST="true")
            self.assertNotContains(cancelled, "hx-trigger")
        events = [e for e in self.service.repository.unpublished(self.gid)
                  if e.event_type == "render_job" and e.payload["job_id"] == "not-polled"]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].payload["fallback"])
        self.assertIn("quantization", events[0].payload)

    def test_metric_examples_and_unknown_form_field(self):
        """입력 예시·해석 지표와 엄격한 화면 입력을 확인한다."""
        page = self.client.get(self.base, HTTP_ACCEPT="text/html")
        self.assertContains(page, "미지원 예시")
        self.assertContains(page, 'src="/static/js/paldo_ws.js"', count=1)
        self.assertContains(page, 'src="/static/js/vendor/alpine.min.js"', count=1)
        self.parse()
        metrics = self.client.get("/metrics")
        self.assertContains(metrics, "paldo_intent_parse_duration_seconds_bucket")
        self.assertNotContains(metrics, "utterance-ui")
        response = self.client.post(self.base + "/utterances", {"game_id": self.gid, "state_version": 0,
                                    "text": "지켜보", "truth": "forbidden"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 400)

    def test_ambiguous_evidence_selects_two_candidate_claims(self):
        """모호한 근거는 후보 주장만 표시하고 두 개를 골라 수정 실행한다."""
        view = self.service.detail(self.gid, str(self.profile.player_pseudonym)).view
        claims = view.public.claims
        alternatives = [EvidenceRef(EvidenceType.CLAIM, claim.claim_id) for claim in claims[:3]]
        card = CommandCandidate(Command(CommandKind.POINT_CONTRADICTION, "mudang"), 1,
                                UtteranceForm.ASSERTION, ["evidence"], evidence_alternatives=alternatives)
        response = self.parse(ParseResult([card], ParseFailure.AMBIGUOUS_EVIDENCE, True, {}))
        for ref in alternatives:
            self.assertContains(response, f'type="checkbox" name="evidence" value="claim:{ref.record_id}"')
        self.assertNotContains(response, f'value="claim:{claims[3].claim_id}"')
        # 녹화 엔진이 이번 단계에서 모순 지적을 받는 시나리오로 조립한다.
        self.service.engine.steps[1]["request"]["command"]["kind"] = "point_contradiction"
        selected = [f"claim:{ref.record_id}" for ref in alternatives[:2]]
        executed = self.resolve(response.context["attempt_id"], "edit", command_id=str(uuid.uuid4()),
                                state_version="0", kind="point_contradiction", evidence=selected)
        self.assertEqual(executed.status_code, 200)
        attempt = self.service.interactions.store.records[self.gid].attempts[response.context["attempt_id"]]
        self.assertEqual(attempt["payload"]["final_command"]["evidence"],
                         [{"type": "claim", "record_id": ref.record_id} for ref in alternatives[:2]])
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)

    def test_empty_retry_keeps_original_card_open(self):
        """빈 재입력 204 뒤에도 기존 카드에서 정상 문장 재입력이 가능하다."""
        aid = self.parse().context["attempt_id"]
        records = self.service.interactions.store.records[self.gid]
        attempts, events = len(records.attempts), len(self.service.repository.unpublished(self.gid))
        empty = self.resolve(aid, "retry", retry_text="  \t")
        self.assertEqual(empty.status_code, 204)
        self.assertFalse(records.attempts[aid]["closed"])
        self.assertEqual(len(records.attempts), attempts)
        self.assertEqual(len(self.service.repository.unpublished(self.gid)), events)
        retry = self.resolve(aid, "retry", retry_text="주모가 수상하다")
        self.assertEqual(retry.status_code, 200)
        self.assertNotEqual(retry.context["attempt_id"], aid)

    def test_meeting_matches_render_job_and_speech(self):
        """같은 발언 ID를 가진 다른 작업이 현재 발언 결과를 덮어쓰지 않는다."""
        for step in self.service.engine.steps[1:4]:
            self.client.post(self.base + "/commands", json.dumps(dict(step["request"], game_id=self.gid,
                command_id=str(uuid.uuid4()))), content_type="application/json", HTTP_HX_REQUEST="true")
        records = self.service.interactions.store.records[self.gid]
        original = records.renders["job_001"]
        req = original["request"]
        wrong = dict(original, request=replace(req, job_id="other-job"),
                     final=RenderOutput("other-job", req.speech_id, RenderStatus.DONE, "잘못된 작업 문장", True))
        records.renders = {"other-job": wrong, **records.renders}
        response = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertNotContains(response, "잘못된 작업 문장")
        self.assertContains(response, 'data-job-id="job_001"')
        self.assertContains(response, 'hx-sync="#meeting:abort"')


@skipUnless(importlib.util.find_spec("sklearn"), "기준선 해석기의 scikit-learn 설치 환경에서 실행")
@override_settings(INTENT_PARSER_FACTORY="intent.parser.BaselineIntentParser")
class BaselineUtteranceScreens(UtteranceScreens):
    """설정 한 줄만 바꿔 같은 화면·수명주기 계약 시험을 기준선으로 재실행한다."""

    def test_mixed_ambiguity_does_not_disable_complete_card(self):
        """실제 다중 의도에서 근거가 모호한 카드만 확인을 막는다."""
        response = self.parse(text="주모가 수상하다; 0일째 밤의 말이 모순이다")
        self.assertEqual(response.context["failure"], "ambiguous_evidence")
        cards = response.context["cards"]
        self.assertEqual(len(cards), 2)
        self.assertTrue(any(card.kind == "suspect" and not card.missing for card in cards))
        self.assertContains(response, 'disabled>이 카드 하나 실행', count=1)
        # 녹화 판의 다음 요청 종류만 맞춰 정상 카드가 단독 실행되는지 확인한다.
        self.service.engine.steps[1]["request"]["command"]["kind"] = "suspect"
        index = next(card.index for card in cards if card.kind == "suspect")
        executed = self.resolve(response.context["attempt_id"], "confirm", command_id=str(uuid.uuid4()),
                                state_version=0, chosen_index=index)
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 1)

    def test_previous_addressee_is_used_without_overriding_explicit_person(self):
        """직전 캐묻기 상대를 쓰되 명시·선택한 인물을 우선한다."""
        for step in self.service.engine.steps[1:4]:
            response = self.client.post(self.base + "/commands", json.dumps(dict(step["request"],
                game_id=self.gid, command_id=str(uuid.uuid4()))), content_type="application/json")
            self.assertEqual(response.status_code, 200)
        for text, selected, expected in (("수상하다", "", "jumo"), ("수상하다", "hunjang", "hunjang"),
                                         ("주모가 수상하다", "hunjang", "jumo")):
            response = self.client.post(self.base + "/utterances", urlencode({"game_id": self.gid,
                "state_version": 3, "text": text, "selected_npc": selected}),
                content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["failure"], "")
            self.assertEqual(response.context["cards"][0].target, expected)
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 3)
        explicit = self.client.post(self.base + "/utterances", json.dumps({"game_id": self.gid,
            "expected_state_version": 3, "text": "수상하다", "last_addressee": "hunjang"}),
            content_type="application/json", HTTP_HX_REQUEST="true")
        retried = self.resolve(explicit.context["attempt_id"], "retry", retry_text="수상하다")
        self.assertEqual(retried.context["cards"][0].target, "hunjang")

    def test_retry_preserves_selected_person_and_evidence(self):
        """재입력도 선택 인물·근거를 이어받고 새 시도를 연결하며 소비하지 않는다."""
        view = self.service.detail(self.gid, str(self.profile.player_pseudonym)).view
        selected = [f"claim:{claim.claim_id}" for claim in view.public.claims[:2]]
        for text in ("수상하다", "0일째 밤의 말이 모순이다"):
            response = self.client.post(self.base + "/utterances", urlencode({"game_id": self.gid,
                "state_version": 0, "text": text, "selected_npc": "jumo", "selected_cards": selected}, doseq=True),
                content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
            aid = response.context["attempt_id"]
            original = self.service.interactions.store.records[self.gid].attempts[aid]["output"].cards
            retried = self.resolve(aid, "retry", retry_text=text)
            next_id = retried.context["attempt_id"]
            attempts = self.service.interactions.store.records[self.gid].attempts
            self.assertNotEqual(next_id, aid)
            self.assertEqual(attempts[aid]["payload"]["superseded_by"], next_id)
            self.assertEqual(attempts[next_id]["output"].cards, original)
            self.assertEqual(retried.context["failure"], "")
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 0)

    def test_configured_parser_is_baseline(self):
        """실제 기준선이 후보를 제안하고 모델 버전을 남기며 판을 소비하지 않는다."""
        from intent.parser import BaselineIntentParser
        observed = self.service.interactions.parser
        self.assertIsInstance(observed.parser, BaselineIntentParser)
        response = self.parse(text="주모가 수상하다")
        self.assertContains(response, "speech-card")
        attempt = self.service.interactions.store.records[self.gid].attempts[response.context["attempt_id"]]
        self.assertTrue(any(card.command.kind is CommandKind.SUSPECT and card.command.target == "jumo"
                            for card in attempt["output"].cards))
        self.assertEqual(set(attempt["payload"]["versions"]),
                         {"model", "tokenizer", "alias_dict", "matcher", "threshold"})
        self.assertEqual(self.service.repository.load(self.gid)[0].state_version, 0)
        self.assertIs(self.service.interactions.parser, observed)
