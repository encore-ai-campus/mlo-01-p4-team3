"""사람 판을 학습·시험에 쓰는 경계(D-10-01): 변환, 동의·삭제·부하 시험 제외, 사람 단위 분할."""
import copy
import json
import random
import tempfile
import unittest
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from engine.domain import serde
from engine.testing.fakes import ListEventSink
from ml.suspicion import evaluate as ev
from ml.suspicion import features as ft
from ml.suspicion import retrain
from server.infra.ut_export import TRAINING_DROP_FIELDS, TRAINING_EVENT_TYPES
from tools.privacy_delete import FIELDS
from tools.run_engine_games import run_games

USERS = ("p-a", "p-b", "p-c")


def training_games(events):
    """봇 판 이벤트를 학습용 내보내기(ut_export training)와 같은 모양의 사람 판으로 바꾼다."""
    by_game = defaultdict(list)
    for raw in events:
        by_game[raw["game_id"]].append(raw)
    games = []
    for i, (gid, es) in enumerate(sorted(by_game.items())):
        user = USERS[i % len(USERS)]
        kept = [{k: v for k, v in dict(e, source="human", player_pseudonym=user).items() if k not in TRAINING_DROP_FIELDS}
                for e in es if e["event_type"] in TRAINING_EVENT_TYPES]
        games.append({"game_id": gid, "player_pseudonym": user, "events": kept})
    return games


class HumanRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sink = ListEventSink()
        run_games("base6", 6, 93, "rule-v0", sink)
        cls.games = training_games([serde.to_jsonable(e) for e in sink.items])

    def test_converts_consented_games_with_pseudonym_split_key(self):
        rows, audit = ft.human_rows(copy.deepcopy(self.games), set(USERS), set())
        self.assertEqual(audit["used_games"], len(self.games))
        self.assertEqual(audit["excluded"], {})
        self.assertTrue(rows)
        self.assertEqual({r.source for r in rows}, {"human"})
        self.assertEqual({r.split_key.user for r in rows}, set(USERS))
        self.assertEqual(set(audit["results"]), {g["game_id"] for g in self.games})
        self.assertTrue(set(audit["results"].values()) <= {"village", "changgwi"})

    def test_same_features_as_bot_conversion(self):
        """사람 판 변환은 봇 판 검사·특징 추출과 같다(출처·가명만 다름)."""
        sink = ListEventSink()
        run_games("base6", 6, 93, "rule-v0", sink)
        bot, _ = ft.engine_rows([serde.to_jsonable(e) for e in sink.items])
        human, _ = ft.human_rows(copy.deepcopy(self.games), set(USERS), set())
        strip = lambda rs: sorted((r.row_id, r.label, tuple(sorted(r.features.items()))) for r in rs)
        self.assertEqual(strip(bot), strip(human))

    def test_consent_deletion_and_load_test_are_excluded(self):
        games = copy.deepcopy(self.games)
        for e in games[1]["events"]:
            e["load_test"] = True
        rows, audit = ft.human_rows(games, {"p-a", "p-b"}, {"p-b"})
        users = {r.split_key.user for r in rows}
        self.assertEqual(users, {"p-a"})
        self.assertEqual(audit["excluded"].get("deletion_request"), 2)
        self.assertEqual(audit["excluded"].get("no_consent"), 2)
        self.assertNotIn("p-b", json.dumps(audit))

    def test_broken_game_is_skipped_not_fatal(self):
        games = copy.deepcopy(self.games)
        truth = next(e for e in games[0]["events"] if e["event_type"] == "truth_revealed")
        truth["seq"] = 0  # 진실이 종료 앞에 온 판
        games[2]["events"] = [e for e in games[2]["events"] if e["event_type"] != "truth_revealed"]
        rows, audit = ft.human_rows(games, set(USERS), set())
        self.assertEqual(audit["excluded"], {"invalid:ValueError:판별 seq 중복·역순": 1, "incomplete": 1})
        self.assertEqual(audit["used_games"], len(games) - 2)
        self.assertTrue(rows)

    def test_web_games_without_envelope_policy_versions_are_inferred(self):
        """운영 웹 판은 봉투 npc_policy_versions가 비어 있다: 결정의 정책 버전과 진실 명단으로 채워 같은 행을 만든다."""
        games = copy.deepcopy(self.games)
        expected, _ = ft.human_rows(copy.deepcopy(self.games), set(USERS), set())
        for g in games:
            for e in g["events"]:
                e["npc_policy_versions"] = {}
        rows, audit = ft.human_rows(games, set(USERS), set())
        self.assertEqual(audit["used_games"], len(games))
        self.assertEqual(audit["policy_versions_inferred"], len(games))
        strip = lambda rs: sorted((r.row_id, r.label, tuple(sorted(r.features.items()))) for r in rs)
        self.assertEqual(strip(rows), strip(expected))

    def test_web_game_with_mixed_decision_versions_is_rejected(self):
        games = copy.deepcopy(self.games[:1])
        decisions = [e for e in games[0]["events"] if e["event_type"] == "decision"]
        for e in games[0]["events"]:
            e["npc_policy_versions"] = {}
        decisions[0]["payload"]["policy_version"] = "rule-other"
        rows, audit = ft.human_rows(games, set(USERS), set())
        self.assertEqual(rows, [])
        self.assertEqual(audit["excluded"], {"invalid:ValueError:판 안 정책 버전 혼합": 1})

    def test_parse_error_reason_does_not_copy_input_values(self):
        games = copy.deepcopy(self.games[:1])
        games[0]["events"][0]["ts"] = "not-a-time-secret-1234"
        rows, audit = ft.human_rows(games, set(USERS), set())
        self.assertEqual(rows, [])
        self.assertEqual(list(audit["excluded"]), ["invalid:SchemaError"])

    def test_no_games_gives_no_rows(self):
        self.assertEqual(ft.human_rows([], set(USERS), set())[0], [])

    def test_validate_rows_requires_pseudonym_only_for_human(self):
        rows, _ = ft.human_rows(copy.deepcopy(self.games[:1]), set(USERS), set())
        with self.assertRaises(ValueError):
            ft.validate_rows([replace(r, split_key=replace(r.split_key, user=None)) for r in rows])
        with self.assertRaises(ValueError):
            ft.validate_rows([replace(r, source="bot") for r in rows])
        with self.assertRaises(ValueError):
            ft.validate_rows([replace(r, source="synthetic") for r in rows])


class HumanSplit(unittest.TestCase):
    def test_side_is_deterministic_and_about_thirty_percent(self):
        users = [f"user-{i}" for i in range(4000)]
        sides = [ev.human_side(u) for u in users]
        self.assertEqual(sides, [ev.human_side(u) for u in users])
        self.assertTrue(.27 < sides.count("test") / len(users) < .33)

    def test_new_people_do_not_move_existing_people(self):
        before = {u: ev.human_side(u) for u in ("a", "b", "c")}
        for u in (f"new-{i}" for i in range(50)):
            ev.human_side(u)
        self.assertEqual(before, {u: ev.human_side(u) for u in before})

    def test_one_person_is_never_on_both_sides(self):
        sink = ListEventSink()
        run_games("base6", 6, 93, "rule-v0", sink)
        rows, _ = ft.human_rows(training_games([serde.to_jsonable(e) for e in sink.items]), set(USERS), set())
        fit, test = ev.split_human_rows(rows)
        self.assertEqual(len(fit) + len(test), len(rows))
        self.assertFalse({r.split_key.user for r in fit} & {r.split_key.user for r in test})
        with self.assertRaises(ValueError):
            ev.split_human_rows([replace(rows[0], source="bot")])

    def test_training_split_keeps_each_person_on_one_side(self):
        """학습 안쪽 70/15/15 분할도 사람 단위: 한 사람의 판은 train·dev·test 중 한 곳에만(Codex P2)."""
        sink = ListEventSink()
        run_games("base6", 12, 97, "rule-v0", sink)
        events = [serde.to_jsonable(e) for e in sink.items]
        bot, _ = ft.engine_rows(events)
        human, _ = ft.human_rows(training_games(events), set(USERS), set())
        human = [replace(r, game_id="h-" + r.game_id, row_id="h-" + r.row_id,
                         split_key=replace(r.split_key, game_id="h-" + r.game_id)) for r in human]
        split = ev.split_games(bot + human, 7)
        for user in USERS:
            sides = {split[r.game_id] for r in human if r.split_key.user == user}
            self.assertEqual(len(sides), 1, user)
        # 봇 판만 있으면 예전 게임 단위 분할과 같다
        games = sorted({r.game_id for r in bot})
        shuffled = list(games)
        random.Random(7).shuffle(shuffled)
        n = max(1, int(len(games) * .15))
        legacy = {g: "test" if i < n else "dev" if i < 2 * n else "train" for i, g in enumerate(shuffled)}
        self.assertEqual(ev.split_games(bot, 7), legacy)

    def test_invalid_input(self):
        for bad in ("", None):
            with self.assertRaises(ValueError):
                ev.human_side(bad)


class LoadHuman(unittest.TestCase):
    def test_load_human_applies_lists_and_records_counts(self):
        sink = ListEventSink()
        run_games("base6", 6, 93, "rule-v0", sink)
        games = training_games([serde.to_jsonable(e) for e in sink.items])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "training.json").write_text(json.dumps({"kind": "training", "games": games, "excluded": {"load_test": 4}}),
                                                encoding="utf-8")
            (root / "consent.json").write_text(json.dumps(list(USERS)), encoding="utf-8")
            (root / "tomb").mkdir()
            (root / "tomb" / "request-1.json").write_text(
                json.dumps({f: (["p-c"] if f == "players" else []) for f in FIELDS}), encoding="utf-8")
            human = retrain.load_human(root / "training.json", root / "consent.json", root / "tomb")
            rec = human["record"]
            self.assertEqual(rec["export_excluded"], {"load_test": 4})
            self.assertEqual(rec["excluded"], {"deletion_request": 2})
            self.assertEqual(rec["train"]["games"] + rec["test"]["games"], 4)
            self.assertNotIn("p-c", json.dumps(rec))
            self.assertNotIn("p-c", {r.split_key.user for r in human["train"] + human["test"]})
            (root / "games.json").write_text(json.dumps({"kind": "games", "games": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                retrain.load_human(root / "games.json", root / "consent.json", root / "tomb")

    def test_artifact_scores_alpha_zero_is_rule_score(self):
        sink = ListEventSink()
        run_games("base6", 3, 95, "rule-v0", sink)
        rows, _ = ft.engine_rows([serde.to_jsonable(e) for e in sink.items])

        class Linear:
            def decision_function(self, x):
                return [sum(v) for v in x]

        artifact = {"feature_version": ft.FEATURE_VERSION, "feature_names": list(ft.FEATURE_NAMES),
                    "models": {"logistic": Linear()}, "temperatures": {"logistic": 2.0}}
        self.assertEqual(retrain.artifact_scores(artifact, rows, 0.0), [r.features["rule_score"] for r in rows])
        full = retrain.artifact_scores(artifact, rows)  # blend_alpha 없음 → α=1, 모델 로짓/온도
        self.assertAlmostEqual(full[0], sum(rows[0].features[n] for n in ft.FEATURE_NAMES) / 2.0)
        with self.assertRaises(ValueError):
            retrain.artifact_scores(dict(artifact, feature_version="old"), rows)


if __name__ == "__main__":
    unittest.main()
