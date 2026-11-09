"""B-08-01·B-10-01 판 종료 화면: 같은 판 화면 안 결과·복기·원한 단서 전환, 결말 해금 표시, 선택형 도전 목록."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from engine.domain.model import ClaimTruth, ClueRecord, Role
from server.api.story_view import ending_options, preset_choices
from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class GameEndScreens(TestCase):
    """녹화 판을 끝낸 뒤 판 화면 조각과 복기·단서 조각을 확인한다."""

    def setUp(self):
        """독립 계정·판을 만들고 종료 전 상태를 남긴다."""
        self.user = User.objects.create_user(username="end-owner")
        self.register = install_test_profiles(self)
        self.pid = str(self.register(self.user).player_pseudonym)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        game = self.service.start(StartGameInput(self.pid, "base6", "test-session", "f"), self.pid, True)
        self.gid = game.view.game_id
        self.document = self.service.repository.docs[self.gid]
        self.document["truth"].roles = {"mudang": Role.MUDANG, "blacksmith": Role.POSSESSED, "jumo": Role.VILLAGER}
        self.document["truth"].secrets = {"jumo": "gambling"}
        self.document["truth"].claim_truths = {"private-claim": ClaimTruth("private-claim", False, True)}

    def finish(self):
        """녹화 판의 마지막 공개 상태로 끝내고, 이 판·다른 판에서 얻은 단서를 하나씩 둔다."""
        end = deepcopy(self.service.engine.views[-1])
        self.document["state"] = replace(self.document["state"], result=end.result, phase=end.phase,
                                         public=end.public, state_version=end.state_version)
        self.service.progress.items[self.pid].clues = {
            "clue_debt": ClueRecord("clue_debt", self.gid, 2), "clue_tiger": ClueRecord("clue_tiger", "other-game", 1)}

    def test_running_game_has_no_end_screen(self):
        """진행 중인 판에는 종료 탭·복기 요청 자리가 없다."""
        page = self.client.get(f"/games/{self.gid}", HTTP_ACCEPT="text/html")
        self.assertNotContains(page, 'id="game-end"')
        self.assertNotContains(page, f'/games/{self.gid}/review')

    def test_finished_game_switches_inside_the_same_screen(self):
        """종료 뒤 전체 화면·htmx 조각 모두 같은 #meeting 안에 세 탭을 두고 다른 페이지로 보내지 않는다."""
        self.finish()
        for headers in ({"HTTP_ACCEPT": "text/html"}, {"HTTP_HX_REQUEST": "true"}):
            page = self.client.get(f"/games/{self.gid}", **headers)
            html = page.content.decode()
            self.assertContains(page, 'id="game-end"')
            for tab in ("end-tab-result", "end-tab-review", "end-tab-clues"):
                self.assertContains(page, f'id="{tab}"')
            self.assertContains(page, f'hx-get="/games/{self.gid}/review"')
            self.assertContains(page, 'hx-get="/players/me/progress"')
            self.assertContains(page, 'id="game-result"')
            self.assertEqual(html.count('data-ad-placeholder="result_banner"'), 1)
            self.assertNotIn('data-ad-placeholder="review_side"', html)   # 복기 탭을 열 때만 요청한다
            self.assertNotIn('href="/players/me/progress"', html)        # 단서는 페이지 이동이 아니라 탭
            self.assertNotIn("gambling", html)                            # 정체·비밀은 복기 조각에서만
            self.assertNotIn("몰래 노름", html)

    def test_review_fragment_after_end_only(self):
        """복기 조각은 종료 전 403, 종료 뒤 정체·비밀 제목·이 판의 단서·복기 광고 지면만 보인다."""
        path = f"/games/{self.gid}/review"
        early = self.client.get(path, HTTP_HX_REQUEST="true")
        self.assertEqual(early.status_code, 403)
        self.assertNotIn("몰래 노름".encode(), early.content)
        self.finish()
        before = deepcopy(self.document)
        page = self.client.get(path, HTTP_HX_REQUEST="true")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertNotIn("<html", html)
        self.assertIn("대장장이</strong> · 씐 자", html)
        self.assertIn("주모</strong> · 마을 사람", html)
        self.assertIn("숨긴 비밀: 몰래 노름", html)
        self.assertNotIn("무당</strong>", html)
        self.assertIn("창귀는 생전에 노름빚", html)
        self.assertNotIn("호랑이 울음", html)                             # 다른 판에서 얻은 단서
        self.assertNotIn("private-claim", html)
        self.assertNotIn("행동의 이유", html)                               # 행동 시점 이유가 없으면 숨김
        self.assertEqual(html.count('data-ad-placeholder="review_side"'), 1)
        self.assertEqual(page["Cache-Control"], "private, no-store")
        self.assertEqual(self.document, before)
        self.assertEqual(set(self.client.get(path, HTTP_ACCEPT="application/json").json())
                         , {"game_id", "result", "roles", "secrets", "timeline", "explanation_job_id", "clues_gained"})

    def test_progress_fragment_for_tab_and_page_for_lobby(self):
        """htmx는 탭에 넣을 조각, 일반 요청은 로비용 전체 화면이다."""
        self.finish()
        fragment = self.client.get("/players/me/progress", HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn("<html", fragment)
        self.assertIn('id="progress"', fragment)
        self.assertEqual(fragment.count("처음 얻은 날"), 2)                # 모든 판에서 모은 단서
        page = self.client.get("/players/me/progress", HTTP_ACCEPT="text/html").content.decode()
        self.assertIn("<html", page)
        self.assertIn("홈으로 돌아가기", page)

    def test_exorcism_is_always_available(self):
        """진척 문서에 퇴마가 없어도 퇴마는 선택 가능, 한풀이는 해금 전 잠김이다."""
        progress = self.service.progress.load(self.pid)
        progress.endings_unlocked = []
        options = {item["value"]: item["unlocked"] for item in ending_options(progress)}
        self.assertTrue(options["exorcism"])
        self.assertFalse(options["haewon:h1"])
        home = self.client.get("/").content.decode()
        self.assertNotIn('value="exorcism" disabled', home)


class ChallengeChoices(TestCase):
    """공개된 선택형 도전만 홈 판 구성 목록에 이름으로 보인다."""

    def setUp(self):
        user = User.objects.create_user(username="challenge-owner")
        install_test_profiles(self)(user)
        self.client.force_login(user)

    def test_split_and_labels(self):
        """일반 프리셋과 도전을 나누고 도전 이름에 인원을 붙인다."""
        base, challenges = preset_choices(["base6", "base6_div1", "base6_sight"])
        self.assertEqual(base, ["base6"])
        self.assertEqual([c["value"] for c in challenges], ["base6_div1", "base6_sight"])
        self.assertEqual(challenges[0]["label"], "점 결과를 한 번만 받음 · 6인")

    def test_home_shows_challenges_only_when_enabled(self):
        """설정에 도전이 없으면 영역 자체가 없고, 있으면 optgroup으로 보인다."""
        from server.app.open_start import StartPolicy
        service = make_service()
        service.prepare_start = StartPolicy.__new__(StartPolicy)
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "enabled.json"
            with patch("server.app.open_start.PRESETS_CONFIG", path):
                path.write_text('["base6"]', encoding="utf-8")
                self.assertNotContains(self.client.get("/"), "선택형 도전")
                path.write_text('["base6","base6_secret3"]', encoding="utf-8")
                home = self.client.get("/")
                self.assertContains(home, '<optgroup label="선택형 도전">')
                self.assertContains(home, '<option value="base6_secret3">비밀 있는 주민 3명 · 6인</option>')
                self.assertContains(home, '<option value="base6">base6</option>')
