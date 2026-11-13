"""행동 결과·아침 진술이 대사창 줄(.vn-source)로 나오고, 진위를 흘리지 않는지 검사한다."""
import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from engine.domain.model import (Claim, ClaimType, DivinationResult, LockRecord, Phase, Place, Polarity, Slot,
                                 Speech, VoteRecord, CommandKind)
from server.api import vn_script
from server.api.vn_script import attach_particles, build_vn_lines, has_batchim, join_names, statement_text
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service

RESULT_KO = {DivinationResult.CLEAR: "맑음", DivinationResult.MURKY: "탁함"}
STATEMENT = re.compile(r"^\d+일째 밤 (초저녁|한밤)에는 (우물가|서낭당|주막|마을 어귀|집)에 (있었|없었)소\.$")
SIGHT = re.compile(r"^\d+일째 밤 (초저녁|한밤)에 (우물가|서낭당|주막|마을 어귀|집)에서"
                   r"( .+[을를] 보았소\.| .+[은는] 보지 못했소\.|는 아무도 보지 못했소\.)$")


def fake_view(claims=(), speeches=(), votes=(), locks=(), divinations=None, day=1, phase=Phase.MORNING):
    """vn_script가 읽는 공개 기록만 가진 가짜 PlayerView."""
    names = {"mudang": "mudang", "jumo": "주모", "yakcho": "약초꾼", "blacksmith": "대장장이"}
    public = SimpleNamespace(claims=list(claims), speeches=list(speeches), votes=list(votes), locks=list(locks))
    return SimpleNamespace(names=names, public=public, player=SimpleNamespace(divinations=divinations or {}),
                           day=day, phase=phase)


class StatementTemplates(SimpleTestCase):
    """승인된 두 문장 틀은 상수 한 곳에 있고, 사실 요소만 바뀐다."""

    def test_templates_are_the_approved_sentences(self):
        """11/10 사용자 승인 문장을 그대로 쓴다."""
        self.assertEqual(vn_script.STATEMENT_AFFIRM, "{night}일째 밤 {slot}에는 {place}에 있었소.")
        self.assertEqual(vn_script.STATEMENT_DENY, "{night}일째 밤 {slot}에는 {place}에 없었소.")

    def test_affirm_and_deny_fill_public_values(self):
        """있었소·없었소 틀에 밤·시간대·장소만 채운다."""
        affirm = Claim("cl01", "jumo", ClaimType.WHEREABOUTS, "jumo", 0, Slot.MIDNIGHT, Place.WELL, day=1)
        deny = Claim("cl02", "jumo", ClaimType.WHEREABOUTS, "jumo", 1, Slot.EVENING, Place.TAVERN,
                     polarity=Polarity.DENY, day=2)
        self.assertEqual(statement_text(affirm, []), "0일째 밤 한밤에는 우물가에 있었소.")
        self.assertEqual(statement_text(deny, []), "1일째 밤 초저녁에는 주막에 없었소.")

    def test_lie_and_truth_have_the_same_shape(self):
        """공개 기록에는 진위가 없고, 같은 틀이라 문장 모양으로 거짓을 구분할 수 없다."""
        truth = Claim("cl01", "jumo", ClaimType.WHEREABOUTS, "jumo", 0, Slot.MIDNIGHT, Place.WELL, day=1)
        lie = Claim("cl02", "yakcho", ClaimType.WHEREABOUTS, "yakcho", 0, Slot.MIDNIGHT, Place.SHRINE, day=1)
        lines = build_vn_lines(fake_view([truth, lie]), RESULT_KO)
        self.assertEqual([STATEMENT.match(line.text) is not None for line in lines], [True, True])
        self.assertEqual(set(vars(lines[0])), {"line_id", "day", "phase", "speaker", "text", "speaker_id", "sprite_id",
                                                "full", "result", "guide", "mascot", "actor_name", "beat"})
        for line in lines:
            self.assertNotRegex(line.text, "거짓|사실|진위|lie|true")


class ResultLines(SimpleTestCase):
    """결과 줄은 그 결과를 처음 볼 단계에, 기존 패널 문구로 놓인다."""

    def test_morning_statements_skip_claims_made_by_speeches(self):
        """캐묻기 답변처럼 발언이 만든 주장은 아침 진술 줄에서 뺀다."""
        stated = Claim("cl01", "jumo", ClaimType.WHEREABOUTS, "jumo", 0, Slot.EVENING, Place.WELL, day=1)
        answered = Claim("cl02", "jumo", ClaimType.WHEREABOUTS, "jumo", 0, Slot.MIDNIGHT, Place.WELL, day=1)
        sighting = Claim("cl03", "yakcho", ClaimType.SIGHTING, "yakcho", 0, Slot.EVENING, Place.VILLAGE_GATE, seen=["jumo"], day=1)
        speech = Speech("sp1", 1, Phase.INTERROGATION, "jumo", CommandKind.INTERROGATE, claim_ids=["cl02"])
        view = fake_view([stated, answered, sighting], [speech])
        lines = build_vn_lines(view, RESULT_KO)
        self.assertEqual([line.line_id for line in lines], ["statement-cl01", "statement-cl03"])
        self.assertEqual({(line.day, line.phase) for line in lines}, {(1, "morning")})
        self.assertEqual(lines[0].speaker, "주모")
        self.assertEqual(lines[0].sprite_id, "sprite:jumo")
        self.assertEqual(lines[1].text, "0일째 밤 초저녁에 마을 어귀에서 주모를 보았소.")

    def test_main_vote_shows_when_runoff_starts(self):
        """본투표 동률로 결선이 막 시작돼 결선 표가 아직 없어도 본투표 줄은 결선 화면에 나온다."""
        votes = [VoteRecord("v1", 2, "main", "mudang", "jumo"), VoteRecord("v2", 2, "main", "jumo", "yakcho")]
        lines = build_vn_lines(fake_view(votes=votes, day=2, phase=Phase.RUNOFF), RESULT_KO)
        self.assertEqual([(line.line_id, line.day, line.phase) for line in lines],
                         [("vote-summary-2-main", 2, "runoff"), ("vote-2-main", 2, "runoff"), ("runoff-notice-2", 2, "runoff")])
        self.assertEqual(lines[0].text, "투표 결과 — 주모 1표, 약초꾼 1표")
        self.assertEqual(lines[2].text, "주모와 약초꾼의 표가 같아 결선 투표로 넘어갑니다.")
        self.assertTrue(all(line.result for line in lines))

    def test_votes_locks_and_divination_land_where_first_seen(self):
        """투표·감금·점괘 줄의 단계와 패널 문구."""
        votes = [VoteRecord("v1", 1, "main", "mudang", "jumo"), VoteRecord("v2", 1, "main", "jumo", "yakcho"),
                 VoteRecord("v3", 1, "runoff", "mudang", "jumo"), VoteRecord("v4", 2, "main", "mudang", "yakcho")]
        locks = [LockRecord(1, "jumo", True), LockRecord(2, None)]
        view = fake_view(votes=votes, locks=locks, divinations={1: {"blacksmith": DivinationResult.MURKY}})
        lines = {line.line_id: line for line in build_vn_lines(view, RESULT_KO)}
        self.assertEqual((lines["vote-1-main"].phase, lines["vote-1-main"].text),
                         ("runoff", "1일째 본투표 · 무당 → 주모, 주모 → 약초꾼"))
        self.assertEqual((lines["vote-1-runoff"].day, lines["vote-1-runoff"].phase), (1, "night"))
        self.assertEqual(lines["vote-2-main"].phase, "night")
        self.assertEqual(lines["vote-summary-1-runoff"].text, "결선 결과 — 주모 1표")
        self.assertEqual(lines["vote-summary-2-main"].text, "투표 결과 — 약초꾼 1표")
        self.assertEqual(lines["lock-1"].text, "1일째 · 주모 감금")
        self.assertEqual((lines["lock-1"].sprite_id, lines["lock-1"].full), ("sprite:jumo", True))
        self.assertEqual(lines["lock-reveal-1"].text, "금줄 반응으로 씐 자임이 밝혀졌습니다.")
        self.assertEqual((lines["lock-2"].text, lines["lock-2"].full), ("2일째 · 무감금", False))
        self.assertNotIn("lock-reveal-2", lines)
        divination = lines["divination-1-blacksmith"]
        self.assertEqual((divination.day, divination.phase, divination.speaker, divination.sprite_id),
                         (2, "morning", "무당", ""))
        self.assertEqual(divination.text, "1일째 밤 · 대장장이 · 탁함")
        order = list(lines)
        self.assertEqual(order[order.index("vote-summary-1-main"):order.index("lock-reveal-1") + 1],
                         ["vote-summary-1-main", "vote-1-main", "runoff-notice-1", "vote-summary-1-runoff",
                          "vote-1-runoff", "lock-1", "lock-reveal-1"])

    def test_tally_orders_by_count_then_first_vote(self):
        """득표 요약은 많은 순, 같으면 먼저 표를 받은 순이다."""
        votes = [VoteRecord("v1", 1, "main", "mudang", "jumo"), VoteRecord("v2", 1, "main", "jumo", "yakcho"),
                 VoteRecord("v3", 1, "main", "yakcho", "jumo"), VoteRecord("v4", 1, "main", "blacksmith", "blacksmith")]
        lines = {line.line_id: line for line in build_vn_lines(fake_view(votes=votes, locks=[LockRecord(1, "jumo", False)]), RESULT_KO)}
        self.assertEqual(lines["vote-summary-1-main"].text, "투표 결과 — 주모 2표, 약초꾼 1표, 대장장이 1표")
        self.assertEqual(lines["lock-reveal-1"].text, "금줄 반응으로 씐 자가 아님이 밝혀졌습니다.")
        self.assertNotIn("runoff-notice-1", lines)


class _Lines(HTMLParser):
    """.vn-source 줄의 속성과 대사 문장을 순서대로 모은다."""

    def __init__(self):
        super().__init__()
        self.lines, self._depth, self._text = [], 0, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "p" and "vn-source" in (attrs.get("class") or "").split():
            self.lines.append({**attrs, "text": ""})
        elif tag == "span" and "vn-source-text" in (attrs.get("class") or "").split() and self.lines:
            self._text = self.lines[-1]
        if self._text is not None and tag == "span":
            self._depth += 1

    def handle_endtag(self, tag):
        if self._text is not None and tag == "span":
            self._depth -= 1
            if self._depth == 0:
                self._text["text"] = self._text["text"].strip()
                self._text = None

    def handle_data(self, data):
        if self._text is not None:
            self._text["text"] += data


@override_settings(GAME_UI="vn")
class RecordedGameLines(TestCase):
    """녹화 판을 진행하며 각 단계 화면에 결과 줄이 생기는지 본다."""

    def setUp(self):
        """합성 계정으로 녹화 판을 시작한다."""
        self.user = User.objects.create_user(username="vn-results-player")
        install_test_profiles(self)(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        started = self.client.post("/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"},
                                   content_type="application/json")
        self.game_id = started.json()["view"]["game_id"]
        self.steps = json.loads((settings.BASE_DIR / "fixtures/api/recorded_game_base6.json")
                                .read_text(encoding="utf-8"))["steps"]

    def play(self, upto, first=1):
        """녹화 명령을 first~upto 순서로 보낸다."""
        for step in self.steps[first:upto + 1]:
            response = self.client.post(f"/games/{self.game_id}/commands",
                                        {**step["request"], "game_id": self.game_id, "command_id": str(uuid4())},
                                        content_type="application/json")
            self.assertEqual(response.status_code, 200)

    def play_from(self, step):
        """녹화 명령 하나만 보낸다."""
        self.play(step, first=step)

    def lines(self):
        """현재 화면의 날짜·단계와 대사 줄 목록."""
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        meeting = re.search(r'<section id="meeting" data-phase="(\w+)"[^>]*data-day="(\d+)"', html)
        parser = _Lines()
        parser.feed(html)
        return meeting.group(2), meeting.group(1), parser.lines

    def current(self, day, phase, lines):
        """지정한 날짜·단계의 줄만 고른다."""
        return [line for line in lines if line["data-day"] == day and line["data-phase"] == phase]

    def test_first_morning_statements_stand_with_speakers(self):
        """1일째 아침: 0일째 밤 진술마다 화자가 서서 승인된 틀(또는 카드 문장)로 말한다."""
        day, phase, lines = self.lines()
        self.assertEqual((day, phase), ("1", "morning"))
        statements = [line for line in self.current(day, phase, lines) if line["data-line-id"].startswith("statement-")]
        self.assertTrue(statements)
        for line in statements:
            self.assertTrue(line.get("data-sprite"), line)
            self.assertTrue(STATEMENT.match(line["text"]) or SIGHT.match(line["text"]), line["text"])

    def test_vote_lock_and_divination_appear_as_dialogue(self):
        """투표 뒤 밤 화면에 투표·감금 줄, 점친 다음 아침에 점괘 혼잣말이 나온다."""
        self.play(6)
        day, phase, lines = self.lines()
        self.assertEqual(phase, "night")
        ids = [line["data-line-id"] for line in self.current(day, phase, lines)]
        self.assertIn(f"lock-{day}", ids)
        self.assertTrue(any(i.startswith(f"vote-{day}-") for i in ids))
        self.assertLess(max(i for i, x in enumerate(ids) if x.startswith("vote-")), ids.index(f"lock-{day}"))
        # 밤 화면 인물은 감금 결과 전신 컷 한 줄뿐이다(추방 결과, spec 7절).
        for line in self.current(day, phase, lines):
            if line["data-line-id"] == f"lock-{day}":
                self.assertEqual(line.get("data-full"), "true")
            else:
                self.assertNotIn("data-sprite", line)
            self.assertEqual(line.get("data-result"), "true" if not line["data-line-id"].startswith("guide-") else None)
        self.play_from(7)
        day, phase, lines = self.lines()
        self.assertEqual(phase, "morning")
        current = self.current(day, phase, lines)
        ids = [line["data-line-id"] for line in current]
        divination = next(line for line in current if line["data-line-id"].startswith("divination-"))
        self.assertEqual(divination["data-speaker"], "무당")
        self.assertNotIn("data-sprite", divination)
        announcement = [i for i, x in enumerate(ids) if x.startswith("announcement-")]
        statements = [i for i, x in enumerate(ids) if x.startswith("statement-")]
        self.assertLess(max(announcement), ids.index(divination["data-line-id"]))
        self.assertLess(ids.index(divination["data-line-id"]), min(statements))

    def test_every_public_record_reaches_the_dialogue(self):
        """기록 패널의 공개 투표·감금·내 점괘·진술·발언이 대사 줄로 한 번 이상 나온다."""
        self.play(11)
        state, _ = self.service.owned(self.game_id, self.client.session.get("player_pseudonym") or "")\
            if False else (None, None)
        _, _, lines = self.lines()
        ids = {line["data-line-id"] for line in lines}
        view = self.client.get(f"/games/{self.game_id}").json()["view"]
        public = view["public"]
        spoken = {cid for speech in public["speeches"] for cid in speech["claim_ids"]}
        for claim in public["claims"]:
            if claim["claim_id"] not in spoken and claim["claim_type"] != "divination_reveal":
                self.assertIn(f"statement-{claim['claim_id']}", ids)
        for speech in public["speeches"]:
            self.assertIn(speech["speech_id"], ids)
        for vote in public["votes"]:
            self.assertIn(f"vote-{vote['day']}-{vote['round']}", ids)
        for lock in public["locks"]:
            self.assertIn(f"lock-{lock['day']}", ids)
        for night, items in view["player"]["divinations"].items():
            for target in items:
                self.assertIn(f"divination-{night}-{target}", ids)


class SightingTemplates(SimpleTestCase):
    """목격·못 봄·점괘 공개 문장 틀(11/10 사용자 승인)과 조사 자동 선택."""

    def claim(self, kind, seen, polarity=Polarity.AFFIRM):
        """1일째 밤 한밤 우물가의 진술 하나."""
        return Claim("cl09", "jumo", kind, "jumo", 1, Slot.MIDNIGHT, Place.WELL, polarity=polarity, seen=seen, day=2)

    def test_particles_follow_final_consonant(self):
        """받침 유무로 조사를 고르고, 한글 아님·숫자도 처리한다."""
        self.assertTrue(has_batchim("훈장"))
        self.assertFalse(has_batchim("주모"))
        self.assertTrue(has_batchim("맑음"))
        self.assertFalse(has_batchim("Kim"))      # 한글 아님은 받침 없음으로 본다
        self.assertTrue(has_batchim("3"))          # 삼
        self.assertFalse(has_batchim("2"))         # 이
        self.assertEqual(attach_particles("훈장을(를) 봤다"), "훈장을 봤다")
        self.assertEqual(attach_particles("주모을(를) 봤다"), "주모를 봤다")
        self.assertEqual(attach_particles("훈장와(과) 주모"), "훈장과 주모")
        self.assertEqual(attach_particles("주모와(과) 훈장"), "주모와 훈장")
        self.assertEqual(attach_particles("훈장은(는)"), "훈장은")
        self.assertEqual(attach_particles("머슴은(는) 주모은(는)"), "머슴은 주모는")
        self.assertEqual(attach_particles("맑음(이)라"), "맑음이라")
        self.assertEqual(attach_particles("Kim(이)라"), "Kim라")

    def test_name_lists(self):
        """이름 나열은 마지막 이름 앞에만 와/과를 붙인다."""
        self.assertEqual(join_names(["주모"]), "주모")
        self.assertEqual(join_names(["훈장", "주모"]), "훈장과 주모")
        self.assertEqual(join_names(["주모", "훈장", "머슴"]), "주모, 훈장과 머슴")

    def test_each_template(self):
        """승인된 목격·못 봄·아무도 틀의 완성 문장."""
        self.assertEqual(statement_text(self.claim(ClaimType.SIGHTING, ["x"]), ["주모"]),
                         "1일째 밤 한밤에 우물가에서 주모를 보았소.")
        self.assertEqual(statement_text(self.claim(ClaimType.SIGHTING, ["x", "y"]), ["주모", "훈장"]),
                         "1일째 밤 한밤에 우물가에서 주모와 훈장을 보았소.")
        self.assertEqual(statement_text(self.claim(ClaimType.NON_SIGHTING, ["x"], Polarity.DENY), ["훈장"]),
                         "1일째 밤 한밤에 우물가에서 훈장은 보지 못했소.")
        self.assertEqual(statement_text(self.claim(ClaimType.NON_SIGHTING, [], Polarity.DENY), []),
                         "1일째 밤 한밤에 우물가에서는 아무도 보지 못했소.")

    def test_no_bracket_particles_left_and_no_truth_words(self):
        """괄호 조사가 남지 않고 문장 모양이 틀과 맞는다."""
        self.assertEqual(statement_text(self.claim(ClaimType.SIGHTING, []), []),
                         "1일째 밤 한밤에 우물가에서 누군가를 보았소.")
        for names in ([], ["주모"], ["훈장", "주모", "Kim"]):
            for kind in (ClaimType.SIGHTING, ClaimType.NON_SIGHTING):
                text = statement_text(self.claim(kind, list(names)), list(names))
                self.assertNotRegex(text, r"\(")
                self.assertRegex(text, SIGHT)


class AbstainLines(SimpleTestCase):
    """투표 기권(rule-0.9, 11/11 ②안): 기권표는 득표에서 빠지고, 기권자는 따로 한 줄로 공개된다(문장 승인됨, 11/11 사용자)."""

    def test_abstainers_listed_and_not_counted(self):
        """기권표는 득표 요약에 없고 '표를 던지지 않았소' 줄과 개별 표 줄의 '표 안 던짐'으로 보인다."""
        votes = [VoteRecord("v1", 1, "main", "mudang", None), VoteRecord("v2", 1, "main", "jumo", "yakcho"),
                 VoteRecord("v3", 1, "main", "yakcho", None), VoteRecord("v4", 1, "main", "blacksmith", "yakcho")]
        lines = build_vn_lines(fake_view(votes=votes, locks=[LockRecord(1, "yakcho", False)], phase=Phase.NIGHT),
                               RESULT_KO)
        text = {line.line_id: line.text for line in lines}
        self.assertEqual(text["vote-summary-1-main"], "투표 결과 — 약초꾼 2표")
        self.assertEqual(text["vote-abstain-1-main"], "무당과 약초꾼은 표를 던지지 않았소.")
        self.assertIn("무당 → 표 안 던짐", text["vote-1-main"])

    def test_everyone_abstains(self):
        """모두 기권하면 요약 자리에 '아무도 표를 던지지 않았소'가 나오고 무감금 줄이 이어진다."""
        votes = [VoteRecord("v1", 1, "main", "mudang", None), VoteRecord("v2", 1, "main", "jumo", None)]
        lines = build_vn_lines(fake_view(votes=votes, locks=[LockRecord(1, None, None)], phase=Phase.NIGHT), RESULT_KO)
        text = {line.line_id: line.text for line in lines}
        self.assertEqual(text["vote-summary-1-main"], "투표 결과 — 아무도 표를 던지지 않았소")
        self.assertEqual(text["lock-1"], "1일째 · 무감금")

    def test_no_abstain_line_without_abstainers(self):
        """기권표가 없는 판(rule-0.8)은 기권 줄이 생기지 않는다."""
        votes = [VoteRecord("v1", 1, "main", "mudang", "jumo"), VoteRecord("v2", 1, "main", "jumo", "yakcho")]
        lines = build_vn_lines(fake_view(votes=votes, phase=Phase.NIGHT), RESULT_KO)
        self.assertFalse([line for line in lines if line.line_id.startswith("vote-abstain")])
