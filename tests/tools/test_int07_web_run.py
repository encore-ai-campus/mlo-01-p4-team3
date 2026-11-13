"""INT-07 웹 자동 재현 도구의 판정 규칙(도메인 함수) 시험."""
import unittest

from tools.evidence_result import build_result
from tools.perf.int07_web_run import forbidden_hits, json_keys, metrics_of, morning_missing, summarize


def good_result():
    """모든 자동 확인이 맞는 측정값."""
    game = {"winner": "villagers", "commands": 6, "renders": 4, "nights_seen": [0, 1], "morning_missing": [],
            "mornings_checked": 3, "errors": [], "view_result_matches": True}
    return {"games": [dict(game), dict(game), dict(game)], "http_5xx": 0, "progress_has_games": True,
            "duplicate": {"replayed": True, "same_view": True, "stale_status": 409},
            "restart": {"same_view": True, "finished_after": True},
            "forbidden_hits": [], "responses_checked": 50, "json_checked": 40, "html_checked": 10}


class JsonKeysTest(unittest.TestCase):
    def test_nested_keys(self):
        self.assertEqual(set(json_keys({"a": [{"b": 1}, {"c": {"d": 2}}]})), {"a", "b", "c", "d"})


class ForbiddenHitsTest(unittest.TestCase):
    def test_json_checks_keys_not_values(self):
        self.assertEqual(forbidden_hits({"view": {"npc_states": {}}}), ["npc_states"])
        self.assertEqual(forbidden_hits({"text": "beliefs"}), [])

    def test_html_checks_whole_body(self):
        self.assertEqual(forbidden_hits("<div data-x='private'>"), ["private"])
        self.assertEqual(forbidden_hits("<p>아침</p>"), [])


class MorningMissingTest(unittest.TestCase):
    def test_all_present(self):
        html = ("<li>1일째 아침: 주모의 짚신이 우물가에서 발견됐다(한밤)</li>"
                "<li>2일째 아침: 과부의 짚신이 장터에서 발견됐다(새벽)</li>")
        self.assertEqual(morning_missing(html, [0, 1]), [])

    def test_missing_day(self):
        html = "<li>1일째 아침: 주모의 짚신이 우물가에서 발견됐다(한밤)</li>"
        self.assertEqual(morning_missing(html, [0, 1]), ["2일째 아침: …에서 발견됐다"])

    def test_place_sentence_checked_per_night(self):
        html = "<li>1일째 아침: 주모의 짚신이 우물가에서 발견됐다(한밤)</li><li>2일째 아침: 과부</li>"
        self.assertEqual(morning_missing(html, [0, 1]), ["2일째 아침: …에서 발견됐다"])

    def test_no_nights(self):
        self.assertEqual(morning_missing("", []), [])


class MetricsTest(unittest.TestCase):
    def test_metrics_pass_result_format(self):
        r = good_result()
        r["elapsed_s"] = 17.0
        metrics = metrics_of(r)
        build_result("INT-07", "web_run", metrics, "python -m tools.perf.int07_web_run", "abc", "2026-11-08T00:00:00Z")
        self.assertEqual((metrics["games_finished"]["value"], metrics["max_night_seen"]["value"],
                          metrics["stale_status"]["value"], metrics["forbidden_hits"]["value"]), (3, 1, 409, 0))


class SummarizeTest(unittest.TestCase):
    def verdicts(self, r):
        return {k: v[0] for k, v in summarize(r).items()}

    def test_all_good(self):
        out = self.verdicts(good_result())
        self.assertEqual(out, {"1": "자동 확인 통과", "3": "자동 확인 완료, 사람 브라우저 확인 대기",
                               "4": "자동 확인 완료, 사람 브라우저 확인 대기", "6": "자동 확인 통과"})

    def test_failures(self):
        r = good_result()
        r["duplicate"]["stale_status"] = 200
        r["forbidden_hits"] = [{"path": "/games/x", "keys": ["private"]}]
        r["games"][1]["morning_missing"] = ["2일째 아침: …에서 발견됐다"]
        out = self.verdicts(r)
        self.assertEqual((out["3"], out["4"], out["6"]), ("실패", "실패", "실패"))

    def test_unfinished_game_fails_items_1_and_6(self):
        r = good_result()
        r["games"][0]["winner"] = None
        out = self.verdicts(r)
        self.assertEqual((out["1"], out["6"]), ("실패", "실패"))

    def test_missing_game_fails_item6(self):
        r = good_result()
        r["games"] = r["games"][:2]
        self.assertEqual(self.verdicts(r)["6"], "실패")

    def test_recorded_errors_fail_item1(self):
        for change in ({"errors": ["render 500"]}, {"view_result_matches": False}):
            r = good_result()
            r["games"][2].update(change)
            self.assertEqual(self.verdicts(r)["1"], "실패", change)
        r = good_result()
        r["progress_has_games"] = False
        self.assertEqual(self.verdicts(r)["1"], "실패")

    def test_5xx_fails_item1(self):
        r = good_result()
        r["http_5xx"] = 1
        self.assertEqual(self.verdicts(r)["1"], "실패")

    def test_second_morning_required(self):
        r = good_result()
        for g in r["games"]:
            g["nights_seen"] = [0]
        self.assertEqual(self.verdicts(r)["3"], "실패")


if __name__ == "__main__":
    unittest.main()
