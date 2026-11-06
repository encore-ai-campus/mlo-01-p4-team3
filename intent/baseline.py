"""TF-IDF/logistic-regression baseline and lineage-safe seed loading (D-05-01)."""
from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupShuffleSplit

from contracts.datasets import IntentExample, Split
from engine.domain.serde import from_jsonable
from intent.matcher import normalize_people

SEED_PATH = Path(__file__).resolve().parents[1] / "fixtures/intent/seed_examples.jsonl"
TOKENIZER_VERSION = "char-2-5-nfc-v1"


def load_examples(path: Path | str = SEED_PATH) -> list[IntentExample]:
    """Read JSONL using the shared strict dataset schema; reject duplicate IDs."""
    rows = [from_jsonable(IntentExample, json.loads(line))
            for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row.example_id for row in rows}) != len(rows):
        raise ValueError("duplicate example_id")
    return rows


def split_examples(rows: list[IntentExample], seed: int = 7) -> tuple[list[IntentExample], list[IntentExample]]:
    """Hold out whole connected lineages/contrast groups, retaining every label on both sides."""
    if any(row.split is Split.TEST_HUMAN for row in rows):
        raise ValueError("test_human must stay outside seed splitting")
    parent = {row.example_id: row.example_id for row in rows}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners = {}
    for row in rows:
        for key in (("lineage", row.split_key.lineage_root or row.example_id),
                    ("contrast", row.contrast_group)):
            if key[1] is None:
                continue
            if key in owners:
                parent[root(row.example_id)] = root(owners[key])
            else:
                owners[key] = row.example_id
    groups = [root(row.example_id) for row in rows]
    labels = {row.label for row in rows}
    if len(set(groups)) < 2 or len(labels) < 2:
        raise ValueError("not enough independent groups/classes")
    # Small seed sets can miss a class by chance. Choose the first valid fixed-seed partition.
    for train_idx, dev_idx in GroupShuffleSplit(n_splits=512, test_size=.3, random_state=seed).split(rows, groups=groups):
        train, dev = [rows[i] for i in train_idx], [rows[i] for i in dev_idx]
        if {r.label for r in train} == {r.label for r in dev} == labels:
            return train, dev
    raise ValueError("cannot hold out all labels without splitting a lineage")


class IntentBaseline:
    """Character TF-IDF + logistic regression; low-confidence/OOV text is unsupported."""

    def __init__(self, threshold: float = .20):
        if not 0 <= threshold <= 1:
            raise ValueError("threshold must be in [0, 1]")
        self.threshold = threshold
        self.vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 5), sublinear_tf=True)
        self.classifier = LogisticRegression(C=4.0, max_iter=1000, random_state=7)
        self.model_version = "unfitted"

    def fit(self, rows: list[IntentExample]) -> IntentBaseline:
        """Fit only supplied training rows; never accept fixed human-test data."""
        if not rows or len({r.label for r in rows}) < 2:
            raise ValueError("training requires at least two classes")
        if any(r.split in (Split.TEST_HUMAN, Split.DEV) for r in rows):
            raise ValueError("evaluation rows cannot be fitted")
        texts = [normalize_people(r.text, {}) for r in rows]
        self.classifier.fit(self.vectorizer.fit_transform(texts), [r.label for r in rows])
        source = json.dumps([(r.example_id, t, r.label) for r, t in zip(rows, texts)], ensure_ascii=False)
        config = f"{TOKENIZER_VERSION}|C=4|max_iter=1000|seed=7|sklearn={version('scikit-learn')}"
        self.model_version = "tfidf-lr-v0-" + hashlib.sha256((config + source).encode()).hexdigest()[:12]
        return self

    def save(self, path: Path | str) -> None:
        """Save this locally trained model; load artifacts only from a trusted local run."""
        import joblib
        joblib.dump(self, path)

    def probabilities(self, text: str) -> dict[str, float]:
        """Return class probabilities; OOV input has no supported model evidence."""
        if self.model_version == "unfitted":
            raise ValueError("model is not fitted")
        x = self.vectorizer.transform([normalize_people(text, {})])
        if x.nnz == 0:
            return {}
        probabilities = self.classifier.predict_proba(x)[0]
        return {str(label): float(score) for label, score in zip(self.classifier.classes_, probabilities)}

    def predict(self, text: str) -> tuple[str, float]:
        """Return one label and its LR probability; unseen input is unsupported with score zero."""
        probabilities = self.probabilities(text)
        if not probabilities:
            return "unsupported", 0.0
        label = max(probabilities, key=probabilities.get)
        score = probabilities[label]
        return (label if score >= self.threshold else "unsupported"), score
