"""Fixed-version model lookup in a trusted local mlruns directory."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re

from engine.ports import FeatureVersionMismatch, IntentParser, ModelUnavailable, SuspicionModel

_VERSION = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def lookup_model(client, name, alias):
    """MLflow 별칭을 판에 고정할 레지스트리 버전·아티팩트 출처로 바꾼다."""
    if alias not in {"candidate", "canary", "production", "previous"}:
        raise ValueError("invalid_model_alias")
    result = client.get_model_version_by_alias(name, alias)
    return {"version": str(result.version), "source": result.source, "run_id": result.run_id}


def _close(model: SuspicionModel) -> None:
    close = getattr(model, "close", None)
    if close is not None:
        close()


class LocalModelSelector:
    """Resolve explicitly registered artifact versions with no canary assignment.

    Direct CLI callers own returned instances. Web processes lend these through
    SharedModelStore; games must not close its underlying adapters.
    The root must contain only trusted local pickle and joblib artifacts.
    """

    def __init__(self, root: Path | str = "mlruns", *, suspicion_version: str, intent_version: str,
                 feature_version: str, suspicion_loader: Callable[[Path], SuspicionModel] | None = None):
        """Configure the fixed pair and the feature version expected by inference."""
        self.root = Path(root).resolve()
        self.suspicion_version = suspicion_version
        self.intent_version = intent_version
        self.feature_version = feature_version
        self._suspicion_loader = suspicion_loader

    def _file(self, family: str, version: str, name: str) -> Path:
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ModelUnavailable(f"invalid {family} version: {version!r}")
        try:
            path = (self.root / family / version / name).resolve(strict=True)
            if path.is_relative_to(self.root) and path.is_file():
                return path
        except (OSError, RuntimeError) as exc:
            raise ModelUnavailable(f"missing {family}/{version}/{name}") from exc
        raise ModelUnavailable(f"invalid {family}/{version}/{name}")

    def select(self, game_id: str, player_pseudonym: str | None) -> tuple[dict[str, str], bool]:
        """Validate the configured pair and return its exact versions for a new game."""
        _close(self.suspicion_model(self.suspicion_version))
        self.intent_parser(self.intent_version)
        return {"suspicion": self.suspicion_version, "intent": self.intent_version}, False

    def suspicion_model(self, version: str) -> SuspicionModel:
        """Load only the requested model after checking its declared feature version."""
        report_path = self._file("suspicion-v0", version, "report.json")
        try:
            feature = json.loads(report_path.read_text(encoding="utf-8"))["feature_version"]
            if not isinstance(feature, str) or not feature:
                raise ValueError("missing feature_version")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ModelUnavailable(f"invalid suspicion report for {version}") from exc
        if feature != self.feature_version:
            raise FeatureVersionMismatch(f"suspicion {version}: expected {self.feature_version}, got {feature}")

        path = self._file("suspicion-v0", version, "models.pkl")
        try:
            loader = self._suspicion_loader
            if loader is None:
                from ml.adapters.suspicion_model import load_model

                loader = load_model
            model = loader(path)
        except (FeatureVersionMismatch, ModelUnavailable):
            raise
        except Exception as exc:
            raise ModelUnavailable(f"cannot load suspicion {version}") from exc
        if not isinstance(model, SuspicionModel) or getattr(model, "model_version", None) != version:
            _close(model)
            raise ModelUnavailable(f"loaded suspicion model does not match {version}")
        if model.feature_version != feature:
            _close(model)
            raise FeatureVersionMismatch(f"suspicion {version}: report {feature}, model {model.feature_version}")
        return model

    def intent_parser(self, version: str) -> IntentParser:
        """Wrap the exact saved TF-IDF baseline in the existing intent parser."""
        path = self._file("intent-v0", version, "model.joblib")
        try:
            from joblib import load
            from intent.baseline import IntentBaseline
            from intent.parser import BaselineIntentParser

            model = load(path)
        except Exception as exc:
            raise ModelUnavailable(f"cannot load intent {version}") from exc
        if not isinstance(model, IntentBaseline) or model.model_version != version:
            raise ModelUnavailable(f"loaded intent model does not match {version}")
        return BaselineIntentParser(model)
