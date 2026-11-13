"""B-10-07 공개 사건 표정·한 줄 복귀·정보 경계·실제 HTML 연결 회귀."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings

from engine.domain.engine import PaldoEngine
from engine.domain.model import (Claim, ClaimType, CommandKind, DisappearanceRecord, DivinationResult,
                                 EvidenceRef, EvidenceType, LockRecord, Phase, Place, PublicRecord,
                                 ReactionIntent, Role, Slot, Speech, TellRecord, VoteRecord)
from engine.testing.fakes import ScriptedGameEngine
from server.api.meeting_view import meeting_context
from server.api.ui_assets import vn_sprite_url
from server.api.vn_expressions import TELL_NOTICE, expression_cues
from server.api.vn_script import VNLine, announcement_lines, reaction_lines
from . import test_vn_results as recorded_results
from .test_vn_results import _Lines


def talk(key, actor, kind=CommandKind.EXPLAIN, target=None, day=1, phase=Phase.ROUND1, **kwargs):
    """공개 발언을 만들며 기본 종류는 일반 설명이다."""
    return Speech(key, day, phase, actor, kind, target=target, line_text=f"{actor}의 발언", **kwargs)


def spoken(speech, full=False):
    """공개 발언을 실제 화면과 같은 ID·날짜·단계의 표시 줄로 바꾼다."""
    return VNLine(speech.speech_id, speech.day, speech.phase.value, speech.speaker, speech.line_text,
                  speech.speaker, f"sprite:{speech.speaker}", full=full)


def morning(key, actor, day=1):
    """아침 공개 진술에 쓰는 표시 줄을 만든다."""
    return VNLine(key, day, "morning", actor, "밤의 일을 말하오.", actor, f"sprite:{actor}")


class PublicExpressionRules(SimpleTestCase):
    """확정된 공개 사건의 대상·순서·한 줄 제한과 우선순위를 검사한다."""

    def test_defended_target_not_defending_speaker_then_neutral(self):
        """감싸기 화자는 기본, 감싸진 대상의 다음 대사 하나만 기쁨."""
        speeches = [talk("s1", "mudang", CommandKind.DEFEND, "jumo"),
                    talk("s2", "mudang"), talk("s3", "jumo"), talk("s4", "jumo")]
        moods, _ = expression_cues(PublicRecord(speeches=speeches), list(map(spoken, speeches)))
        self.assertEqual(moods, {"s1": "", "s2": "", "s3": "joy", "s4": ""})

    def test_suspect_immediate_reaction_consumes_next_own_line(self):
        """새 dev의 즉시 반응 줄에 당황을 주고 그 다음 주민 대사는 기본."""
        speeches = [talk("s1", "mudang", CommandKind.SUSPECT, "jumo"), talk("s2", "jumo")]
        public = PublicRecord(speeches=speeches)
        view = type("PublicView", (), {"public": public, "names": {"jumo": "주모"}})()
        reaction = reaction_lines(view)["s1"][0]
        moods, _ = expression_cues(public, [spoken(speeches[0]), reaction, spoken(speeches[1])])
        self.assertEqual((moods["s1"], moods[reaction.line_id], moods["s2"]), ("", "flustered", ""))

    def test_contradiction_target_from_public_evidence(self):
        """모순 지적은 같은 화자의 근거 진술로 대상을 고른다."""
        claims = [Claim(c, "jumo", ClaimType.WHEREABOUTS, "jumo", 0) for c in ("c1", "c2")]
        s = talk("s1", "mudang", CommandKind.POINT_CONTRADICTION,
                 evidence=[EvidenceRef(EvidenceType.CLAIM, "c1"), EvidenceRef(EvidenceType.CLAIM, "c2")])
        target = talk("s2", "jumo")
        moods, _ = expression_cues(PublicRecord(claims=claims, speeches=[s, target]), [spoken(s), spoken(target)])
        self.assertEqual(moods["s2"], "flustered")

    def test_unknown_contradiction_target_does_not_guess(self):
        """대상을 고를 수 없는 공개 기록이면 다른 인물에 표정을 붙이지 않는다."""
        s, target = talk("s1", "mudang", CommandKind.POINT_CONTRADICTION), talk("s2", "jumo")
        moods, _ = expression_cues(PublicRecord(speeches=[s, target]), [spoken(s), spoken(target)])
        self.assertEqual(moods["s2"], "")

    def test_only_public_murky_divination_triggers_target(self):
        """내 점괘는 입력하지 않고 실제로 공개한 탁함 주장 뒤에만 당황."""
        for result, expected in [(DivinationResult.MURKY, "flustered"), (DivinationResult.CLEAR, "")]:
            with self.subTest(result=result):
                claim = Claim("c1", "mudang", ClaimType.DIVINATION_REVEAL, "jumo", 0,
                              divination_result=result)
                speeches = [talk("before", "jumo"), talk("reveal", "mudang", CommandKind.REVEAL_DIVINATION,
                            claim_ids=["c1"]), talk("after", "jumo"), talk("neutral", "jumo")]
                moods, _ = expression_cues(PublicRecord(claims=[claim], speeches=speeches), list(map(spoken, speeches)))
                self.assertEqual((moods["before"], moods["after"], moods["neutral"]), ("", expected, ""))

    def test_confession_is_sad_and_next_line_resets(self):
        """공개 고백 한 줄만 슬픔이고 사적 비밀·정체는 읽지 않는다."""
        a = talk("confess", "jumo", CommandKind.INTERROGATE, reaction=ReactionIntent.CONFESS)
        b = talk("after", "jumo")
        moods, _ = expression_cues(PublicRecord(speeches=[a, b]), [spoken(a), spoken(b)])
        self.assertEqual(moods, {"confess": "sad", "after": ""})

    def test_morning_first_line_each_actor_and_victim_full_cut(self):
        """피해자 컷은 자기 대사가 아니며, 각 인물 첫 대사만 슬픔이고 두 번째는 기본."""
        public = PublicRecord(disappearances=[DisappearanceRecord(0, "yakcho", Place.WELL)])
        view = type("PublicView", (), {"public": public, "day": 1, "names": {"yakcho": "약초꾼"}})()
        lines = announcement_lines(view) + [morning("a1", "jumo"), morning("b1", "blacksmith"), morning("a2", "jumo")]
        moods, _ = expression_cues(public, lines)
        self.assertEqual(moods, {"announcement-0-full": "", "a1": "sad", "b1": "sad", "a2": ""})

    def test_no_disappearance_no_first_line_sadness(self):
        """실종 없는 아침은 슬픔을 주지 않는다."""
        moods, _ = expression_cues(PublicRecord(), [morning("first", "jumo")])
        self.assertEqual(moods["first"], "")

    def test_unshown_announcement_does_not_reveal_later_death(self):
        """나중 실종 기록이 있어도 그 발표 전의 줄에는 영향이 없다."""
        public = PublicRecord(disappearances=[DisappearanceRecord(1, "yakcho", Place.WELL)])
        moods, _ = expression_cues(public, [morning("before", "jumo", day=1)])
        self.assertEqual(moods["before"], "")

    def test_shown_tell_only_and_exact_claim(self):
        """shown=False·다른 주장에는 표정이나 기색 문구를 붙이지 않는다."""
        for shown, expected in [(True, "flustered"), (False, "")]:
            with self.subTest(shown=shown):
                claim = Claim("c1", "jumo", ClaimType.WHEREABOUTS, "jumo", 0)
                line = morning("statement-c1", "jumo")
                moods, tells = expression_cues(PublicRecord(claims=[claim], tells=[TellRecord("c1", 1, shown)]), [line])
                self.assertEqual(moods[line.line_id], expected)
                self.assertEqual(line.line_id in tells, shown)

    def test_speech_tell_remains_when_sadness_wins(self):
        """고백·기색이 겹치면 슬픔이지만 기색 사실 문구는 남긴다."""
        claim = Claim("c1", "jumo", ClaimType.WHEREABOUTS, "jumo", 0)
        s = talk("s1", "jumo", CommandKind.INTERROGATE, reaction=ReactionIntent.CONFESS, claim_ids=["c1"])
        moods, tells = expression_cues(PublicRecord(claims=[claim], speeches=[s], tells=[TellRecord("c1", 1, True)]),
                                       [spoken(s)])
        self.assertEqual(moods["s1"], "sad")
        self.assertIn("s1", tells)

    def test_flustered_wins_over_joy_and_both_are_consumed(self):
        """감싸기와 의심이 함께 걸리면 당황, 이후 대사는 기본."""
        speeches = [talk("defend", "mudang", CommandKind.DEFEND, "jumo"),
                    talk("suspect", "yakcho", CommandKind.SUSPECT, "jumo"), talk("reply", "jumo"), talk("next", "jumo")]
        moods, _ = expression_cues(PublicRecord(speeches=speeches), list(map(spoken, speeches)))
        self.assertEqual((moods["reply"], moods["next"]), ("flustered", ""))

    def test_morning_sadness_wins_over_suspected_reply(self):
        """희생자 발표 뒤 첫 대사에 의심도 걸리면 슬픔, 두 번째는 기본."""
        attack = talk("s", "mudang", CommandKind.SUSPECT, "jumo", phase=Phase.MORNING)
        public = PublicRecord(speeches=[attack], disappearances=[DisappearanceRecord(0, "yakcho", Place.WELL)])
        announcement = VNLine("announcement-0", 1, "morning", "까치", "", mascot=True)
        moods, _ = expression_cues(public, [announcement, spoken(attack), morning("reply", "jumo"),
                                           morning("after", "jumo")])
        self.assertEqual((moods["reply"], moods["after"]), ("sad", ""))

    def test_public_tell_does_not_mark_other_claim(self):
        """기색이 보인 다른 주장을 지금 말하는 주장의 표정에 붙이지 않는다."""
        a = Claim("a", "jumo", ClaimType.WHEREABOUTS, "jumo", 0)
        b = Claim("b", "jumo", ClaimType.WHEREABOUTS, "jumo", 0)
        public = PublicRecord(claims=[a, b], tells=[TellRecord("b", 1, True)])
        moods, tells = expression_cues(public, [morning("statement-a", "jumo")])
        self.assertEqual(moods["statement-a"], "")
        self.assertFalse(tells)

    def test_vote_survival_after_lock_only_and_morning_exception(self):
        """감금 결과 뒤 생존 득표자의 첫 대사는 아침 슬픔 대신 기쁨, 다음은 기본."""
        pub = PublicRecord(votes=[VoteRecord("v1", 1, "main", "mudang", "jumo"),
                                  VoteRecord("v2", 1, "runoff", "yakcho", "blacksmith")],
                           locks=[LockRecord(1, "blacksmith", False)],
                           disappearances=[DisappearanceRecord(1, "yakcho", Place.WELL)])
        lock = VNLine("lock-1", 1, "night", "감금 결과", "대장장이 감금",
                      "blacksmith", "sprite:blacksmith", full=True, result=True)
        view = type("PublicView", (), {"public": pub, "day": 2, "names": {"yakcho": "약초꾼"}})()
        lines = [morning("before", "jumo"), lock] + announcement_lines(view)
        lines += [morning("first", "jumo", 2), morning("second", "jumo", 2)]
        moods, _ = expression_cues(pub, lines)
        self.assertEqual((moods["before"], moods["lock-1"], moods["first"], moods["second"]), ("", "sad", "joy", ""))

    def test_locked_recipient_gets_no_survival_joy(self):
        """감금 당사자는 생존 기쁨 대상에서 제외한다."""
        p = PublicRecord(votes=[VoteRecord("v", 1, "main", "mudang", "jumo")], locks=[LockRecord(1, "jumo")])
        lock = VNLine("lock-1", 1, "night", "", "", "jumo", "sprite:jumo", full=True, result=True)
        moods, _ = expression_cues(p, [lock, morning("later", "jumo", 2)])
        self.assertEqual((moods["lock-1"], moods["later"]), ("sad", ""))

    def test_no_lock_line_no_early_vote_survival_joy(self):
        """투표 기록만 있고 감금 결과 줄이 아직 없으면 기쁨이 아니다."""
        p = PublicRecord(votes=[VoteRecord("v", 1, "main", "mudang", "jumo")], locks=[LockRecord(1, None)])
        moods, _ = expression_cues(p, [morning("before", "jumo")])
        self.assertEqual(moods["before"], "")

    def test_visual_and_guide_lines_do_not_consume_next_speech(self):
        """피해자 한 박자 컷은 다음 자기 대사·아침 첫 대사로 세지 않는다."""
        s = talk("defend", "mudang", CommandKind.DEFEND, "jumo", phase=Phase.MORNING)
        cut = VNLine("visual", 1, "morning", "", "", "jumo", "sprite:jumo", full=True, beat=True)
        reply = morning("reply", "jumo")
        moods, _ = expression_cues(PublicRecord(speeches=[s]), [spoken(s), cut, reply])
        self.assertEqual((moods["visual"], moods["reply"]), ("", "joy"))

    def test_future_events_do_not_change_existing_line(self):
        """나중 의심·투표·고백이 추가돼도 이미 존재한 줄의 표정은 불변."""
        first = talk("first", "jumo")
        original = PublicRecord(speeches=[first])
        before, _ = expression_cues(original, [spoken(first)])
        later = deepcopy(original)
        later.speeches += [talk("suspect", "mudang", CommandKind.SUSPECT, "jumo"),
                           talk("confess", "jumo", reaction=ReactionIntent.CONFESS)]
        after, _ = expression_cues(later, list(map(spoken, later.speeches)))
        self.assertEqual(after["first"], before["first"])

    def test_repeat_replay_and_public_record_are_unchanged(self):
        """재생 위치·역할·비공개 데이터 없이 재계산하며 입력을 수정하지 않는다."""
        p = PublicRecord(speeches=[talk("d", "mudang", CommandKind.DEFEND, "jumo"), talk("r", "jumo")])
        before = deepcopy(p)
        lines = list(map(spoken, p.speeches))
        a = expression_cues(p, lines)
        self.assertEqual(a, expression_cues(p, lines))
        self.assertEqual(p, before)
        self.assertEqual(a[0]["r"], "joy")


class ManifestExpressionFallback(SimpleTestCase):
    """공식 ID·무당 선택·미등록/임시/유실 표정의 기본 대체를 검증한다."""

    def test_all_eight_actual_actors_and_three_moods(self):
        """8명 × 3표정을 공식 manifest 경로로 조회한다."""
        for actor in ("mudang_f", "mudang_m", "hunjang", "jumo", "blacksmith", "meosum", "yakcho", "gwabu"):
            for mood in ("joy", "flustered", "sad"):
                with self.subTest(actor=actor, mood=mood):
                    self.assertEqual(vn_sprite_url(actor, mood), f"/static/assets/sprite/{actor}__{mood}.png")

    def test_mudang_variant_is_kept(self):
        """같은 공개 표정에서도 판에서 고른 남/여 무당 외형을 유지한다."""
        for sex in ("f", "m"):
            self.assertEqual(vn_sprite_url("mudang", "sad", f"portrait:mudang_{sex}"),
                             f"/static/assets/sprite/mudang_{sex}__sad.png")

    def test_unknown_mood_keeps_original(self):
        """허용하지 않은 표정 이름이나 빈 값은 기본 그림이다."""
        for mood in ("", None, "../../sad", "angry"):
            self.assertEqual(vn_sprite_url("jumo", mood), "/static/assets/sprite/jumo.png")

    def test_missing_placeholder_and_unregistered_variant_use_same_actor(self):
        """실제 표정 파일 유실·임시 표정·미등록 표정 모두 동일 인물 기본으로 대체."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [{"kind": "sprite", "id": "jumo", "url": "/base.png", "path": "content/images/sprite/jumo.png"},
                       {"kind": "sprite", "id": "jumo__sad", "url": "/sad.png", "path": "content/images/sprite/sad.png",
                        "placeholder": False}]
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(entries), encoding="utf-8")
            with override_settings(BASE_DIR=root, ASSET_MANIFEST=manifest):
                self.assertEqual(vn_sprite_url("jumo", "sad"), "/base.png")
                p = root / entries[1]["path"]
                p.parent.mkdir(parents=True)
                p.write_bytes(b"registered fixture")
                entries[1]["placeholder"] = True
                manifest.write_text(json.dumps(entries), encoding="utf-8")
                self.assertEqual(vn_sprite_url("jumo", "sad"), "/base.png")
                self.assertEqual(vn_sprite_url("jumo", "joy"), "/base.png")


class RenderedExpressionLines(SimpleTestCase):
    """실제 meeting_context와 템플릿이 선택된 표정·공개 기색만 전달한다."""

    def view(self, speeches=(), claims=(), tells=()):
        """녹화 PlayerView의 공개 기록만 검사 대상 기록으로 바꾼다."""
        view = ScriptedGameEngine().views[1]
        view.public = PublicRecord(speeches=list(speeches), claims=list(claims), tells=list(tells))
        return view

    def rendered(self, view, variant="f"):
        """정식 기록 템플릿을 그려 줄 ID별 속성을 수집한다."""
        context = meeting_context(view)
        context["mudang_portrait_id"] = f"portrait:mudang_{variant}"
        html = render_to_string("meeting_parts/records.html", context)
        parser = _Lines()
        parser.feed(html)
        return html, {x["data-line-id"]: x for x in parser.lines}

    def test_defend_wiring_and_neutral_reset_in_html(self):
        """대상이 기쁨 URL, 이후 대사는 기본 URL을 받는다."""
        speeches = [talk("d", "mudang", CommandKind.DEFEND, "jumo"), talk("r", "jumo"), talk("n", "jumo")]
        _, rows = self.rendered(self.view(speeches))
        self.assertTrue(rows["r"]["data-sprite"].endswith("jumo__joy.png"))
        self.assertTrue(rows["n"]["data-sprite"].endswith("jumo.png"))

    def test_reaction_wiring_uses_new_dev_line(self):
        """dev에 추가된 즉시 반응 줄에도 당황이 연결된다."""
        _, rows = self.rendered(self.view([talk("s", "mudang", CommandKind.SUSPECT, "jumo")]))
        self.assertEqual(rows["reaction-s"]["data-expression"], "flustered")
        self.assertTrue(rows["reaction-s"]["data-sprite"].endswith("jumo__flustered.png"))

    def test_gender_choice_and_public_tell_notice(self):
        """공개 기색 문구는 폴링 본문 밖에 두어 갱신에도 보존한다."""
        claim = Claim("c", "mudang", ClaimType.WHEREABOUTS, "mudang", 0)
        view = self.view([talk("s", "mudang", claim_ids=["c"])], [claim], [TellRecord("c", 1, True)])
        html, rows = self.rendered(view, "m")
        self.assertTrue(rows["s"]["data-sprite"].endswith("mudang_m__flustered.png"))
        self.assertIn(TELL_NOTICE, html)
        self.assertIn("data-vn-tell", html)
        self.assertNotIn(TELL_NOTICE, rows["s"]["text"])

    def test_hidden_tell_does_not_appear_in_html(self):
        """shown=False는 표정·문구·표시 속성 어디에도 쓰지 않는다."""
        c = Claim("c", "jumo", ClaimType.WHEREABOUTS, "jumo", 0)
        html, rows = self.rendered(self.view([talk("s", "jumo", claim_ids=["c"])], [c], [TellRecord("c", 1, False)]))
        self.assertTrue(rows["s"]["data-sprite"].endswith("jumo.png"))
        self.assertNotIn("data-vn-tell", html)

    def test_role_private_belief_and_own_divination_do_not_affect_cues(self):
        """동일한 공개 사건이면 사적 데이터가 달라도 같은 표정이다."""
        view = self.view([talk("d", "mudang", CommandKind.DEFEND, "jumo"), talk("r", "jumo")])
        before = self.rendered(view)[1]
        view.player.divinations = {0: {"jumo": DivinationResult.MURKY}}
        view.locked, view.missing = ["jumo"], ["yakcho"]
        after = self.rendered(view)[1]
        self.assertEqual(before["r"]["data-sprite"], after["r"]["data-sprite"])
        self.assertTrue(after["r"]["data-sprite"].endswith("jumo__joy.png"))


@override_settings(GAME_UI="vn")
class ActualExpressionHTTP(TestCase):
    """기존 HTTP 경로·녹화 엔진에서 전신/첫 대사와 JSON 비변경을 확인한다."""
    setUp = recorded_results.RecordedGameLines.setUp

    def assert_role_invariant(self, public, expected, day=1):
        """공개 스냅샷을 실제 엔진 조회→HTTP 렌더로 보내 역할별 표정·기본 복귀를 비교한다."""
        doc = self.service.repository.docs[self.game_id]
        state = doc["state"]
        state.public, state.day, state.phase = deepcopy(public), day, Phase.ROUND1
        state.active, state.locked, state.missing = ["mudang", "jumo", "blacksmith"], [], []
        self.service.engine = PaldoEngine()
        sources = []
        for role in (Role.VILLAGER, Role.POSSESSED):
            with self.subTest(role=role):
                doc["truth"].roles["jumo"] = role
                response = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html")
                self.assertEqual(response.status_code, 200)
                parser = _Lines()
                parser.feed(response.content.decode())
                rows = {r["data-line-id"]: (r.get("data-sprite"), r.get("data-expression", ""))
                        for r in parser.lines}
                self.assertTrue(rows)
                for key, mood in expected.items():
                    self.assertIn(key, rows)
                    self.assertEqual(rows[key], (f"/static/assets/sprite/jumo{'__' + mood if mood else ''}.png", mood))
                sources.append(rows)
        self.assertEqual(sources[0], sources[1])
        self.assertEqual(doc["state"].public, public)

    def test_defended_target_expression_is_identical_for_both_roles(self):
        """감싸진 대상의 다음 대사는 역할별로 같은 기쁨이며 그 뒤 기본으로 돌아간다."""
        speeches = [talk("before", "jumo"), talk("defend", "mudang", CommandKind.DEFEND, "jumo"),
                    talk("reply", "jumo"), talk("after", "jumo")]
        self.assert_role_invariant(PublicRecord(speeches=speeches), {"before": "", "reply": "joy", "after": ""})

    def test_contradiction_target_expression_is_identical_for_both_roles(self):
        """공개 근거로 지목된 모순 대상의 즉시 반응만 역할과 무관하게 당황이다."""
        claims = [Claim(c, "jumo", ClaimType.WHEREABOUTS, "jumo", 0) for c in ("a", "b")]
        speeches = [talk("before", "jumo"), talk("point", "mudang", CommandKind.POINT_CONTRADICTION,
                    evidence=[EvidenceRef(EvidenceType.CLAIM, c) for c in ("a", "b")]), talk("after", "jumo")]
        self.assert_role_invariant(PublicRecord(claims=claims, speeches=speeches),
                                   {"before": "", "reaction-point": "flustered", "after": ""})

    def test_public_murky_target_expression_is_identical_for_both_roles(self):
        """실제 정체와 무관하게 공개한 탁함 뒤 대상 대사만 당황이며 앞 대사는 기본이다."""
        claim = Claim("reveal-claim", "mudang", ClaimType.DIVINATION_REVEAL, "jumo", 0,
                      divination_result=DivinationResult.MURKY)
        speeches = [talk("before", "jumo"), talk("reveal", "mudang", CommandKind.REVEAL_DIVINATION,
                    claim_ids=[claim.claim_id]), talk("reply", "jumo"), talk("after", "jumo")]
        self.assert_role_invariant(PublicRecord(claims=[claim], speeches=speeches),
                                   {"before": "", "reply": "flustered", "after": ""})

    def test_vote_survival_expression_is_identical_for_both_roles(self):
        """감금 결과 뒤 생존자의 첫 대사 기쁨·아침 슬픔 예외·기본 복귀는 역할별로 같다."""
        public = PublicRecord(speeches=[talk("before", "jumo"), talk("reply", "jumo", day=2),
                                       talk("after", "jumo", day=2)],
                              votes=[VoteRecord("v", 1, "main", "mudang", "jumo")],
                              locks=[LockRecord(1, "blacksmith", False)],
                              disappearances=[DisappearanceRecord(1, "yakcho", Place.WELL)])
        self.assert_role_invariant(public, {"before": "", "reply": "joy", "after": ""}, day=2)

    def test_first_morning_matches_public_death_and_resets(self):
        """기존 /games/{id} HTML이 피해자 컷 기본·각 인물 첫 진술 슬픔을 내려 준다."""
        response = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html")
        self.assertEqual(response.status_code, 200)
        parser = _Lines()
        parser.feed(response.content.decode())
        rows = parser.lines
        first = set()
        found = 0
        for row in rows:
            if row.get("data-full") == "true" and row.get("data-beat") == "true":
                self.assertTrue(row["data-sprite"].endswith(f"/{row['data-speaker-id']}.png"))
            if row["data-line-id"].startswith("statement-"):
                actor = row["data-speaker-id"]
                expected = "__sad.png" if actor not in first else f"/{actor}.png"
                self.assertTrue(row["data-sprite"].endswith(expected), row)
                first.add(actor)
                found += 1
        self.assertGreater(found, 1)
        public = self.client.get(f"/games/{self.game_id}").json()["view"]
        self.assertNotIn("vn_expressions", public)
        self.assertNotIn("sprite", public)

    def test_same_public_events_ignore_actual_role(self):
        """정체가 달라도 같은 공개 대사의 표정 URL은 같다."""
        sources = []
        for role in (Role.VILLAGER, Role.POSSESSED):
            self.service.repository.docs[self.game_id]["truth"].roles["jumo"] = role
            html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
            parser = _Lines()
            parser.feed(html)
            sources.append({r["data-line-id"]: r.get("data-sprite") for r in parser.lines})
        self.assertEqual(sources[0], sources[1])
