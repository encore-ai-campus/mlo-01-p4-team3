"""행적표(B-10-07, 11/10 사용자 결정): 공개 진술을 (밤, 시간대, 장소) 칸에 모은 화면 모델과 템플릿."""
import dataclasses
import json
import re
import uuid
from dataclasses import replace
from pathlib import Path

from django.template.loader import render_to_string
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from engine.domain.model import (PLAYER_ID, Claim, ClaimType, DisappearanceRecord, DivinationResult, LockRecord, Phase,
                                 Place, PlayerState, Polarity, PublicRecord, Slot)
from engine.ports import PlayerView
from server.api.alibi_grid import build_alibi_grid
from server.api.meeting_view import meeting_context
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service

NAMES = {"a": "훈장", "b": "과부", "c": "포졸", "d": "주모", PLAYER_ID: PLAYER_ID}
E, M = Slot.EVENING, Slot.MIDNIGHT
W, S, T, G, H = Place.WELL, Place.SHRINE, Place.TAVERN, Place.VILLAGE_GATE, Place.HOME


def view_of(claims=(), disappearances=(), locks=(), active=("a", "b", "c", "d"), locked=(), missing=()):
    """허용 공개 필드만 채운 가짜 화면 정보."""
    public = PublicRecord(claims=list(claims), locks=list(locks), disappearances=list(disappearances))
    return PlayerView("g1", 1, 2, Phase.ROUND1, [PLAYER_ID, *active], list(locked), list(missing), public,
                      PlayerState(), [], None, [], None, dict(NAMES))


def where(cid, who, night, slot, place, **kw):
    """행적 주장."""
    return Claim(cid, who, ClaimType.WHEREABOUTS, who, night, slot, place, **kw)


def saw(cid, who, night, slot, place, seen, **kw):
    """목격 주장."""
    return Claim(cid, who, ClaimType.SIGHTING, who, night, slot, place, seen=list(seen), **kw)


def cell(grid, night, place, slot):
    """격자에서 (밤, 장소, 시간대) 칸."""
    n = next(item for item in grid if item.night == night)
    row = next(r for r in n.rows if r.place_key == place.value)
    return next(c for c in row.cells if c.slot == slot.value)


def people(c):
    """칸의 사람 → 괄호 안 글자(본인 말은 쓰지 않고 목격 출처·정정 표시만, 없으면 빈 문자열)."""
    return {p.name: p.label for p in c.people}


def full_night(night, extra=(), who=("a", "b", "c", "d"), first=1):
    """참가자 모두가 두 시간대 행적을 말한 밤."""
    out, n = [], first
    for w in who:
        for slot in (E, M):
            out.append(where(f"n{night}-{n}", w, night, slot, H))
            n += 1
    return out + list(extra)


class GridModel(SimpleTestCase):
    """순수 함수 build_alibi_grid."""
    def test_whereabouts_affirm_and_deny(self):
        """있었다는 사람 목록에 이름만(본인 말은 글로 쓰지 않음), 없었다(DENY)는 사람이 아닌 기타 줄로(11/11 사용자 결정)."""
        grid = build_alibi_grid(view_of([where("c1", "a", 0, E, W), where("c2", "b", 0, E, W, polarity=Polarity.DENY)]))
        c = cell(grid, 0, W, E)
        self.assertEqual(people(c), {"훈장": ""})
        self.assertEqual([s.kind for s in c.people[0].sources], ["self"])
        self.assertEqual([line.text for line in c.others], ["과부: 여기 없었다고 함"])

    def test_sighting_many_seen_and_merge_with_own_whereabouts(self):
        """목격 대상 여럿은 각자 '{화자}이(가) 봄', 화자는 같은 칸에 자기 행적이 있으면 본인 말로 합쳐 이름만."""
        grid = build_alibi_grid(view_of([where("c1", "a", 0, M, T), saw("c2", "a", 0, M, T, ["b", "c"])]))
        self.assertEqual(people(cell(grid, 0, T, M)), {"훈장": "", "과부": "훈장이 봄", "포졸": "훈장이 봄"})

    def test_sighting_speaker_without_own_whereabouts(self):
        """화자의 행적이 그 칸에 없으면 '{대상}을(를) 봤다고 말함'으로 따로 둔다. 대상 여럿은 B·C."""
        grid = build_alibi_grid(view_of([saw("c1", "b", 0, E, S, ["a", "d"])]))
        self.assertEqual(people(cell(grid, 0, S, E))["과부"], "훈장·주모를 봤다고 말함")
        grid = build_alibi_grid(view_of([saw("c1", "b", 0, E, S, ["c"])]))
        self.assertEqual(people(cell(grid, 0, S, E))["과부"], "포졸을 봤다고 말함")

    def test_empty_seen_is_someone(self):
        """목격 대상이 비면 '누군가' 한 줄, 화자 쪽 출처도 누군가."""
        grid = build_alibi_grid(view_of([saw("c1", "c", 0, E, G, [])]))
        self.assertEqual(people(cell(grid, 0, G, E)), {"누군가": "포졸이 봄", "포졸": "누군가를 봤다고 말함"})

    def test_same_person_many_sources_one_line_and_no_duplicate(self):
        """같은 사람의 출처는 한 줄로 합치고, 같은 주장은 한 번만 센다."""
        claims = [where("c1", "a", 0, E, W), saw("c2", "b", 0, E, W, ["a"]), saw("c3", "c", 0, E, W, ["a", "a"]),
                  where("c4", "b", 0, E, W), where("c5", "c", 0, E, W)]
        c = cell(build_alibi_grid(view_of(claims)), 0, W, E)
        self.assertEqual(people(c)["훈장"], "과부가 봄, 포졸이 봄")      # 본인 말과 목격이 함께면 목격 출처만
        self.assertEqual([s.kind for s in next(p for p in c.people if p.name == "훈장").sources], ["self", "seen_by", "seen_by"])
        self.assertEqual(len(c.people), 3)

    def test_same_person_two_cells_kept_without_mark(self):
        """같은 시간대 두 칸에 보여도 그대로 두고 판정 표시가 없다."""
        grid = build_alibi_grid(view_of([where("c1", "a", 0, M, T), where("c2", "b", 0, M, W), saw("c3", "b", 0, M, W, ["a"])]))
        self.assertIn("훈장", people(cell(grid, 0, T, M)))
        self.assertEqual(people(cell(grid, 0, W, M))["훈장"], "과부가 봄")

    def test_correction_new_and_before(self):
        """정정 주장은 새 칸에 '정정한 진술', 고친 대상은 원래 칸에 '고치기 전'(흐림)."""
        claims = [where("c1", "a", 0, E, W), where("c9", "a", 0, E, S, corrects="c1", day=2)]
        grid = build_alibi_grid(view_of(claims))
        before = cell(grid, 0, W, E).people[0]
        self.assertEqual(before.label, "고치기 전")
        self.assertTrue(before.faded)
        after = cell(grid, 0, S, E).people[0]
        self.assertEqual(after.label, "정정한 진술")
        self.assertFalse(after.faded)

    def test_sighting_kept_when_own_whereabouts_corrected(self):
        """같은 칸의 자기 행적이 고쳐졌어도 유효한 목격 진술은 '봤다고 말함'으로 남고 흐리게 하지 않는다."""
        claims = [where("c1", "a", 0, E, W), saw("c2", "a", 0, E, W, ["b"]), where("c9", "a", 0, E, S, corrects="c1")]
        person = next(p for p in cell(build_alibi_grid(view_of(claims)), 0, W, E).people if p.npc_id == "a")
        self.assertEqual(person.label, "고치기 전, 과부를 봤다고 말함")
        self.assertFalse(person.faded)

    def test_correction_target_missing(self):
        """고친 대상이 기록에 없으면 새 주장에만 정정한 진술."""
        grid = build_alibi_grid(view_of([where("c9", "a", 0, E, S, corrects="gone")]))
        self.assertEqual(people(cell(grid, 0, S, E)), {"훈장": "정정한 진술"})

    def test_correction_chain(self):
        """정정이 이어지면 마지막만 정정한 진술이고 앞은 모두 고치기 전."""
        claims = [where("c1", "a", 0, E, W), where("c2", "a", 0, E, S, corrects="c1"), where("c3", "a", 0, E, T, corrects="c2")]
        grid = build_alibi_grid(view_of(claims))
        self.assertEqual(people(cell(grid, 0, W, E))["훈장"], "고치기 전")
        self.assertEqual(people(cell(grid, 0, S, E))["훈장"], "고치기 전")
        self.assertEqual(people(cell(grid, 0, T, E))["훈장"], "정정한 진술")

    def test_late_confession_goes_to_night_zero(self):
        """칸은 말한 날이 아니라 가리키는 밤으로 정한다(3일째 고백도 0일째 밤 칸)."""
        claims = [where("c1", "a", 0, E, W), where("c2", "a", 1, E, T, day=2), where("c3", "a", 0, E, S, corrects="c1", day=3)]
        grid = build_alibi_grid(view_of(claims))
        self.assertIn("훈장", people(cell(grid, 0, S, E)))
        self.assertNotIn("훈장", people(cell(grid, 1, S, E)))

    def test_crime_cell_public_home_and_evening(self):
        """피해 칸은 실종 기록의 장소·시간대(slot 그대로). 집이면 각자 집 줄에 '({이름}의 집)'."""
        grid = build_alibi_grid(view_of(disappearances=[DisappearanceRecord(0, "d", W, M)]))
        self.assertEqual(cell(grid, 0, W, M).crime, "실종: 주모")
        self.assertEqual(grid[0].caption, "0일째 밤 · 실종: 주모 · 한밤 우물가")
        grid = build_alibi_grid(view_of(disappearances=[DisappearanceRecord(0, "d", H, M)]))
        self.assertEqual(cell(grid, 0, H, M).crime, "실종: 주모 (주모의 집)")
        self.assertEqual(grid[0].caption, "0일째 밤 · 실종: 주모 · 한밤 주모의 집")
        home_row = next(r for r in grid[0].rows if r.place_key == "home")
        self.assertEqual(home_row.place_label, "각자 집")
        grid = build_alibi_grid(view_of(disappearances=[DisappearanceRecord(0, "d", T, E)]))
        self.assertEqual(cell(grid, 0, T, E).crime, "실종: 주모")
        self.assertEqual(cell(grid, 0, T, M).crime, "")

    def test_night_without_disappearance_and_only_disappearance(self):
        """실종 기록이 없는 밤은 caption이 밤만, 실종 기록만 있는 밤(아침 진술 없음)도 밤이 생긴다."""
        grid = build_alibi_grid(view_of([where("c1", "a", 0, E, W)], [DisappearanceRecord(1, "b", S, M)]))
        self.assertEqual([n.night for n in grid], [0, 1])
        self.assertEqual(grid[0].caption, "0일째 밤")
        self.assertEqual(cell(grid, 1, S, M).crime, "실종: 과부")
        self.assertEqual(grid[1].no_claim, "")      # 아침이 없으면 진술 없음을 그리지 않는다

    def test_missing_person_seen_before_disappearing_no_self_fill(self):
        """실종자도 실종 전 시간대 목격 대상으로 나오고, 자기 행적은 보충하지 않는다."""
        grid = build_alibi_grid(view_of([saw("c1", "a", 1, E, W, ["d"])], [DisappearanceRecord(1, "d", S, M)],
                                        active=("a", "b", "c"), missing=("d",)))
        self.assertEqual(people(cell(grid, 1, W, E))["주모"], "훈장이 봄")      # 지금 상태(실종)는 붙이지 않는다
        self.assertNotIn("주모", people(cell(grid, 1, S, M)))

    def test_many_nights_sorted_and_only_latest_open(self):
        """밤별로 나뉘고 오름차순, 최신 밤만 펼친다."""
        grid = build_alibi_grid(view_of([where("c3", "a", 2, E, W), where("c1", "a", 0, E, W), where("c2", "a", 1, E, W)]))
        self.assertEqual([n.night for n in grid], [0, 1, 2])
        self.assertEqual([n.open for n in grid], [False, False, True])
        self.assertEqual([n.label for n in grid], ["0일째 밤", "1일째 밤", "2일째 밤"])

    def test_no_claim_participants_from_disappearances_and_locks(self):
        """밤별 참가자는 실종·감금 기록으로 복원한다. 0일째 실종자 d는 0일째 밤부터, 1일째 감금된 c는 1일째 밤부터 빠진다."""
        claims = full_night(0, who=("a", "b")) + full_night(1, who=("a",), first=20)
        view = view_of(claims, [DisappearanceRecord(0, "d", W, M), DisappearanceRecord(1, "b", S, M)],
                       [LockRecord(1, "c")], active=("a",), locked=("c",), missing=("d", "b"))
        grid = build_alibi_grid(view)
        self.assertEqual(grid[0].no_claim, "진술 없음: 포졸")       # 감금 전이라 0일째 밤 참가자
        self.assertEqual(grid[1].no_claim, "")                    # b는 그 밤 실종, c는 감금, d는 실종

    def test_no_claim_one_slot(self):
        """한 시간대만 빠지면 시간대를 적는다."""
        claims = full_night(0, who=("a", "b", "c")) + [where("x1", "d", 0, E, W)]
        grid = build_alibi_grid(view_of(claims))
        self.assertEqual(grid[0].no_claim, "진술 없음: 주모(한밤)")

    def test_no_claim_skipped_when_uncertain(self):
        """참가자가 아닌 화자가 있으면(이전 기록 등) 진술 없음을 그리지 않는다."""
        claims = [where("c1", "d", 1, E, W)]
        grid = build_alibi_grid(view_of(claims, [DisappearanceRecord(0, "d", S, M)], active=("a", "b", "c"), missing=("d",)))
        self.assertEqual(grid[1].night, 1)
        self.assertEqual(grid[1].no_claim, "")

    def test_locked_person_past_night_kept_without_status(self):
        """지금 감금된 사람의 지난 밤 주장도 그대로 두고, 이름 뒤에 지금 상태를 붙이지 않는다(11/11 사용자 결정)."""
        grid = build_alibi_grid(view_of([where("c1", "c", 0, E, T)], locks=[LockRecord(1, "c")],
                                        active=("a", "b", "d"), locked=("c",)))
        person = cell(grid, 0, T, E).people[0]
        self.assertEqual((person.name, person.label), ("포졸", ""))
        self.assertFalse({f.name for f in dataclasses.fields(person)} & {"status", "status_label"})

    def test_empty_game(self):
        """격자에 둘 공개 기록이 없으면(점괘 공개만 있어도) 빈 목록."""
        self.assertEqual(build_alibi_grid(view_of()), [])
        reveal = Claim("c1", PLAYER_ID, ClaimType.DIVINATION_REVEAL, "a", 0, divination_result=DivinationResult.CLEAR)
        self.assertEqual(build_alibi_grid(view_of([reveal])), [])

    def test_mudang_speaker_skipped(self):
        """화자가 무당인 주장은 건너뛴다."""
        grid = build_alibi_grid(view_of([where("c1", PLAYER_ID, 0, E, W), where("c2", "a", 0, E, T)]))
        self.assertEqual(people(cell(grid, 0, W, E)), {})

    def test_non_sighting_lines(self):
        """비목격(가짜)은 사람 배치 없이 기타 줄로만."""
        claims = [Claim("c1", "a", ClaimType.NON_SIGHTING, "a", 0, E, G, Polarity.DENY, seen=["b"]),
                  Claim("c2", "c", ClaimType.NON_SIGHTING, "c", 0, E, G, Polarity.DENY)]
        c = cell(build_alibi_grid(view_of(claims)), 0, G, E)
        self.assertEqual(c.people, ())
        self.assertEqual([line.text for line in c.others], ["훈장: 과부를 못 봤다고 함", "포졸: 아무도 못 봤다고 함"])

    def test_same_input_same_output_regardless_of_preset(self):
        """창귀 수·역할은 입력에 없다. 같은 공개 입력이면 같은 격자."""
        claims = [where("c1", "a", 0, M, T), saw("c2", "a", 0, M, T, ["b"])]
        self.assertEqual(build_alibi_grid(view_of(claims)), build_alibi_grid(view_of(claims)))

    def test_output_only_plain_values(self):
        """출력에는 문자열·정수·불리언(과 그 묶음)만 있고 진위·역할 필드가 없다."""
        claims = full_night(0, [saw("s1", "a", 0, E, H, ["b"])])
        grid = build_alibi_grid(view_of(claims, [DisappearanceRecord(0, "d", W, M)]))

        def walk(value):
            if dataclasses.is_dataclass(value):
                names = {f.name for f in dataclasses.fields(value)}
                self.assertFalse(names & {"is_true", "role", "faction", "truth", "deliberate_lie"})
                for f in dataclasses.fields(value):
                    walk(getattr(value, f.name))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
            else:
                self.assertIsInstance(value, (str, int, bool))
        walk(grid)


class GridTemplate(SimpleTestCase):
    """행적표 조각과 기록 창 배치. VN은 무대 패널(in_panel), classic은 진술표 앞(11/11 사용자 결정)."""

    def render(self, view, **extra):
        """VN 무대 패널로 쓰는 행적표 조각을 그린다."""
        return render_to_string("meeting_parts/alibi_grid.html", {**meeting_context(view), "in_panel": True, **extra})

    def records(self, view, **extra):
        """기록 창(records.html)을 그린다."""
        return render_to_string("meeting_parts/records.html", {**meeting_context(view), **extra})

    def recorded_view(self):
        """녹화 판 1일째 화면 정보."""
        return make_service().engine.views[1]

    @override_settings(GAME_UI="vn")
    def test_vn_records_window_has_no_grid(self):
        """VN 공개 기록 창에는 행적표가 없고 진술표·진술 카드 훅은 그대로."""
        html = self.records(self.recorded_view())
        self.assertNotIn("alibi-grid", html)
        self.assertIn('id="claim-list-title"', html)
        self.assertIn("data-record-id=", html)

    def test_classic_grid_before_claim_table(self):
        """classic 화면은 무대 버튼이 없어 기록 안 진술표 앞에 그대로 둔다(섹션 제목 포함)."""
        html = self.records(self.recorded_view(), classic_mode=True)
        self.assertIn('class="alibi-grid"', html)
        self.assertIn('<h2 id="alibi-grid-title">행적표</h2>', html)
        self.assertLess(html.index("행적표"), html.index('id="claim-list-title"'))

    def test_panel_fragment_uses_dialog_title(self):
        """무대 패널에서는 창 제목(ui-stage.js가 만드는 h2)으로 이름을 붙이고 섹션 제목을 두지 않는다."""
        html = self.render(self.recorded_view())
        self.assertIn('class="record-panel alibi-grid" aria-labelledby="meeting-alibi-grid-dialog-title"', html)
        self.assertNotIn('id="alibi-grid-title"', html)

    def test_shown_with_only_disappearance(self):
        """진술이 없고 실종 기록만 있어도 섹션이 나온다(classic 기록 창도 진술표 없이 행적표만)."""
        view = self.recorded_view()
        view = replace(view, public=replace(view.public, claims=[],
                                            disappearances=[DisappearanceRecord(0, view.missing[0] if view.missing else "x", W, M)]))
        self.assertIn("실종: ", self.render(view))
        html = self.records(view, classic_mode=True)
        self.assertIn('id="alibi-grid-title"', html)
        self.assertNotIn('id="claim-list-title"', html)

    def test_hidden_without_records(self):
        """격자 데이터가 비면 섹션을 그리지 않는다."""
        view = self.recorded_view()
        view = replace(view, public=replace(view.public, claims=[], disappearances=[]))
        self.assertNotIn("alibi-grid", self.render(view))
        self.assertNotIn("alibi-grid", self.records(view, classic_mode=True))

    def test_table_structure_and_crime_text(self):
        """열·행 머리글 scope, caption, 피해 칸 글, 진술 카드 속성·초상·주장 ID 글자 없음."""
        view = view_of(full_night(0), [DisappearanceRecord(0, "d", W, M)])
        grid = self.render(view)
        self.assertEqual(grid.count('<th scope="col">'), 2)
        self.assertEqual(grid.count('<th scope="row">'), 5)
        self.assertIn("<caption", grid)
        self.assertIn("각자 집", grid)
        self.assertRegex(grid, r'class="alibi-grid-crime"[^>]*>\s*<p class="alibi-grid-crime-label">실종: 주모</p>')
        self.assertNotIn("data-record-", grid)
        self.assertNotIn("vn-source", grid)
        self.assertNotIn("review-portrait", grid)
        for claim in view.public.claims:                  # 주장 ID는 화면 글자로 내지 않는다
            self.assertNotIn(claim.claim_id, re.sub(r"<[^>]+>", " ", grid))

    def test_names_without_self_source_or_status(self):
        """본인 말만 있으면 이름만, 칸 안 이름에 지금 상태 글이 없다(11/11 사용자 결정)."""
        view = view_of(full_night(0, [saw("s1", "a", 0, E, H, ["b"])]), [DisappearanceRecord(1, "c", W, M)],
                       locks=[LockRecord(1, "b")], active=("a", "d"), locked=("b",), missing=("c",))
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", self.render(view)))
        self.assertNotIn("본인 말", text)
        self.assertNotIn("(실종)", text)
        self.assertNotIn("(감금)", text)
        self.assertIn("과부 ( 훈장이 봄 )", text)
        self.assertIn("실종: 포졸", text)

    def test_guides_and_duo_line(self):
        """안내 줄 1·2는 늘, 안내 줄 3은 창귀 2명 판에만."""
        html = self.render(view_of(full_night(0)))
        self.assertIn("공개된 진술을 밤·시간대·장소별로 모았습니다. 누가 어디 있었는지는 직접 판단하세요.", html)
        self.assertIn("같은 칸에 있어도 함께 있었다는 뜻은 아닙니다.", html)
        self.assertNotIn("이름 옆 상태", html)
        self.assertNotIn("목격된 사람도 씐 자일 수 있습니다.", html)
        context = {**meeting_context(view_of(full_night(0)), guide_k=2), "in_panel": True}
        self.assertIn("목격된 사람도 씐 자일 수 있습니다.", render_to_string("meeting_parts/alibi_grid.html", context))
        context = {**meeting_context(view_of(full_night(0)), guide_k=1), "in_panel": True}
        self.assertNotIn("목격된 사람도 씐 자일 수 있습니다.", render_to_string("meeting_parts/alibi_grid.html", context))

    def test_only_latest_night_open(self):
        """최신 밤 details만 open."""
        html = self.render(view_of(full_night(0) + full_night(1, first=30)))
        self.assertRegex(html, r'data-grid-night="0">')
        self.assertRegex(html, r'data-grid-night="1" open>')


class GridStagePanel(TestCase):
    """VN 판 화면: 무대 버튼 줄의 '행적표' 버튼과 무대 패널, 명령 뒤 #meeting 교체 조각에도 함께 그려짐."""

    def setUp(self):
        """동의한 계정과 녹화 판을 만든다."""
        user = User.objects.create_user(username="alibi-grid")
        install_test_profiles(self)(user)
        self.client.force_login(user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        response = self.client.post("/games", json.dumps({"preset_id": "base6", "session_id": "test-session",
                                    "mudang_variant": "f"}), content_type="application/json", HTTP_ACCEPT="application/json")
        self.gid = response.json()["view"]["game_id"]
        self.base = f"/games/{self.gid}"

    @override_settings(GAME_UI="vn")
    def test_button_between_records_and_notebook_and_panel(self):
        """버튼은 '기록' 바로 다음(수첩 앞), 패널은 공개 기록 창 뒤, 기록 창 안에는 행적표가 없다."""
        html = self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode()
        start = html.index('class="stage-toolbar"')
        toolbar = html[start:html.index("</nav>", start)]
        opens = re.findall(r'data-stage-open="([^"]+)"', toolbar)
        self.assertEqual(opens[opens.index("meeting-records") + 1], "meeting-alibi-grid")
        self.assertRegex(toolbar, r'data-stage-open="meeting-alibi-grid"[^>]*>행적표</button>')
        self.assertIn('<section id="meeting-alibi-grid" class="meeting-records" data-stage-panel="행적표" tabindex="-1">', html)
        records = html[html.index('id="meeting-records"'):html.index('<section id="meeting-alibi-grid"')]
        self.assertNotIn("alibi-grid", records)
        self.assertIn("data-grid-night=", html[html.index('<section id="meeting-alibi-grid"'):])

    @override_settings(GAME_UI="vn")
    def test_panel_redrawn_in_command_swap(self):
        """명령 뒤 htmx가 받는 #meeting 조각에도 행적표 창과 버튼이 새 상태로 들어 있다."""
        step = self.service.engine.steps[1]
        response = self.client.post(self.base + "/commands", json.dumps(dict(step["request"], game_id=self.gid,
                                    command_id=str(uuid.uuid4()))), content_type="application/json",
                                    HTTP_HX_REQUEST="true")
        html = response.content.decode()
        self.assertIn('<section id="meeting-alibi-grid"', html)
        self.assertIn('data-stage-open="meeting-alibi-grid"', html)

    @override_settings(GAME_UI="vn")
    def test_header_button_after_records_opens_stage_panel(self):
        """오른쪽 위 머리글에 '진술표' 바로 뒤 '행적표' 버튼(같은 툴팁 키, 처음엔 숨김). 누르면 무대 버튼을 대신 누르고 튜토리얼 잠금 대상이다."""
        html = self.client.get(self.base, HTTP_ACCEPT="text/html").content.decode()
        header = html[html.index('class="site-links"'):html.index("</nav>", html.index('class="site-links"'))]
        self.assertRegex(header, r'>진술표</button><button type="button" data-alibi-grid-open hidden title="진술을 밤·시간대·장소별로 모아 봅니다"[^>]*>행적표</button>')
        root = Path(__file__).resolve().parents[2] / "web/static/js"
        vn = (root / "ui-vn.js").read_text(encoding="utf-8")
        self.assertIn("alibiButton.hidden=!root?.querySelector('[data-stage-open=\"meeting-alibi-grid\"]')", vn)
        self.assertIn("if(event.target.closest('[data-alibi-grid-open]'))document.querySelector('[data-stage-open=\"meeting-alibi-grid\"]')?.click();", vn)
        self.assertIn(".app-header [data-alibi-grid-open]", (root / "ui-tutorial.js").read_text(encoding="utf-8"))

    @override_settings(GAME_UI="classic")
    def test_classic_has_no_stage_button(self):
        """classic 판 화면은 무대 버튼·패널 없이 기록 안 행적표만."""
        html = self.client.get(self.base, HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn("meeting-alibi-grid", html)
        self.assertIn('id="alibi-grid-title"', html)
