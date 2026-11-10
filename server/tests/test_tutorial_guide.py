"""첫 판 안내(족제비) 대사: 승인 문장 그대로, 첫 판에만, 밤에는 그림 없이, 새 게임 정보 없음."""
import re

from django.test import SimpleTestCase, override_settings

from server.api.tutorial_script import GUIDE_STEPS, REPLAY_FIRST_LINE, guide_lines
from .test_vn_results import RecordedGameLines



class GuideScript(SimpleTestCase):
    """안내 대사 순수 함수."""

    def test_steps_and_k(self):
        """8단계 순서, 씐 자 수 채움, 날짜 거르기."""
        self.assertEqual([step for step, *_ in GUIDE_STEPS],
                         ["start", "morning1", "meeting1", "interrogate", "meeting2", "vote", "pre_night", "morning2"])
        lines = guide_lines(2)
        self.assertEqual(len(lines), 15)
        self.assertEqual(lines[9].text, "털어놓은 비밀에서 창귀의 원한에 얽힌 단서가 나오기도 해요. 단서는 판이 끝나도 없어지지 않아요!")
        self.assertIn("씐 사람이 2명 숨어 있어요", lines[1].text)
        self.assertTrue(all("{" not in line.text for line in lines))
        self.assertEqual({line.day for line in guide_lines(1, day=1)}, {1})
        self.assertEqual([line.step for line in guide_lines(1, day=2)], ["morning2"])
        self.assertEqual(guide_lines(1, day=3), [])
        self.assertEqual(lines[0].line_id, "guide-start-0")

    def test_text_is_the_approved_wording(self):
        """승인 문장(11/10)과 글자까지 같다. 회의 안내는 '변호하기'."""
        texts = [line.text for line in guide_lines(1)]
        self.assertIn("회의에서는 차례마다 하나씩 골라요. 의심하기, 변호하기, 말이 안 맞는다고 따지기 같은 거요.", texts)

    def test_replay_changes_only_first_line(self):
        """다른 판에서 다시 켠 안내는 첫 줄만 승인 문장(11/10)으로 바뀌고 나머지는 같다."""
        first, replay = guide_lines(1), guide_lines(1, replay=True)
        self.assertEqual(REPLAY_FIRST_LINE, "무당님, 또 만났네요! 처음부터 다시 알려 드릴게요!")
        self.assertEqual(replay[0].text, REPLAY_FIRST_LINE)
        self.assertIn("처음 오셨죠?", first[0].text)
        self.assertEqual([l.text for l in first[1:]], [l.text for l in replay[1:]])

    def test_no_game_information(self):
        """규칙 안내만 한다. 주민 이름·장소·결과 같은 판 정보 자리가 없다."""
        for line in guide_lines(1):
            self.assertNotRegex(line.text, r"훈장|주모|약초꾼|대장장이|머슴|우물가|서낭당|주막|어귀")


@override_settings(GAME_UI="vn")
class GuideOnScreen(RecordedGameLines):
    """녹화 판 화면의 안내 줄."""

    test_first_morning_statements_stand_with_speakers = None
    test_vote_lock_and_divination_appear_as_dialogue = None
    test_every_public_record_reaches_the_dialogue = None

    def guide(self, lines):
        """안내(족제비) 줄만 고른다."""
        return [line for line in lines if line.get("data-guide") == "true"]

    def test_first_game_morning_order(self):
        """첫 판 1일째 아침: 시작 안내 → 실종 발표 → 아침 안내 → 진술 순서, 족제비 그림."""
        day, phase, lines = self.lines()
        ids = [line["data-line-id"] for line in self.current(day, phase, lines)]
        first_announcement = next(i for i, x in enumerate(ids) if x.startswith("announcement-"))
        first_statement = next(i for i, x in enumerate(ids) if x.startswith("statement-"))
        self.assertEqual(ids[:3], ["guide-start-0", "guide-start-1", "guide-start-2"])
        self.assertLess(3, first_announcement + 1)
        self.assertLess(ids.index("guide-morning1-1"), first_statement)
        self.assertGreater(ids.index("guide-morning1-0"), first_announcement)
        for line in self.guide(lines):
            if line["data-phase"] != "night":
                self.assertIn("mascot/jokjebi", line.get("data-sprite", ""), line)
            self.assertEqual(line["data-speaker"], "족제비")
        self.assertIn("씐 사람이 1명", next(l["text"] for l in lines if l["data-line-id"] == "guide-start-1"))

    def test_night_guide_has_no_picture_and_day_two(self):
        """밤 안내는 그림 없이 글만, 2일째 아침 안내는 하나."""
        self.play(6)
        day, phase, lines = self.lines()
        self.assertEqual(phase, "night")
        night = [line for line in self.guide(lines) if line["data-phase"] == "night"]
        self.assertEqual([line["data-line-id"] for line in night], ["guide-pre_night-0", "guide-pre_night-1"])
        for line in night:
            self.assertNotIn("data-sprite", line)
        self.play_from(7)
        day, phase, lines = self.lines()
        self.assertEqual([line["data-line-id"] for line in self.guide(lines)], ["guide-morning2-0"])

    def test_second_game_guide_is_off_but_toggle_has_pool(self):
        """두 번째 판은 안내가 기본 꺼짐: 대사 줄에는 없고, 숨은 안내 목록과 꺼진 '이번 판에서 안내 보기' 스위치가 있다."""
        started = self.client.post("/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"},
                                   content_type="application/json")
        self.assertEqual(started.status_code, 200, started.content[:300])
        self.game_id = started.json()["view"]["game_id"]
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertNotIn('data-guide="true"', html)
        self.assertNotIn("안내 다시 보기", html)
        toggle = re.search(r"<input[^>]*data-guide-toggle[^>]*>", html).group(0)
        self.assertNotIn("checked", toggle)
        self.assertIn("title=\"켜면 이번 판에서", toggle)
        pool = re.search(r'<div class="vn-guide-pool" data-guide-pool hidden>(.*?)</div>', html, re.S).group(1)
        self.assertNotIn("vn-source\"", pool)   # 숨은 목록 줄은 대사 줄로 잡히지 않는다(클라이언트가 켤 때만 만든다)
        steps = re.findall(r'data-guide-step="(\w+)" data-guide-phase="(\w+)" data-guide-index="(\d+)"', pool)
        self.assertEqual([(s, int(i)) for s, _, i in steps], [(l.step, l.index) for l in guide_lines(1)])
        for opt in re.findall(r"<p class=\"vn-guide-opt\"[^>]*>", pool):
            if 'data-guide-phase="night"' in opt:
                self.assertNotIn("data-sprite", opt)   # 밤 안내는 글만
            else:
                self.assertIn("mascot/jokjebi", opt)
        self.assertIn("씐 사람이 1명", pool)
        self.assertIn(REPLAY_FIRST_LINE, pool)
        self.assertNotIn("처음 오셨죠?", pool)
        self.assertIn("씐 자를 모두 가두면 승리합니다", html)

    def test_first_game_toggle_is_on_with_first_pool(self):
        """첫 판은 서버가 안내 줄을 넣고 스위치는 켜진 채로 나온다. 1일째 뒤 다시 켤 때 쓰는 숨은 목록은 첫 판 표시를 단다."""
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertIn('data-guide="true"', html)
        self.assertIn('<div class="vn-guide-pool" data-guide-pool data-guide-first hidden>', html)
        self.assertIn("checked", re.search(r"<input[^>]*data-guide-toggle[^>]*>", html).group(0))

    def test_classic_page_has_no_help_dialog(self):
        """classic 화면에는 도움말 창이 없어(classic/base.html) 안내 스위치도 그리지 않는다."""
        with override_settings(GAME_UI="classic"):
            html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertNotIn('id="ui-help"', html)
        self.assertNotIn("data-guide-toggle", html)

    def test_result_lock_notice_and_skip_button(self):
        """밤 화면에 투표 결과 잠금 안내 자리와 안내 건너뛰기 버튼이 있다."""
        self.play(6)
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertRegex(html, r'data-result-lock-notice[^>]*hidden')
        self.assertIn("data-vn-guide-skip", html)
        self.assertTrue(re.search(r'data-line-id="vote-summary-\d+-main"', html))


@override_settings(GAME_UI="vn")
class FirstGameEnd(RecordedGameLines):
    """첫 판 종료 화면의 족제비 한풀이 안내(11/10 사용자 승인)."""

    test_first_morning_statements_stand_with_speakers = None
    test_vote_lock_and_divination_appear_as_dialogue = None
    test_every_public_record_reaches_the_dialogue = None

    def end_html(self):
        """녹화 판을 끝까지 진행하고 종료 화면 HTML을 돌려준다."""
        self.play(len(self.steps) - 1)
        html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
        self.assertIn('id="game-end"', html)
        return html

    def test_first_game_end_shows_grudge_guide(self):
        """첫 판이면 승패와 관계없이 족제비 그림과 두 줄이 결과 탭 위에 나온다."""
        html = self.end_html()
        self.assertIn('class="end-guide"', html)
        self.assertIn("그게 한풀이예요!", html)
        self.assertIn("&#x27;원한 단서&#x27; 탭에서 볼 수 있어요", html)
        self.assertIn("mascot/jokjebi", html[html.index('class="end-guide"'):][:400])

    def test_later_game_end_guide_hidden_until_toggle(self):
        """계정의 첫 판이 아니면(진척 목록 첫 판이 다른 판) 말풍선은 숨김으로 내려가고 스위치를 켜야 보인다."""
        (progress,) = self.service.progress.items.values()   # 테스트 가짜 저장소(engine/testing/fakes.py)
        progress.game_ids.insert(0, "earlier-game")
        html = self.end_html()
        self.assertRegex(html, r'<aside class="end-guide"[^>]*data-guide-end hidden>')
        self.assertNotIn('data-guide="true"', html)
