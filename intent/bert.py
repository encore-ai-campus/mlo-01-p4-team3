"""Local D-07-03 checkpoint adapter for the confirmation-only IntentParser port."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from intent.finetune import BertClassifier, TOKENIZER_VERSION, _sha
from intent.parser import BaselineIntentParser
from tools.intent_examples import LABELS


def checkpoint_record(run_dir: Path | str) -> tuple[dict, list[str], str]:
    """Verify local checkpoint provenance and return labels/tokenizer identity without importing torch."""
    run_dir = Path(run_dir)
    try:
        record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        model_dir = run_dir / "model"
        weights_sha = _sha(model_dir / "model.safetensors")
        if (record["task"] != "D-07-03" or record["training_complete"] is not True or
                record["weights_sha256"] != weights_sha or
                record["model_version"] != "koelectra-intent-v0-" + weights_sha[:12] or
                record["feature_version"] != TOKENIZER_VERSION):
            raise ValueError("checkpoint provenance mismatch")
        length, threshold = record["length"], record["threshold"]
        if (type(length) is not int or not 8 <= length <= 512 or
                not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1):
            raise ValueError("invalid checkpoint length/threshold")
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        labels = [config["id2label"][str(i)] for i in range(len(LABELS))]
        if (labels != sorted(LABELS) or len(config["id2label"]) != len(LABELS) or
                config["label2id"] != {v: k for k, v in enumerate(labels)}):
            raise ValueError("checkpoint label mapping mismatch")
        digest = hashlib.sha256()
        for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json", "vocab.txt"):
            if name not in ("tokenizer.json", "tokenizer_config.json") and not (model_dir / name).exists():
                continue
            digest.update(name.encode())
            digest.update((model_dir / name).read_bytes())
        return record, labels, TOKENIZER_VERSION + "|sha256:" + digest.hexdigest()
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError(f"BERT checkpoint unavailable ({type(exc).__name__})") from None


def load_classifier(run_dir: Path | str) -> BertClassifier:
    """Load a trusted verified local safetensors run on CPU; never download or silently fall back."""
    record, labels, tokenizer_version = checkpoint_record(run_dir)
    model_dir = Path(run_dir) / "model"
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        model = AutoModelForSequenceClassification.from_pretrained(model_dir, local_files_only=True,
            trust_remote_code=False, weights_only=True, use_safetensors=True).to("cpu")
        tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
        classifier = BertClassifier(model, tokenizer, labels, record["model_version"], record["length"], record["threshold"])
        classifier.tokenizer_version = tokenizer_version
        return classifier
    except (OSError, ImportError, RuntimeError, KeyError, TypeError) as exc:
        # Do not echo third-party error text: it may contain input or local environment values.
        raise ValueError(f"BERT checkpoint unavailable ({type(exc).__name__})") from None


class BertIntentParser(BaselineIntentParser):
    """Reuse all public person/evidence/context matching with one fixed local BERT run."""

    def __init__(self, run_dir: Path | str):
        super().__init__(load_classifier(run_dir))
