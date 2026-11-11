"""Fixed-version model lookup in a trusted local mlruns directory, and the per-process model store.

계층: 인프라. 게임 웹 프로세스별 의심 모델 보관소(`ProcessSuspicionModels`, B-09-01 1번)도 여기 둔다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading
import time

from engine.ports import FeatureVersionMismatch, IntentParser, ModelUnavailable, SuspicionModel
from tools.jsonlog import log

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


_PERMANENT_PREFIXES = ("missing ", "invalid ")


def _permanent(exc: Exception | None) -> bool:
    """버전을 영구히 띄울 수 없는 실패인가(파일 없음·잘못된 버전·특징 버전 불일치)."""
    if isinstance(exc, FeatureVersionMismatch):
        return True
    return isinstance(exc, ModelUnavailable) and str(exc).startswith(_PERMANENT_PREFIXES)


@dataclass
class _VersionEntry:
    model: SuspicionModel | None = None
    games: set = field(default_factory=set)
    permanent: bool = False
    failures: int = 0
    retry_at: float = 0.0
    loading: bool = False


class _GameModel:
    """한 판이 빌려 쓰는 의심 모델. 판은 worker를 닫지 않고, 고정 버전 외 다른 버전으로 바꾸지 않는다."""

    def __init__(self, store: "ProcessSuspicionModels", game_id: str, version: str):
        self.model_version = version
        self.feature_version = store.feature_version
        self._store, self._game_id = store, game_id

    def estimate(self, obs):
        """보관소의 같은 버전 모델로 추론한다. 준비 전·영구 불가·재기동 중이면 `ModelUnavailable`로 규칙 v0 폴백."""
        return self._store._estimate(self._game_id, self.model_version, obs)

    def close(self):
        """판 종료는 공유 worker를 닫지 않는다(닫기는 보관소가 판 수를 세어 정한다)."""


class ProcessSuspicionModels:
    """게임 웹 프로세스 하나가 소유하는 의심 모델 보관소(B-09-01 1번, README 7절 "의심 모델 추론 장애 시 동작").

    - 모델 버전마다 모델(D-08-01 어댑터면 worker 하나)을 하나만 두고, 그 버전으로 시작한 판들이 빌려 쓴다.
    - 판이 추론하면 그 버전의 사용 판으로 센다. `release(game_id)`로 판이 끝나면 빼고, 쓰는 판이 없고
      새 판에도 배정하지 않는(`active_versions`에 없는) 버전만 닫는다. `close()`는 프로세스 종료 때 모두 닫는다.
    - 영구 불가(파일 없음·잘못된 버전·특징 버전 불일치) 버전은 그 버전으로 시작한 판이 끝날 때까지 폴백한다.
      일시적 로드 실패는 연속 실패를 세어 3회 미만이면 다음 추론 때, 3회부터는 30초 뒤 다시 띄우며
      그동안만 폴백한다. 로드된 뒤 timeout·worker 죽음은 어댑터(D-08-01 3번)가 같은 버전으로 재기동한다.
    """

    COOLDOWN_S = 30
    MAX_FAILURES = 3

    def __init__(self, loader: Callable[[str], SuspicionModel], active_versions: Callable[[], set] = set,
                 *, feature_version: str = "unavailable", clock: Callable[[], float] = time.monotonic,
                 background: bool = True):
        """`loader(version)`은 그 버전 모델을 만든다(실제 모델은 `LocalModelSelector.suspicion_model`)."""
        self._loader = loader
        self._active_versions = active_versions
        self.feature_version = feature_version
        self._clock = clock
        self._background = background
        self._entries: dict[str, _VersionEntry] = {}
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self, version: str):
        """판 ID 없이 버전만 아는 호출자용(사용 판 수에 넣지 않음)."""
        return self.for_game("", version)

    def preload(self, version: str) -> bool:
        """프로세스 시작 때 운영 버전을 미리 띄운다(동기). 성공하면 True."""
        with self._lock:
            if self._closed:
                return False
            entry = self._entries.setdefault(version, _VersionEntry())
            if entry.model is not None or entry.permanent or entry.loading:
                return entry.model is not None
            entry.loading = True
        self._load(version)
        with self._lock:
            entry = self._entries.get(version)
            return entry is not None and entry.model is not None

    def for_game(self, game_id: str, version: str) -> _GameModel:
        """판 메타에 고정한 버전의 빌린 모델. 처음 보는 버전이면 로드를 시작한다."""
        self._ensure(version)
        return _GameModel(self, game_id, version)

    def _ensure(self, version: str) -> None:
        with self._lock:
            if self._closed:
                return
            entry = self._entries.setdefault(version, _VersionEntry())
            if entry.model is not None or entry.permanent or entry.loading or self._clock() < entry.retry_at:
                return
            entry.loading = True
        if self._background:
            threading.Thread(target=self._load, args=(version,), daemon=True).start()
        else:
            self._load(version)

    def _load(self, version: str) -> None:
        try:
            model, error = self._loader(version), None
        except Exception as exc:  # noqa: BLE001 - 실패 종류만 가려 폴백·재시도를 정한다
            model, error = None, exc
        stale = None
        with self._lock:
            # 결과 반영과 loading 해제를 한 잠금 안에서 해 같은 버전 worker가 둘 생기지 않게 한다.
            entry = None if self._closed else self._entries.get(version)
            if entry is None:
                stale, model = model, None
            else:
                entry.loading = False
                if model is not None:
                    entry.model, entry.failures, entry.retry_at = model, 0, 0.0
                elif _permanent(error):
                    entry.permanent = True
                else:
                    entry.failures += 1
                    entry.retry_at = self._clock() + (self.COOLDOWN_S if entry.failures >= self.MAX_FAILURES else 0.0)
            failures = entry.failures if entry is not None else 0
            permanent = entry is not None and entry.permanent
        if stale is not None:
            _close(stale)
        if entry is None or model is not None:
            return
        if permanent:
            log("model_store", "warning", "의심 모델 버전 영구 사용 불가; 그 버전 판은 끝까지 규칙 v0",
                model_version=version, error_type=type(error).__name__)
        else:
            log("model_store", "warning", "의심 모델 로드 실패; 다시 띄울 때까지 규칙 v0",
                model_version=version, failures=failures, cooldown=failures >= self.MAX_FAILURES,
                error_type=type(error).__name__)

    def _estimate(self, game_id: str, version: str, obs):
        with self._lock:
            entry = None if self._closed else self._entries.get(version)
            if entry is not None and game_id:
                entry.games.add(game_id)
            model = None if entry is None else entry.model
            permanent = entry is not None and entry.permanent
        if model is None:
            if not permanent:
                self._ensure(version)
            raise ModelUnavailable(f"suspicion_model_unavailable:{version}")
        return model.estimate(obs)

    def release(self, game_id: str) -> None:
        """판 종료. 그 판을 사용 판에서 빼고, 쓰는 판이 없고 새 판에도 배정하지 않는 버전을 닫는다."""
        active = set(self._active_versions())
        to_close = []
        with self._lock:
            for version, entry in list(self._entries.items()):
                entry.games.discard(game_id)
                if not entry.games and version not in active and not entry.loading:
                    if entry.model is not None:
                        to_close.append(entry.model)
                    del self._entries[version]
        for model in to_close:
            _close(model)

    def close(self) -> None:
        """프로세스 종료 때 모든 버전 모델을 닫는다. 여러 번 불러도 안전하다."""
        with self._lock:
            self._closed = True
            models = [e.model for e in self._entries.values() if e.model is not None]
            self._entries.clear()
        for model in models:
            _close(model)

    def in_use(self) -> dict[str, set]:
        """시험·계측용: 버전별 사용 판 ID."""
        with self._lock:
            return {version: set(entry.games) for version, entry in self._entries.items()}
