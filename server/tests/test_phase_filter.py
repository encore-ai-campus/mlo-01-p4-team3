"""B-09-07 자유 입력 카드 단계 거르기·심문 양식 기본값 회귀."""
import importlib.util
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from engine.domain.model import Command, CommandKind, EvidenceRef, EvidenceType, InterrogationKind, Phase
from engine.ports import CommandCandidate, ParseFailure, ParseResult, UtteranceForm
from server.api.interaction_view import cards_context
from server.api.meeting_view import meeting_context
from server.app.dto import ParseUtteranceOutput
from server.app.interactions import phase_cards, player_kinds
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service

SENTENCE = "대장장이가 수상하다"


def suspect(target="blacksmith", missing=()):
    """해석기가 낸 의심 후보 한 장."""
    return CommandCandidate(Command(CommandKind.SUSPECT, "mudang", target=target), 0.9, UtteranceForm.ASSERTION,
                            list(missing))


class PhaseCards(SimpleTestCase):
    """응용 계층 거르기 함수만 따로 확인한다."""

    def test_vote_phase_turns_suspect_into_vote_and_drops_defend(self):
        """투표 단계: 의심은 같은 대상 투표로, 변호는 버리고, 같은 대상 투표는 한 장만 남긴다."""
        defend = CommandCandidate(Command(CommandKind.DEFEND, "mudang", target="jumo"), 0.8, UtteranceForm.ASSERTION)
        cards = phase_cards([suspect(), defend, suspect()], Phase.VOTE)
        self.assertEqual([(c.command.kind, c.command.target) for c in cards], [(CommandKind.VOTE, "blacksmith")])
        self.assertEqual(cards[0].evidence_alternatives, [])

    def test_vote_conversion_keeps_missing_target_slot(self):
        """대상이 빠진 의심은 대상 칸이 빠진 투표 카드가 된다."""
        cards = phase_cards([suspect(None, ["target", "evidence"])], Phase.VOTE)
        self.assertEqual(cards[0].command.kind, CommandKind.VOTE)
        self.assertEqual(cards[0].missing_slots, ["target"])

    def test_runoff_keeps_only_runoff_candidates(self):
        """결선은 결선 후보 대상만 투표 카드로 바꾼다."""
        self.assertEqual(len(phase_cards([suspect("jumo")], Phase.RUNOFF, ["blacksmith", "meosum"])), 0)
        self.assertEqual(phase_cards([suspect()], Phase.RUNOFF, ["blacksmith"])[0].command.kind, CommandKind.VOTE)

    def test_runoff_keeps_targetless_suspect_as_vote(self):
        """결선에서 대상이 빠진 의심은 버리지 않고 대상 칸이 빠진 투표 카드로 남긴다(카드 수정에서 결선 후보 중 고름)."""
        cards = phase_cards([suspect(None, ["target"])], Phase.RUNOFF, ["blacksmith"])
        self.assertEqual([(c.command.kind, c.command.target, c.missing_slots) for c in cards],
                         [(CommandKind.VOTE, None, ["target"])])

    def test_other_phases_keep_only_legal_kinds(self):
        """1라운드는 의심 유지, 심문은 캐묻기만, 아침·밤은 의심을 버린다."""
        ask = CommandCandidate(Command(CommandKind.INTERROGATE, "mudang", addressee="jumo",
                                       interrogation_kind=InterrogationKind.WHEREABOUTS), 0.7, UtteranceForm.QUESTION)
        self.assertEqual(phase_cards([suspect()], Phase.ROUND1)[0].command.kind, CommandKind.SUSPECT)
        self.assertEqual([c.command.kind for c in phase_cards([suspect(), ask], Phase.INTERROGATION)],
                         [CommandKind.INTERROGATE])
        for phase in (Phase.MORNING, Phase.NIGHT, Phase.ENDED):
            self.assertEqual(phase_cards([suspect()], phase), [])
        self.assertEqual(player_kinds(Phase.MORNING), [])
        self.assertEqual(player_kinds(Phase.INTERROGATION), ["interrogate"])


class RecordedGame(TestCase):
    """녹화 판 조립과 진행·해석 도우미(시험 없음)."""

    def setUp(self):
        """동의한 계정과 녹화 판을 만든다."""
        user = User.objects.create_user(username="phase-filter")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session",
                                    "mudang_variant": "f"}), content_type="application/json", HTTP_ACCEPT="application/json")
        self.gid = response.json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    def advance_to(self, index):
        """처음부터 녹화 요청을 순서대로 보내 해당 화면까지 진행한다."""
        self.advance_from(1, index)

    def advance_from(self, first, last):
        """녹화 요청 first~last번을 순서대로 보낸다."""
        for step in self.service.engine.steps[first:last + 1]:
            response = self.client.post(self.base + "/commands", json.dumps(dict(step["request"], game_id=self.gid,
                                        command_id=str(uuid.uuid4()))), content_type="application/json")
            self.assertEqual(response.status_code, 200)

    def version(self):
        """저장된 상태 버전."""
        return self.service.repository.load(self.gid)[0].state_version

    def parse(self, candidates):
        """화면 폼으로 같은 문장을 해석한다. 해석기 결과는 고정한다."""
        data = urlencode({"game_id": self.gid, "state_version": str(self.version()), "text": SENTENCE})
        with patch.object(self.service.interactions.parser, "parse",
                          return_value=ParseResult(candidates, None, True, {})):
            return self.client.post(self.base + "/utterances", data,
                                    content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")

    def attempt(self, response):
        """저장된 시도 기록."""
        return self.service.interactions.store.records[self.gid].attempts[response.context["attempt_id"]]

class PhaseFilterScreens(RecordedGame):
    """녹화 판을 단계별로 진행하며 같은 문장의 화면 결과를 대조한다."""

    def test_same_sentence_across_phases(self):
        """아침 입력 비활성, 1라운드 의심, 심문 실패 안내, 투표 카드 실행 422 0건, 원래 후보 보존."""
        page = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertContains(page, "회의가 시작되면 말할 수 있습니다.")
        self.assertContains(page, 'class="utterance-fields" disabled')
        self.advance_to(1)
        page = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertNotContains(page, "회의가 시작되면 말할 수 있습니다.")
        self.assertNotContains(page, 'class="utterance-fields" disabled')
        round1 = self.parse([suspect()])
        self.assertEqual([card.kind for card in round1.context["cards"]], ["suspect"])
        self.advance_from(2, 2)  # 1라운드 → 심문(녹화 2번 요청은 의심)
        interrogation = self.parse([suspect()])
        self.assertEqual(interrogation.context["cards"], [])
        self.assertEqual(interrogation.context["failure"], "unsupported_intent")
        self.assertContains(interrogation, "지금 할 수 있는 행동: 캐묻기")
        self.assertEqual(self.attempt(interrogation)["payload"]["candidates"][0]["command"]["kind"], "suspect")
        self.assertIsNone(self.attempt(interrogation)["payload"]["failure"])
        self.advance_from(3, 5)
        self.assertEqual(self.service.repository.load(self.gid)[0].phase, Phase.VOTE)
        vote = self.parse([suspect("yakcho")])
        self.assertEqual([(card.kind, card.target) for card in vote.context["cards"]], [("vote", "yakcho")])
        self.assertEqual(self.attempt(vote)["payload"]["candidates"][0]["command"]["kind"], "suspect")
        before = self.version()
        executed = self.client.post(self.base + "/utterances/" + vote.context["attempt_id"], urlencode({
            "game_id": self.gid, "attempt_id": vote.context["attempt_id"], "reaction": "confirm",
            "command_id": str(uuid.uuid4()), "state_version": str(before), "chosen_index": "0"}),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        self.assertEqual(executed.status_code, 200)
        self.assertEqual(self.version(), before + 1)
        self.assertEqual(self.attempt(vote)["payload"]["final_command"]["kind"], "vote")

    def test_interrogation_form_keeps_previous_addressee_and_question(self):
        """질문을 보낸 뒤 다시 그린 심문 양식은 직전 대상·질문 종류를 고른다(엔진 거절 0건)."""
        self.advance_to(2)
        response = self.client.post(self.base + "/commands", urlencode({
            "game_id": self.gid, "command_id": str(uuid.uuid4()), "state_version": str(self.version()),
            "kind": "interrogate", "addressee": "meosum", "interrogation_kind": "suspicion_reason",
            "target": "jumo"}), content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "행동을 적용하지 못했습니다")
        html = response.content.decode()
        self.assertRegex(html, r'<option value="meosum" selected>')
        self.assertRegex(html, r'<option value="suspicion_reason" selected>')

    def test_whereabouts_claims_carry_night_and_slot(self):
        """진술 체크박스는 진술의 밤·시간을 data 속성으로 가진다(화면 스크립트가 시간 칸을 맞춘다)."""
        self.advance_to(2)
        page = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertContains(page, 'value="claim:cl02" data-night="0" data-slot="midnight"')
        self.assertContains(page, 'value="claim:cl01" data-night="0" data-slot="evening"')


class CommandBar(RecordedGame):
    """8번: 명령 패널·자유 입력·발언 카드가 화면 아래 고정 막대 안에 있고, 조각 교체·409 뒤에도 유지된다."""

    def bar(self, response):
        """막대 영역 HTML."""
        html = response.content.decode()
        start = html.index('<div class="command-bar"')
        return html[start:html.index('<footer class="asset-credits"', start)]

    def test_bar_wraps_actions_input_and_cards(self):
        """회의 화면(전체·htmx 조각) 모두 막대 안에 행동 선택·말로 행동하기·발언 카드 자리가 있다."""
        for headers in ({"HTTP_ACCEPT": "text/html"}, {"HTTP_HX_REQUEST": "true"}):
            bar = self.bar(self.client.get(self.base, **headers))
            self.assertIn('id="meeting-actions"', bar)
            self.assertIn('id="utterance-input"', bar)
            self.assertIn('id="speech-cards"', bar)

    def test_bar_survives_stale_409_swap(self):
        """오래된 버전 명령의 409 회의 화면 교체에도 막대가 그대로 있다."""
        self.advance_to(1)
        stale = self.client.post(self.base + "/commands", urlencode({
            "game_id": self.gid, "command_id": str(uuid.uuid4()), "state_version": "0", "kind": "advance"}),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        self.assertEqual(stale.status_code, 409)
        self.assertIn('id="meeting-actions"', self.bar(stale))

    @override_settings(GAME_UI="vn")
    def test_controls_have_stage_destination_with_noscript_forms(self):
        """11/9 B-10-07 기준: 명령·자유 입력은 무대 창으로 옮기며 JS 없이도 원본 폼을 보존한다."""
        html = self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('data-stage-open="stage-actions"', html)
        self.assertIn('id="stage-actions" data-stage-panel="행동 선택"', html)
        self.assertIn('id="utterance-input"', html)
        self.assertIn('method="post"', html)


class PreviousSelectionTemplate(TestCase):
    """감금·실종된 직전 대상은 기본 선택하지 않는다(첫 항목)."""

    def test_missing_previous_addressee_falls_back_to_first(self):
        """활동 대상에 없는 직전 대상은 selected를 만들지 않는다."""
        service = make_service()
        view = service.engine.views[2]
        context = {**meeting_context(view), "previous_interrogation": {"addressee": "gwabu",
                                                                     "interrogation_kind": "suspicion_reason"}}
        html = render_to_string("meeting_actions.html", context)
        addressee = html[html.index('name="addressee"'):html.index("</select>", html.index('name="addressee"'))]
        self.assertNotIn("selected", addressee)
        self.assertIn('value="suspicion_reason" selected', html)


class FunctionlessInputs(RecordedGame):
    """10번: 기능 없는 체크박스를 지우고, 진술 선택은 쓰는 명령에만 접힌 목록으로 둔다."""

    def page(self):
        """현재 회의 화면 조각."""
        return self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode()

    def test_meeting_forms_keep_evidence_only_where_used(self):
        """자유 입력의 진술 체크박스는 없고, 의심·감싸기·모순 지적·심문 양식만 접힌 진술 목록(고른 개수 0)을 갖는다."""
        self.advance_to(1)
        html = self.page()
        self.assertNotIn('name="selected_cards"', html)
        self.assertEqual(html.count('<details class="evidence-picker">'), 3)
        self.assertEqual(html.count('<span class="evidence-count">0</span>개 고름'), 3)
        self.advance_from(2, 2)
        html = self.page()
        self.assertEqual(html.count('<details class="evidence-picker">'), 1)
        self.assertNotIn('<details class="evidence-picker" open>', html)
        self.advance_from(3, 5)
        # 읽기 옵션·비교 선택은 실제 UI 기능이다. 명령/자유 입력의 쓰이지 않는 근거만 금지한다.
        self.assertNotIn('name="selected_cards"', self.page())
        self.assertNotIn('name="evidence"', self.page())

    def test_cards_show_evidence_only_for_kinds_that_use_it(self):
        """카드 수정: 의심·모순 지적·"왜 말이 다른가"만 진술 목록, "왜 의심하는가"·투표는 없음. 근거가 애매하면 열어 둔다."""
        self.advance_to(2)
        ask = lambda kind: CommandCandidate(Command(CommandKind.INTERROGATE, "mudang", addressee="jumo",
                                                    interrogation_kind=kind), 0.6, UtteranceForm.QUESTION)
        cards = self.parse([ask(InterrogationKind.SUSPICION_REASON), ask(InterrogationKind.CONTRADICTION)])
        html = cards.content.decode()
        self.assertEqual(html.count('class="evidence-picker"'), 1)
        self.assertEqual(html.count('name="evidence"'), len(cards.context["claims"]))

    def test_card_evidence_count_and_ambiguous_open(self):
        """고른 진술 수는 카드 근거 중 선택지에 있는 것만 세고, 근거가 애매한 카드는 목록을 연 채로 그린다."""
        view = self.service.engine.views[1]
        claims = meeting_context(view)["claims"]
        ref = claims[0].card.claim_id
        sus = CommandCandidate(Command(CommandKind.SUSPECT, "mudang", target="blacksmith",
                                       evidence=[EvidenceRef(EvidenceType.CLAIM, ref)]), 0.9, UtteranceForm.ASSERTION,
                               [], {}, [EvidenceRef(EvidenceType.CLAIM, ref)])
        context = cards_context(ParseUtteranceOutput("a1", 1, [sus], None, True), view)
        self.assertEqual(context["cards"][0].evidence_count, 1)
        html = render_to_string("speech_cards.html", context)
        self.assertIn('<details class="evidence-picker" open>', html)
        self.assertIn('<span class="evidence-count">1</span>개 고름', html)


class VoteCardTargets(TestCase):
    """투표 카드 수정의 대상 목록은 투표 대상(결선이면 결선 후보)만 보인다."""

    def test_vote_card_lists_only_vote_targets(self):
        """대상이 빠진 투표 카드의 대상 칸은 vote_targets만, 의심 카드는 활동 대상 전체."""
        view = make_service().engine.views[5]
        vote = CommandCandidate(Command(CommandKind.VOTE, "mudang"), 0.9, UtteranceForm.ASSERTION, ["target"])
        output = ParseUtteranceOutput("a1", 1, [vote, suspect(None, ["target"])], None, True)
        context = cards_context(output, view)
        context["vote_targets"] = context["active_targets"][:1]
        html = render_to_string("speech_cards.html", context)
        selects = [html[i:html.index("</select>", i)] for i in range(len(html)) if html.startswith('name="target"', i)]
        self.assertEqual([part.count("<option value=\"") - 1 for part in selects],
                         [1, len(context["active_targets"])])


class CardMissingLabels(SimpleTestCase):
    """카드의 빠진 항목은 명령 칸 이름이 아니라 카드 수정 칸과 같은 문구로 보인다(11/9 로컬 6인 판)."""

    def test_missing_slot_uses_screen_label_without_leading_dot(self):
        """대상 없는 캐묻기 카드: '질문 상대' 앞에 구분점이 없고, 빠진 시간은 'slot'이 아니라 '시간'."""
        view = make_service().engine.views[1]
        ask = CommandCandidate(Command(CommandKind.INTERROGATE, "mudang", addressee="jumo",
                                       interrogation_kind=InterrogationKind.WHEREABOUTS, night=0),
                               0.6, UtteranceForm.QUESTION, ["slot"])
        html = render_to_string("speech_cards.html", cards_context(ParseUtteranceOutput("a1", 1, [ask], None, True), view))
        self.assertIn("빠진 항목: 시간</p>", html)
        self.assertIn("<p>질문 상대:", html)
        self.assertNotIn("빠진 항목: slot", html)


@skipUnless(shutil.which("node"), "Node.js가 있는 환경에서 화면 스크립트 시험 실행")
class GameScriptSync(SimpleTestCase):
    """진술 체크 → 밤·시간 칸 맞춤 스크립트 시험(`node server/tests/test_game_js.cjs`)을 함께 돌린다."""

    def test_whereabouts_sync_script(self):
        """한밤 진술 체크 시 slot=midnight, 직접 고른 값 유지, 점괘·모르는 밤은 그대로."""
        script = Path(__file__).with_name("test_game_js.cjs")
        done = subprocess.run([shutil.which("node"), str(script)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)


@skipUnless(importlib.util.find_spec("sklearn"), "기준선 해석기의 scikit-learn 설치 환경에서 실행")
@override_settings(INTENT_PARSER_FACTORY="intent.parser.BaselineIntentParser")
class RealEngineSeed6(TestCase):
    """운영과 같은 엔진(규칙 v0)·기준선 해석기·실제 콘텐츠로 시드 6 판을 HTTP로 진행한다(E-08-10 재현 경로)."""

    def setUp(self):
        """메모리 저장소에 운영 엔진 조립을 끼우고 시드 6 판을 연다."""
        from engine.domain import params
        from engine.domain.model import DataSource, GameMeta, PRESETS
        from engine.ports import EngineDeps
        from engine.testing.fakes import FakeSuspicionModel, InMemoryGameRepository, InMemoryProgressRepository
        from server.app.usecases import GameService
        from server.infra.game_factory import ContentPolicyEngine, attach_interactions
        from tools.check_content import load_bundle
        from .interaction_store import MemoryInteractionStore
        content = load_bundle(Path(__file__).resolve().parents[2] / "content" / "bundle_v1.json")

        def prepare(data, player):
            return GameMeta("", PRESETS["base6"], 6, DataSource.HUMAN, params.RULE_PARAMS_VERSION, "paldo-engine",
                            content.content_version, player_pseudonym=player, session_id=data.session_id)
        service = GameService(ContentPolicyEngine(), InMemoryGameRepository(), InMemoryProgressRepository(),
                              lambda meta: EngineDeps(content, FakeSuspicionModel()), prepare)
        attach_interactions(service, MemoryInteractionStore(), None)
        user = User.objects.create_user(username="phase-real")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        self.service = service
        self.content = content
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session",
                                    "mudang_variant": "f"}), content_type="application/json", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.gid = response.json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"
        self.rejects = 0

    def view(self):
        """현재 플레이어 화면 JSON."""
        return self.client.get(self.base, HTTP_ACCEPT="application/json").json()["view"]

    def command(self, **command):
        """JSON 명령 하나(거절 수를 센다)."""
        view = self.view()
        response = self.client.post(self.base + "/commands", json.dumps({
            "game_id": self.gid, "command_id": str(uuid.uuid4()), "expected_state_version": view["state_version"],
            "command": {"actor": "mudang", **command}}), content_type="application/json", HTTP_ACCEPT="application/json")
        self.rejects += response.status_code == 422
        return response

    def until(self, phase):
        """허용 행동 중 진행·침묵·첫 대상 투표로 해당 단계까지 간다."""
        for _ in range(40):
            view = self.view()
            if view["phase"] == phase:
                return view
            allowed = view["legal_kinds"]
            if "advance" in allowed:
                self.command(kind="advance")
            elif "silence" in allowed:
                self.command(kind="silence")
            else:
                self.fail(f"진행할 수 없는 단계 {view['phase']} {allowed}")
        self.fail(f"{phase} 단계에 닿지 못함")

    def utter(self, text):
        """화면 폼으로 해석한다."""
        return self.client.post(self.base + "/utterances", urlencode({"game_id": self.gid,
            "state_version": str(self.view()["state_version"]), "text": text}),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")

    def test_seed6_sentence_phases_and_interrogation_form(self):
        """아침 비활성 → 1라운드 의심 카드 → 심문 행적 질문(한밤 진술)·직전 대상 유지 → 투표 카드 실행, 422 0건."""
        self.assertContains(self.client.get(self.base, HTTP_HX_REQUEST="true"), 'class="utterance-fields" disabled')
        self.until("round1")
        # 주민 풀이 6명보다 많으면(E-08-02) 시드 6 판의 출연자가 바뀌므로 살아 있는 주민 중 하나를 고른다
        target, sentence = self.sentence_target()
        round1 = self.utter(sentence)
        self.assertIn(("suspect", target), [(c.kind, c.target) for c in round1.context["cards"]])
        self.until("interrogation")
        asked = self.utter(sentence)
        self.assertTrue(all(card.kind == "interrogate" for card in asked.context["cards"]))
        if not asked.context["cards"]:
            self.assertEqual(asked.context["failure"], "unsupported_intent")
        view = self.service.detail(self.gid, self.view_owner()).view
        midnight = next(c for c in view.public.claims if c.slot is not None and c.slot.value == "midnight"
                        and c.speaker != "mudang" and c.speaker in view.active)
        page = self.client.get(self.base, HTTP_HX_REQUEST="true")
        self.assertContains(page, f'value="claim:{midnight.claim_id}" data-night="{midnight.night}" data-slot="midnight"')
        # 화면 스크립트가 맞춘 값(night·slot)과 같은 폼 전송
        response = self.client.post(self.base + "/commands", urlencode({
            "game_id": self.gid, "command_id": str(uuid.uuid4()), "state_version": str(self.view()["state_version"]),
            "kind": "interrogate", "addressee": midnight.speaker, "interrogation_kind": "whereabouts",
            "night": str(midnight.night), "slot": "midnight", "evidence": f"claim:{midnight.claim_id}"}),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "행동을 적용하지 못했습니다")
        if self.view()["phase"] == "interrogation":
            self.assertContains(response, f'<option value="{midnight.speaker}" selected>')
        self.until("vote")
        vote = self.utter(sentence)
        self.assertEqual([(c.kind, c.target) for c in vote.context["cards"]], [("vote", target)])
        executed = self.client.post(self.base + "/utterances/" + vote.context["attempt_id"], urlencode({
            "game_id": self.gid, "attempt_id": vote.context["attempt_id"], "reaction": "confirm",
            "command_id": str(uuid.uuid4()), "state_version": str(vote.context["attempt_version"]), "chosen_index": "0"}),
            content_type="application/x-www-form-urlencoded", HTTP_HX_REQUEST="true")
        self.assertEqual(executed.status_code, 200)
        self.assertNotContains(executed, "행동을 적용하지 못했습니다")
        self.assertEqual(self.rejects, 0)

    def sentence_target(self):
        """살아 있는 주민 중 대장장이(없으면 묶음 순서상 첫 주민)와 그를 의심하는 문장."""
        view = self.service.detail(self.gid, self.view_owner()).view
        names = {c.npc_id: c.name_ko for c in self.content.characters}
        alive = [npc for npc in names if npc in view.active and npc != "mudang"]
        target = "blacksmith" if "blacksmith" in alive else alive[0]
        if target == "blacksmith":
            return target, SENTENCE
        name = names[target]
        josa = "이" if (ord(name[-1]) - 0xAC00) % 28 else "가"
        return target, f"{name}{josa} 수상하다"

    def view_owner(self):
        """판 주인 가명."""
        return self.service.repository.load(self.gid)[0].meta.player_pseudonym
