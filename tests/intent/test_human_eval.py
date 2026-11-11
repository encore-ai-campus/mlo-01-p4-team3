"""D-09-01: frozen-human gating, leakage rejection and honest evaluation."""
import unittest
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from contracts.datasets import LabelSource, Split
from intent.baseline import load_examples
from intent.evaluation import evaluate
from intent.parser import BaselineIntentParser
from engine.ports import ParseFailure, ParseResult


class HumanEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_examples()

    def test_split_categories_use_annotation_and_gold_not_predicted_candidates(self):
        from intent.human_eval import category_scores
        rows = [replace(self.rows[0], text="그는 수상하지 않다", notes="type=pronoun",
                        form="assertion", contrast_group="pair"),
                replace(self.rows[1], text="훈장은 수상한가?", notes="type=alias",
                        form="question", contrast_group="pair"),
                replace(self.rows[2], text="수상하다", notes="type=ellipsis", contrast_group=None)]
        cases = [{"expected": [{}], "exact": True},
                 {"expected": [{}, {}], "exact": False},
                 {"expected": [{}], "exact": True}]
        scores = category_scores(rows, cases)
        self.assertEqual(scores["question_assertion"], {"rows": 2, "exact_match": .5})
        self.assertEqual(scores["multi_intent"], {"rows": 1, "exact_match": 0.})
        self.assertEqual(scores["negation"], {"rows": 1, "exact_match": 1.})
        self.assertEqual(scores["pronoun"]["rows"], 1)
        self.assertEqual(scores["ellipsis"]["rows"], 1)
        self.assertEqual(scores["alias"]["rows"], 1)
        self.assertIsNone(category_scores(rows[:1], cases[:1])["multi_intent"]["exact_match"])

    def test_pm_smoke_requires_five_pm_rows_matching_annotations(self):
        from intent.human_eval import validate_pm_smoke
        with tempfile.TemporaryDirectory() as path:
            rows = [replace(r, split_key=replace(r.split_key, user="pm", game_id="pm-game"))
                    for r in self.rows[:5]]
            raw = [{"event_type": "intent_attempt", "event_id": r.example_id, "player_pseudonym": "pm",
                    "payload": {"attempt_id": r.example_id, "text": r.text}} for r in rows]
            source = Path(path) / "games.json"
            def write(items):
                source.write_text(json.dumps({"games": [{"game_id": "pm-game", "source": "human",
                                                        "events": items}]}), encoding="utf-8")
            write(raw)
            validate_pm_smoke(source, rows)
            for invalid in (raw[:4], [*raw[:4], {**raw[-1], "player_pseudonym": "other"}],
                            [*raw[:4], {**raw[-1], "payload": {"attempt_id": rows[-1].example_id,
                                                              "text": "다른 문장"}}]):
                write(invalid)
                with self.assertRaises(ValueError):
                    validate_pm_smoke(source, rows)

    def test_bootstrap_overlap_and_80_percent_boundary_cannot_pass_as_improvement(self):
        from intent.human_eval import decision
        self.assertFalse(decision(.8, .7, [.70, .90], [.60, .80], 100, human=True)["passed"])
        self.assertTrue(decision(.8, .5, [.75, .85], [.4, .6], 100, human=True)["passed"])
        self.assertFalse(decision(.79, .5, [.75, .85], [.4, .6], 100, human=True)["passed"])
        self.assertFalse(decision(.9, .5, [.8, 1.], [.4, .6], 100, human=False)["passed"])
        self.assertFalse(decision(.8, .8, None, None, 200, human=True)["passed"])
        self.assertTrue(decision(.81, .8, None, None, 200, human=True)["passed"])

    def test_frozen_human_requires_annotation_user_and_no_training_overlap(self):
        from intent.human_eval import validate_human
        row = replace(self.rows[0], example_id="human1", text="새로운 독립 문장", split=Split.TEST_HUMAN,
            label_source=LabelSource.HUMAN_ANNOTATION,
            split_key=replace(self.rows[0].split_key, user="user-eval", game_id="human-game", lineage_root="human1"),
            contrast_group=None)
        validate_human([row], self.rows)
        for change in ({"split": Split.DEV}, {"label_source": LabelSource.AUTHORED},
                       {"split_key": replace(row.split_key, user=None)}, {"text": self.rows[0].text},
                       {"split_key": replace(row.split_key, lineage_root=self.rows[0].split_key.lineage_root)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_human([replace(row, **change)], self.rows)
        training = [replace(self.rows[0], split_key=replace(self.rows[0].split_key, user="user-eval"))]
        with self.assertRaises(ValueError):
            validate_human([row], training)

    def test_bootstrap_resamples_users_as_groups(self):
        from intent.human_eval import bootstrap_interval
        rows = [replace(r, example_id=f"r{i}", contrast_group=None,
                        split_key=replace(r.split_key, user="one-user", lineage_root=f"r{i}"))
                for i, r in enumerate(self.rows[:4])]
        self.assertEqual(bootstrap_interval([True, True, False, False], rows, seed=7), [.5, .5])

    def test_failure_without_candidates_is_not_correct_unsupported_detection(self):
        class BrokenParser:
            def parse(self, text, ctx):
                return ParseResult([], ParseFailure.UNPARSEABLE, True, {})
        report = evaluate(BrokenParser(), [r for r in self.rows if r.label == "unsupported"])
        self.assertEqual(report["metrics"]["unsupported_recall"]["value"], 0.)

    def test_counterexamples_cover_six_categories_and_compare_all_candidates(self):
        from intent.human_eval import evaluate_counterexamples
        report = evaluate_counterexamples(BaselineIntentParser())
        self.assertEqual(set(report["categories"]),
                         {"negation", "pronoun", "ellipsis", "multi_intent", "question_assertion", "alias"})
        self.assertTrue(all(g["rows"] >= 2 for g in report["categories"].values()))
        self.assertEqual(report["confirmation_violations"], 0)
        multi = [c for c in report["cases"] if c["category"] == "multi_intent"]
        self.assertTrue(all(len(c["expected"]) == 2 for c in multi))

    def test_full_command_evaluation_uses_supplied_public_context_and_all_gold_commands(self):
        from engine.domain.model import Command, CommandKind
        from intent.evaluation import reference_case
        row = replace(self.rows[0], text="수상하다; 머슴은 결백해")
        _, ctx, _ = reference_case(row)
        ctx.selected_npc = "jumo"
        gold = [Command(CommandKind.DEFEND, "mudang", target="meosum"),
                Command(CommandKind.SUSPECT, "mudang", target="jumo")]
        parser = BaselineIntentParser()
        case = lambda _: (row.text, ctx, gold)
        self.assertTrue(evaluate(parser, [row], case=case)["cases"][0]["exact"])
        self.assertFalse(evaluate(parser, [row], case=lambda _: (row.text, ctx, gold[1:]))["cases"][0]["exact"])
        from intent.human_eval import summarize
        report = summarize(parser, [row], calls=1, seed=7, case=case)
        self.assertEqual(report["metrics"]["command_exact_match"]["value"], 1.)
        self.assertEqual(report["metrics"]["parse_calls"]["value"], 1)

    def test_official_freeze_manifest_binds_human_rows_and_rejects_changed_files(self):
        from intent.human_eval import validate_manifest
        from tools.ut_freeze import freeze_manifest
        with tempfile.TemporaryDirectory() as path:
            folder = Path(path)
            row = replace(self.rows[0], example_id="attempt-human", text="독립 사람 문장",
                split=Split.TEST_HUMAN, label_source=LabelSource.HUMAN_ANNOTATION,
                split_key=replace(self.rows[0].split_key, user="eval-user", game_id="g-human"))
            raw = {"attempt_id": row.example_id, "text": row.text, "player_pseudonym": "eval-user",
                   "game_id": "g-human", "pm": True}
            files = {"test_human.jsonl": json.dumps(raw), "train_candidates.jsonl": "",
                     "assignment.json": json.dumps({"eval_users": ["eval-user"], "train_users": ["train-user"]}),
                     "report.json": json.dumps({"ok": True, "user_overlap": 0, "seed": 7,
                         "privacy_excluded": 0, "sentence_conflicts": 0, "conflicts_dropped": 0})}
            for name, content in files.items():
                (folder / name).write_text(content, encoding="utf-8")
            manifest = folder / "manifest.json"
            manifest.write_text(json.dumps(freeze_manifest(folder, files)), encoding="utf-8")
            self.assertEqual(validate_manifest(manifest, [row])["pm_users"], ["eval-user"])
            with self.assertRaises(ValueError):
                validate_manifest(manifest, [replace(row, text="변경된 사람 문장")])
            with self.assertRaises(ValueError):
                validate_manifest(manifest, [replace(row, split_key=replace(row.split_key, user="train-user"))])
            (folder / "train_candidates.jsonl").write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_manifest(manifest, [row])
