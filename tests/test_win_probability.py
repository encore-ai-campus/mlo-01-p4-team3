import unittest

from ml.win_probability import daily_rows, game_split, win_metrics


def event(game, seq, kind="observation", day=1, **payload):
    """시험용 공개 관측 또는 종료 봉투를 만든다."""
    return dict(game_id=game, seq=seq, event_type=kind, day=day, source="bot",
                preset_id="base6", payload=payload or {"snapshot": {"public": {
                    "locks": [], "confessions": [], "disappearances": []},
                    "private": {"role": "possessed"}}})


class WinProbabilityTests(unittest.TestCase):
    """관측 시점·판 분할·보정 지표의 경계를 검사한다."""
    def test_uses_first_public_observation_of_completed_day(self):
        """날짜의 최초 공개 관측만 쓰고 미종료·부하 판을 제외한다."""
        later = event("a", 2)
        later["payload"]["snapshot"]["public"]["confessions"].append({})
        rows = daily_rows([later, event("a", 1),
                           event("a", 3, "game_ended", winner="village"),
                           event("a", 4, day=2), event("unfinished", 0),
                           event("aborted", 0),
                           event("aborted", 1, "game_ended", winner="village", completed=False),
                           {**event("load", 0), "load_test": True},
                           event("load", 1, "game_ended", winner="village")])
        self.assertEqual(list(rows), [("a", 1)])
        features, label = rows["a", 1]
        self.assertEqual(label, 1)
        self.assertEqual(features["confessions"], 0)
        self.assertNotIn("role", features)

    def test_game_split_is_disjoint_and_order_independent(self):
        """같은 판이 학습과 평가에 함께 들어가지 않는다."""
        games = [f"g{i}" for i in range(20)]
        train, test = game_split(games, 7)
        self.assertEqual((train, test), game_split(reversed(games), 7))
        self.assertFalse(train & test)
        self.assertEqual(train | test, set(games))

    def test_log_loss_and_calibration(self):
        """알려진 확률의 로그 손실과 ECE를 확인한다."""
        result = win_metrics([1, 0], [.8, .2])
        self.assertAlmostEqual(result["log_loss"], 0.2231435513)
        self.assertAlmostEqual(result["ece"], .2)


class PortableModelTests(unittest.TestCase):
    """표준 JSON 추론과 학습 라이브러리의 결과를 대조한다."""
    def test_export_matches_sklearn_and_uses_only_first_pre_end_public_observations(self):
        """내보낸 계수·공개 관측 경계·버전 거부·잘못된 모델 생략을 검증한다."""
        import json
        from pathlib import Path
        import tempfile
        from types import SimpleNamespace
        import joblib
        from engine.domain.review import TraceEvent, TraceInput
        from ml.win_probability import main, public_features
        from server.infra.win_probability import WinProbabilityModel

        source_events = []
        for i in range(20):
            observation = event(str(i), 0)
            observation.update(rule_version="test-rule", content_version="test-content")
            end = event(str(i), 2, "game_ended", winner="village" if i % 2 else "changgwi")
            end.update(rule_version="test-rule", content_version="test-content")
            source_events.extend([observation, end])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "events.jsonl"
            path.write_text("\n".join(json.dumps(row) for row in source_events), encoding="utf-8")
            self.assertEqual(main(["--events", str(path), "--out", str(root / "model")]), 0)
            portable = WinProbabilityModel.from_path(root / "model/model.json")
            self.assertIsNotNone(portable)
            trained = joblib.load(root / "model/model.joblib")
            public = {"locks": [], "confessions": [], "disappearances": []}
            first = TraceEvent(1, 1, "round1", 1, "observation",
                               {"snapshot": {"public": public, "private": {"role": "possessed"}}})
            later = TraceEvent(2, 1, "vote", 2, "observation", {"snapshot": {
                "public": dict(public, confessions=[{}]), "private": {"role": "villager"}}})
            ended = TraceEvent(3, 1, "ended", 3, "game_ended", {"winner": "village"})
            future = TraceEvent(4, 2, "round1", 4, "observation", first.payload)
            inp = TraceInput("g", "test-rule", 7, [future, later, ended, first])
            meta = SimpleNamespace(rule_version="test-rule", content_version="test-content",
                                   preset=SimpleNamespace(preset_id="base6"))
            result = portable(inp, meta)
            expected = trained.predict_proba([public_features(1, "base6", public)])[0, 1]
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result[0]["probability"], expected, places=12)
            first.payload["snapshot"]["private"]["role"] = "changed"
            self.assertEqual(result, portable(inp, meta))
            meta.rule_version = "new-rule"
            self.assertIsNone(portable(inp, meta))

            artifact = json.loads((root / "model/model.json").read_text(encoding="utf-8"))
            artifact["intercept"] = float("nan")
            (root / "bad.json").write_text(json.dumps(artifact), encoding="utf-8")
            self.assertIsNone(WinProbabilityModel.from_path(root / "bad.json"))
            artifact["intercept"] = 0
            artifact["coefficients"] = [1]
            (root / "bad.json").write_text(json.dumps(artifact), encoding="utf-8")
            self.assertIsNone(WinProbabilityModel.from_path(root / "bad.json"))
