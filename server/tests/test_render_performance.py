"""B-05-03의 로컬 화면 100회 측정. 공식 성능 증빙이나 운영 인수 완료를 뜻하지 않는다."""
import json
import math
import platform
from time import perf_counter_ns

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class GameRenderPerformance(TestCase):
    """로그인·판 준비를 제외한 실제 게임 전체 화면 응답 시간을 계측한다."""

    def test_one_hundred_game_renders_report_measured_percentiles(self):
        """응답 100개가 모두 성공하는지 검사하고 시간 기준 없이 p50·p95를 출력한다."""
        user = User.objects.create_user(username="render-measurement-player")
        profile = install_test_profiles(self)(user)
        self.client.force_login(user)
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        player = str(profile.player_pseudonym)
        game = service.start(StartGameInput(player, "base6", "test-session", "f"), player, True)
        game_url = f"/games/{game.view.game_id}"

        samples_ms = []
        for index in range(100):
            started = perf_counter_ns()
            response = self.client.get(game_url, HTTP_ACCEPT="text/html")
            elapsed_ms = (perf_counter_ns() - started) / 1_000_000
            with self.subTest(render=index + 1):
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "game.html")
                self.assertTemplateUsed(response, "meeting.html")
                self.assertContains(response, 'id="meeting"')
            samples_ms.append(elapsed_ms)

        ordered = sorted(samples_ms)
        result = {
            "ts": timezone.now().isoformat(),
            "level": "info",
            "service": "game-web",
            "component": "render-measurement",
            "msg": "game_page_render_measurement",
            "task": "B-05-03",
            "requests": len(samples_ms),
            "p50_ms": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
            "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
            "min_ms": round(ordered[0], 3),
            "max_ms": round(ordered[-1], 3),
            "percentile_method": "nearest_rank",
            "environment": {
                "database": connection.vendor,
                "game_engine": type(service.engine).__name__,
                "game_repository": type(service.repository).__name__,
                "client": "django.test.Client",
                "python": platform.python_version(),
                "os": platform.system(),
            },
            "scope": "100 full game-page requests; account/game setup excluded; no warm-up",
            "acceptance_threshold_ms": None,
            "official_performance_evidence": False,
            "limitation": "로컬 측정이며 회귀 시험 통과가 실제 성능·태스크 완료를 뜻하지 않는다.",
        }
        print(json.dumps(result, ensure_ascii=True), flush=True)
