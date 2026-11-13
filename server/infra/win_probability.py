"""D-10-03 공개 특징 로지스틱의 표준 라이브러리 추론과 holdout 지표."""
import hashlib
import json
import math
from pathlib import Path

from prometheus_client import Gauge

from engine.domain.review import public_win_features
from tools.jsonlog import log

LABELS = ("model_version", "scope", "rule_version", "content_version")
PREDICTED = Gauge("paldo_win_probability_predicted_mean", "Mean predicted village win probability", LABELS)
ACTUAL = Gauge("paldo_win_probability_actual_rate", "Observed village win rate on the same rows", LABELS)
GAMES = Gauge("paldo_win_probability_evaluation_games", "Held-out games behind win probability metrics", LABELS)


class WinProbabilityModel:
    """신뢰된 로컬 JSON의 유한 계수와 특징·규칙·콘텐츠·프리셋 버전을 검증한다."""

    def __init__(self, artifact, model_version):
        """학습과 같은 공개 특징만 읽으며 평가 지표는 offline_holdout으로 표시한다."""
        self.weights = artifact["coefficients"]
        self.intercept = artifact["intercept"]
        self.versions = artifact["versions"]
        evaluation = artifact["evaluation"]
        if artifact["format"] != "win-logistic-v1" or artifact["feature_version"] != "win-public-v1":
            raise ValueError("unsupported win probability model")
        if not isinstance(self.weights, dict) or not self.weights or not all(
                isinstance(key, str) for key in self.weights) or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                for value in [self.intercept, *self.weights.values()]):
            raise ValueError("invalid win probability coefficients")
        if not isinstance(self.versions, dict) or any(
                not isinstance(self.versions.get(key), list) or not self.versions[key]
                or not all(isinstance(value, str) and value for value in self.versions[key])
                for key in ("rule_version", "content_version", "preset_id")):
            raise ValueError("missing win probability provenance")
        if type(evaluation["test_games"]) is not int or evaluation["test_games"] <= 0 or any(
                type(evaluation["model"][key]) not in (int, float)
                or not math.isfinite(evaluation["model"][key]) or not 0 <= evaluation["model"][key] <= 1
                for key in ("predicted_mean", "actual_rate")):
            raise ValueError("invalid win probability evaluation")
        labels = (model_version, "offline_holdout", ",".join(self.versions["rule_version"]),
                  ",".join(self.versions["content_version"]))
        PREDICTED.labels(*labels).set(evaluation["model"]["predicted_mean"])
        ACTUAL.labels(*labels).set(evaluation["model"]["actual_rate"])
        GAMES.labels(*labels).set(evaluation["test_games"])

    @classmethod
    def from_path(cls, path):
        """JSON 오류·로드 실패는 예측 생략으로 처리하고 게임 조립을 계속한다."""
        try:
            raw = Path(path).read_bytes()
            return cls(json.loads(raw), "win-logistic-" + hashlib.sha256(raw).hexdigest()[:12])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log("win_probability", "warning", "복기 승률 모델 로드 실패; 예측 생략", error_type=type(exc).__name__)
            return None

    def __call__(self, inp, meta):
        """각 날짜의 최초 공개 관측을 추론한다. 버전 불일치·중단 판은 예측하지 않는다."""
        if (meta.rule_version not in self.versions["rule_version"]
                or meta.content_version not in self.versions["content_version"]
                or meta.preset.preset_id not in self.versions["preset_id"]):
            return None
        events = sorted(inp.events, key=lambda event: event.seq)
        end = next((event for event in events if event.event_type == "game_ended"), None)
        if end is None or not end.payload.get("completed", True) or end.payload.get("reason") == "aborted":
            return None
        days = {}
        try:
            for event in events:
                if event.seq >= end.seq:
                    break
                if event.event_type != "observation" or event.day in days:
                    continue
                features = public_win_features(event.day, meta.preset.preset_id,
                                               event.payload["snapshot"]["public"])
                score = self.intercept
                for key, value in features.items():
                    name = key + "=" + value if isinstance(value, str) else key
                    score += self.weights.get(name, 0) * (1 if isinstance(value, str) else value)
                if not math.isfinite(score):
                    raise ValueError("non-finite win probability score")
                probability = 1 / (1 + math.exp(-score)) if score >= 0 else math.exp(score) / (1 + math.exp(score))
                days[event.day] = probability
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            log("win_probability", "warning", "복기 공개 관측 불일치; 예측 생략", error_type=type(exc).__name__)
            return None
        return [dict(day=day, probability=probability) for day, probability in sorted(days.items())] or None
