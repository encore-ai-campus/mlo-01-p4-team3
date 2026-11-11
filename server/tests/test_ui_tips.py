"""게임 화면의 모든 버튼에 마우스 올림 설명(title) 또는 이름표(aria-label)가 있는지 본다."""
import re
from html.parser import HTMLParser

from django.test import SimpleTestCase, override_settings

from server.api.template_tags import UI_TIPS, tip
from .test_vn_results import RecordedGameLines


class _Buttons(HTMLParser):
    def __init__(self):
        super().__init__()
        self.missing, self.count = [], 0

    def handle_starttag(self, tag, attrs):
        if tag != "button":
            return
        self.count += 1
        attrs = dict(attrs)
        if not (attrs.get("title") or attrs.get("aria-label")):
            self.missing.append(attrs)


class TipDictionary(SimpleTestCase):
    """버튼 설명 사전의 문구 형식."""
    def test_tips_are_short_single_lines(self):
        """설명은 한 줄·40자 이내다."""
        for key, text in UI_TIPS.items():
            self.assertNotIn("\n", text, key)
            self.assertLessEqual(len(text), 40, key)

    def test_tip_escapes_and_formats(self):
        """이름을 넣고 HTML 특수 문자를 이스케이프한다."""
        self.assertEqual(tip("person", name='<b>'), ' title="&lt;b&gt; 행동 보기"')


@override_settings(GAME_UI="vn")
class EveryButtonHasTip(RecordedGameLines):
    """녹화 판의 각 단계와 종료 화면에서 설명 없는 버튼이 없어야 한다."""

    def test_every_button_on_each_phase(self):
        """녹화 판 모든 단계와 종료 화면에서 설명 없는 버튼이 0개다."""
        seen_phases = set()
        for upto in range(len(self.steps)):
            if upto:
                self.play(upto, first=upto)
            html = self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()
            phase = re.search(r'<section id="meeting" data-phase="(\w+)"', html).group(1)
            seen_phases.add(phase)
            parser = _Buttons()
            parser.feed(html)
            self.assertGreater(parser.count, 0)
            self.assertEqual(parser.missing, [], f"{phase} 단계 버튼에 설명 없음")
        self.assertTrue({"morning", "night", "vote"} <= seen_phases, seen_phases)

    test_first_morning_statements_stand_with_speakers = None
    test_vote_lock_and_divination_appear_as_dialogue = None
    test_every_public_record_reaches_the_dialogue = None
