"""B-07-05의 성능 판정이 오류·중계 실패·50ms 초과를 숨기지 않는지 확인한다."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import TestCase
from unittest.mock import patch
from unittest.mock import MagicMock
from server.tests.asgi_bench import assess, main


class NetworkBenchVerdict(TestCase):
    """실제 서버를 띄우지 않고 측정값의 합격 경계를 검사한다."""

    def setUp(self):
        """정상·장애를 구분할 수 있는 다섯 시나리오 결과를 준비한다."""
        self.rows = [dict(case=name, errors=0, p95_ms=100, finished_games=1,
                          beacon_acks=100, beacon_relayed=100 if name == "daphne_ads" else 0,
                          beacon_failed=100 if name in {"daphne_delay", "daphne_failure"} else 0,
                          beacon_errors=0, ad_errors=0)
                     for name in ("wsgi", "daphne", "daphne_ads", "daphne_delay", "daphne_failure")]

    def test_latency_boundary_and_game_errors(self):
        """증가 50ms는 허용하며 초과·게임 오류·완주 없는 결과는 거부한다."""
        self.rows[3]["p95_ms"] = 150
        self.assertTrue(assess(deepcopy(self.rows)))
        for key, value in (("p95_ms", 150.001), ("errors", 1), ("finished_games", 0)):
            rows = deepcopy(self.rows)
            rows[3][key] = value
            self.assertFalse(assess(rows))

    def test_missing_or_broken_beacon_load_fails(self):
        """비콘 미전송·인증 실패·미발생 장애를 통과로 오인하지 않는다."""
        for key, value in (("beacon_acks", 0), ("beacon_errors", 1), ("beacon_failed", 0), ("ad_errors", 1)):
            rows = deepcopy(self.rows)
            rows[3][key] = value
            self.assertFalse(assess(rows))
        self.rows[2]["beacon_relayed"] = 0
        self.assertFalse(assess(self.rows))

    def test_short_or_invalid_official_run_never_opens_database(self):
        """짧은 사전 시험과 잘못된 이름을 정식 결과로 저장하지 못한다."""
        with patch("server.tests.asgi_bench.mongo_uri") as connection, redirect_stderr(StringIO()):
            for arguments in (["--users", "0"], ["--minutes", "0"],
                              ["--users", "2", "--result-name", "trial"],
                              ["--minutes", "0.25", "--result-name", "trial"],
                              ["--result-name", "../unsafe"]):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    main(arguments)
            connection.assert_not_called()

    def test_cli_forwards_workload_and_hides_connection_error_details(self):
        """기존 CLI가 네트워크 모드로 인자를 넘기며 예외의 접속값은 출력하지 않는다."""
        from server.tests.api_bench import main as entry
        with patch("server.tests.asgi_bench.main", return_value=0) as compare:
            self.assertEqual(entry(["--compare-asgi", "--users", "10", "--minutes", "3"]), 0)
            compare.assert_called_once_with(["--users", "10", "--minutes", "3.0"])
        output = StringIO()
        with patch("server.tests.asgi_bench.main", side_effect=OSError("private-connection-detail")), redirect_stdout(output):
            self.assertEqual(entry(["--compare-asgi"]), 2)
        self.assertNotIn("private-connection-detail", output.getvalue())
        self.assertIn("network_benchmark_failed", output.getvalue())

    def test_actual_command_uses_public_state_and_runoff_targets(self):
        """실제 명령은 API 공개 상태의 버전과 결선 후보를 따른다."""
        from dataclasses import replace
        from random import Random
        from engine.domain.model import CommandKind
        from engine.domain.serde import to_jsonable
        from engine.testing.fakes import ScriptedGameEngine
        from server.tests.asgi_bench import actual_command

        view = ScriptedGameEngine().views[0]
        runoff = replace(view, active=["mudang", "other", "target"], runoff_candidates=["target"],
                         legal_kinds=[CommandKind.VOTE], state_version=23)
        request = actual_command(to_jsonable(runoff), Random(1))
        self.assertEqual(request["expected_state_version"], 23)
        self.assertEqual(request["command"]["kind"], CommandKind.VOTE.value)
        self.assertEqual(request["command"]["target"], "target")

    def test_actual_load_starts_an_idempotent_open_game(self):
        """일반 판 시작에 회차 대신 고유한 시작 UUID를 보낸다."""
        import json
        from threading import Barrier
        from uuid import UUID
        from server.tests.asgi_bench import measure_game

        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 400
        response.headers = {}
        response.read.return_value = b'{}'
        opener = MagicMock()
        opener.open.return_value = response
        with patch("server.tests.asgi_bench.build_opener", return_value=opener), \
                patch("server.tests.asgi_bench.time.monotonic", side_effect=[0, 0, 0, 0, 0, 0, 99]), \
                patch("server.tests.asgi_bench.time.sleep"):
            measure_game("http://localhost", "sessionid=test", None, 1, Barrier(1))
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertIsNone(body["session_id"])
        UUID(body["start_token"])
