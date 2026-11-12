"""튜토리얼 화면(표현 계층): 서버가 주는 튜토리얼 컨텍스트(tutorial_step_format.md 1·2절)를 템플릿이 그리는 방식.

서버 컨텍스트 함수(tutorial_view.tutorial_context)를 시험 값으로 바꿔 템플릿만 본다. 실제 판 거부 안내는 test_tutorial.py가 본다.
"""
import json
import re
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from server.api.vn_script import VNLine
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service
from .test_vn_results import RecordedGameLines

TOKEN = "11111111-2222-4333-8444-555555555555"


def tutorial_info(step, day, phase, ended=False):
    """형식 문서 2절 모양의 단계 정보와 대사창 줄."""
    lines = [{"line_id": f"tutorial-{step}-0", "text": "반짝이는 곳을 눌러 봐요!"}]
    if ended:
        lines = [{"line_id": "tutorial-end-0", "text": "무당님이 이겼어요!"}]
    info = {"scenario": "tutorial-v1", "step": None if ended else step, "total": 14, "ended": ended,
            "won": True if ended else None, "lines": lines,
            "substeps": [] if ended else [{"key": "submit", "action": "submit",
                                            "target": 'form[data-command-kind="advance"] button[type=submit]', "say": "눌러요"}],
            "exit_url": "/", "restart": {"url": "/tutorial", "start_token": TOKEN, "mudang_variant": "f"}}
    vn = [VNLine(line["line_id"], day, phase, "족제비", line["text"], sprite_id="mascot:jokjebi", guide=True, mascot=True)
          for line in lines]
    return info, vn


def with_tutorial(extra=None, ended=False):
    """서버의 튜토리얼 컨텍스트 함수(tutorial_view.tutorial_context)를 시험용 값으로 바꾼다."""
    from server.api import tutorial_view

    def fake(view, *args, **kwargs):
        info, vn = tutorial_info(view.state_version, view.day, str(getattr(view.phase, "value", view.phase)),
                                 ended=ended or view.result is not None)
        return {"tutorial_mode": True, "tutorial": info, "tutorial_lines": vn, "utterance_prefill": "", **(extra or {})}
    return patch.object(tutorial_view, "tutorial_context", fake)


@override_settings(GAME_UI="vn")
class TutorialMeetingScreen(RecordedGameLines):
    """녹화 판 화면에 튜토리얼 표시가 붙는 자리."""

    test_first_morning_statements_stand_with_speakers = None
    test_vote_lock_and_divination_appear_as_dialogue = None
    test_every_public_record_reaches_the_dialogue = None

    def page(self):
        """판 화면 HTML을 읽는다."""
        return self.client.get(f"/games/{self.game_id}", HTTP_ACCEPT="text/html").content.decode()

    def test_normal_game_has_no_tutorial_parts_but_has_command_hooks(self):
        """일반 판: 튜토리얼 표시·스크립트 자료가 없고, 강조 선택자가 쓰는 data 속성은 늘 붙는다."""
        self.play(4)   # 1일 회의2
        html = self.page()
        self.assertNotIn("data-tutorial", html.split('id="meeting"')[1].split(">")[0])
        self.assertNotIn('id="tutorial-step"', html)
        self.assertNotIn("data-tutorial-bar", html)
        self.assertIn('data-command-kind="suspect"', html)
        self.assertIn('data-action-sheet="suspect"', html)
        self.assertIn("data-utterance-form", html)
        self.play_from(5)  # 투표
        html = self.page()
        self.assertRegex(html, r'data-command-kind="vote" data-command-target="\w+"')

    def test_tutorial_marks_root_json_bar_and_lines_last(self):
        """튜토리얼 판: #meeting 표시, 단계 JSON, 머리 막대, 족제비 줄은 모든 대사 줄 뒤."""
        with with_tutorial():
            html = self.page()
        head = re.search(r'<section id="meeting"[^>]*>', html).group(0)
        self.assertIn("data-tutorial", head)
        data = json.loads(re.search(r'<script id="tutorial-step" type="application/json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(data["step"], 0)
        self.assertIn('data-tutorial-bar', html)
        self.assertIn(f'name="start_token" value="{TOKEN}"', html)
        self.assertIn('action="/tutorial"', html)
        ids = re.findall(r'class="vn-source" data-line-id="([^"]+)"', html)
        self.assertEqual(ids[-1], "tutorial-0-0")
        self.assertIn('data-guide="true"', re.search(r'<p class="vn-source" data-line-id="tutorial-0-0"[^>]*>', html).group(0))

    def test_utterance_prefill_is_readonly(self):
        """12단계: 자유 입력 칸은 대본 문장으로 채운 읽기 전용, 대화 상대는 잠금."""
        self.play(4)
        with with_tutorial({"utterance_prefill": "대장장이가 영 수상하오"}):
            html = self.page()
        textarea = re.search(r'<textarea name="text"[^>]*>([^<]*)</textarea>', html)
        self.assertIn("readonly", textarea.group(0))
        self.assertEqual(textarea.group(1), "대장장이가 영 수상하오")
        self.assertRegex(html, r'<select name="selected_npc" disabled>')

    def test_tutorial_end_hides_survey_ad_and_clues(self):
        """튜토리얼 종료: 설문·결과 광고·원한 단서 탭이 없고 마무리 말풍선과 다시·홈 버튼이 있다."""
        self.play(12)
        normal = self.page()
        for marker in ('id="end-tab-survey"', 'id="end-tab-clues"', 'id="end-panel-survey"'):
            self.assertIn(marker, normal)
        with with_tutorial(ended=True):
            html = self.page()
        for marker in ('id="end-tab-survey"', 'id="end-tab-clues"', 'id="end-panel-survey"', 'id="end-panel-clues"',
                       "result_banner", "설문은 건너뛰기로"):
            self.assertNotIn(marker, html)
        self.assertIn("data-tutorial-end", html)
        self.assertIn("무당님이 이겼어요!", html)
        self.assertIn("처음부터 다시 하기", html)
        self.assertIn('id="end-tab-records"', html)

    def test_every_tutorial_button_has_title(self):
        """튜토리얼 표시로 생긴 버튼도 설명(title)이 있다."""
        with with_tutorial():
            html = self.page()
        for button in re.findall(r"<button[^>]*>", html):
            self.assertTrue("title=" in button or "aria-label=" in button, button)


class TutorialHomeCard(TestCase):
    """홈 튜토리얼 카드."""

    def setUp(self):
        """튜토리얼을 시작하지 않은 시험 계정으로 로그인한다."""
        self.user = User.objects.create_user(username="tutorial-home")
        install_test_profiles(self)(self.user)
        self.client.force_login(self.user)
        configure_game_service(make_service())
        self.addCleanup(configure_game_service, None)

    def test_card_posts_new_token_to_tutorial(self):
        """동의한 계정 홈에 /tutorial 시작 양식(새 UUID·무당 모습)이 있고, 요청마다 토큰이 다르다."""
        tokens = []
        for _ in range(2):
            html = self.client.get("/").content.decode()
            card = re.search(r'<details class="start-mode"[^>]*data-tutorial-card>.*?</details>', html, re.S).group(0)
            self.assertIn('action="/tutorial"', card)
            self.assertIn('name="mudang_variant"', card)
            self.assertNotIn('name="seed"', card)
            self.assertNotIn('name="preset_id"', card)
            tokens.append(re.search(r'name="start_token" value="([^"]+)"', card).group(1))
        self.assertNotEqual(*tokens)
        uuid4().__class__(tokens[0])   # UUID 형식
