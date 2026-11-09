"""D-09-01: local checkpoint identity and inference failure boundaries."""
import copy
import json
import os
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engine.ports import IntentParser, ParseFailure
from intent.finetune import BertClassifier, TOKENIZER_VERSION, _sha
from intent.parser import BaselineIntentParser
from tests.intent.test_parser import context


@unittest.skipUnless(os.environ.get("PALDO_BERT_TESTS") == "1", "enable PALDO_BERT_TESTS=1 for real torch integration")
class BertRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from transformers import ElectraConfig, ElectraForSequenceClassification, PreTrainedTokenizerFast
        from tools.intent_examples import LABELS
        cls.temp = tempfile.TemporaryDirectory()
        cls.checkpoint_dir = Path(cls.temp.name)
        cls.labels = sorted(LABELS)
        tokenizer = PreTrainedTokenizerFast(tokenizer_object=Tokenizer(WordLevel(
            {"[UNK]": 0, "[PAD]": 1}, unk_token="[UNK]")), unk_token="[UNK]", pad_token="[PAD]")
        model = ElectraForSequenceClassification(ElectraConfig(vocab_size=2, embedding_size=8,
            hidden_size=8, num_hidden_layers=1, num_attention_heads=2, intermediate_size=16,
            max_position_embeddings=64, id2label=dict(enumerate(cls.labels)),
            label2id={v: k for k, v in enumerate(cls.labels)}))
        with torch.no_grad():
            model.classifier.out_proj.weight.zero_()
            model.classifier.out_proj.bias.zero_()
            model.classifier.out_proj.bias[cls.labels.index("suspect")] = 20
        model.save_pretrained(cls.checkpoint_dir / "model", safe_serialization=True)
        tokenizer.save_pretrained(cls.checkpoint_dir / "model")
        cls.record = {"task": "D-07-03", "model_version": "koelectra-intent-v0-" +
            _sha(cls.checkpoint_dir / "model/model.safetensors")[:12],
            "weights_sha256": _sha(cls.checkpoint_dir / "model/model.safetensors"),
            "feature_version": TOKENIZER_VERSION, "length": 64, "threshold": .2, "training_complete": True}
        (cls.checkpoint_dir / "run.json").write_text(json.dumps(cls.record), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_checkpoint_parser_preserves_slots_context_versions_and_confirmation(self):
        from intent.bert import BertIntentParser
        parser = BertIntentParser(self.checkpoint_dir)
        ctx = context()
        before = copy.deepcopy(ctx)
        result = parser.parse("훈장이 수상하다", ctx)
        self.assertIsInstance(parser, IntentParser)
        self.assertEqual(ctx, before)
        self.assertEqual(result.candidates[0].command.target, "hunjang")
        self.assertEqual(result.versions["model"], self.record["model_version"])
        self.assertIn("sha256", result.versions["tokenizer"])
        self.assertEqual(len(result.versions), 5)
        self.assertTrue(result.needs_confirmation)

    def test_missing_or_mismatched_checkpoint_is_not_a_silent_baseline(self):
        from intent.bert import BertIntentParser
        with self.assertRaises(ValueError):
            BertIntentParser(self.checkpoint_dir / "absent")
        original = (self.checkpoint_dir / "run.json").read_text(encoding="utf-8")
        try:
            (self.checkpoint_dir / "run.json").write_text(json.dumps({**self.record, "weights_sha256": "0" * 64}))
            with self.assertRaises(ValueError):
                BertIntentParser(self.checkpoint_dir)
        finally:
            (self.checkpoint_dir / "run.json").write_text(original, encoding="utf-8")

    def test_bad_logits_and_runtime_errors_return_unparseable_without_text_logging(self):
        from intent.bert import BertIntentParser
        import torch
        parser = BertIntentParser(self.checkpoint_dir)
        with torch.no_grad():
            parser.model.model.classifier.out_proj.bias.fill_(float("nan"))
        with self.assertLogs("paldo.intent", level="WARNING") as logs:
            result = parser.parse("비공개 문장", context())
        self.assertEqual(result.failure, ParseFailure.UNPARSEABLE)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(result.candidates, [])
        self.assertNotIn("비공개", "".join(logs.output))
        class Broken:
            def __call__(self, **kwargs):
                raise RuntimeError("private error content")
        parser.model.model = Broken()
        with self.assertLogs("paldo.intent", level="WARNING"):
            result = parser.parse("훈장이 수상하다", context())
        self.assertEqual(result.failure, ParseFailure.UNPARSEABLE)

    def test_classifier_rejects_unknown_duplicate_or_missing_labels(self):
        class Model:
            def eval(self):
                return self
        for labels in (["suspect"], ["suspect"] * 10, [*self.labels[:-1], "vote"]):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                BertClassifier(Model(), None, labels, "test")


class BertBoundaryTests(unittest.TestCase):
    def test_missing_checkpoint_fails_before_optional_ml_imports(self):
        from intent.bert import BertIntentParser
        with tempfile.TemporaryDirectory() as path, self.assertRaises(ValueError):
            BertIntentParser(path)

    def test_weight_mismatch_rejected_before_loading_model(self):
        from intent.bert import BertIntentParser
        with tempfile.TemporaryDirectory() as path:
            run = Path(path)
            (run / "model").mkdir()
            (run / "model/model.safetensors").write_bytes(b"corrupt")
            record = {"task": "D-07-03", "training_complete": True, "weights_sha256": "0" * 64}
            (run / "run.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                BertIntentParser(run)

    def test_bad_model_outputs_return_confirmed_failure_without_input_logging(self):
        from tools.intent_examples import LABELS
        class Logits:
            shape = (1, 10)
            def softmax(self, axis):
                return [SimpleNamespace(tolist=lambda: [float("nan")] * 10)]
        class Model:
            def eval(self):
                return self
            def __call__(self, **kwargs):
                return SimpleNamespace(logits=Logits())
        model = BertClassifier(Model(), lambda *a, **k: {}, sorted(LABELS), "broken-model")
        parser = BaselineIntentParser(model)
        torch_boundary = SimpleNamespace(inference_mode=nullcontext)
        with patch.dict("sys.modules", {"torch": torch_boundary}):
            for shape in ((1, 10), (1, 9)):
                Logits.shape = shape
                with self.assertLogs("paldo.intent", level="WARNING") as logs:
                    result = parser.parse("비공개 문장", context())
                self.assertEqual(result.failure, ParseFailure.UNPARSEABLE)
                self.assertEqual(result.candidates, [])
                self.assertTrue(result.needs_confirmation)
                self.assertNotIn("비공개", "".join(logs.output))

    def test_invalid_label_vocabulary_rejected(self):
        from tools.intent_examples import LABELS
        for labels in (["suspect"], ["suspect"] * 10, [*sorted(LABELS)[:-1], "vote"]):
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                BertClassifier(None, None, labels, "test")
