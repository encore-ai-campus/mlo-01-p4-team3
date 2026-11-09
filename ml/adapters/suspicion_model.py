"""SuspicionModel 경계. 로컬 D-07-02 logistic 산출물을 제한 시간 안에서 호출한다.

Windows에서도 멈춘 호출을 중단하려고 모델은 같은 Python 환경의 지속 worker에 둔다.
D-07-02 특징/학습 코드는 지연 import하며, 신뢰된 로컬 models.pkl만 로드한다.
"""
from __future__ import annotations

from functools import partial
import hashlib
import math
import multiprocessing
from pathlib import Path
import pickle
import threading
import time

from engine.domain.model import ObservationSnapshot
from engine.domain.npc.vote import valid_estimate
from engine.ports import FeatureVersionMismatch, ModelUnavailable, SuspicionEstimate
from tools.jsonlog import log


def _worker(connection, loader):
    try:
        model = loader()
        connection.send(('ready', (model.model_version, model.feature_version)))
        while True:
            snapshot = connection.recv()
            connection.send(('result', model.estimate(snapshot)))
    except FeatureVersionMismatch:
        connection.send(('version', None))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        connection.send(('unavailable', type(exc).__name__))
    finally:
        connection.close()


class SuspicionModelAdapter:
    """포트 구현. 로드 실패는 호출 때 ModelUnavailable, 버전 불일치는 즉시 거부한다.

    timeout 후 worker를 종료하고 이 인스턴스는 다시 로드하지 않는다. 판 중 모델 교체나
    자동 재시도는 하지 않는다. Selector는 판 종료 후 close()로 수명을 관리해야 한다.
    """

    def __init__(self, loader, expected_feature_version: str | None, *,
                 timeout_s: float = .09, load_timeout_s: float = 30):
        if not math.isfinite(timeout_s) or not 0 < timeout_s < .1:
            raise ValueError('추론 제한은 0초 초과 100ms 미만')
        if not math.isfinite(load_timeout_s) or load_timeout_s <= 0:
            raise ValueError('로드 제한은 양의 유한값')
        self.model_version = 'unavailable'
        self.feature_version = expected_feature_version or 'unavailable'
        self.timeout_s = timeout_s
        self.calls = self.successes = 0
        self.latencies_ms = []
        self._lock = threading.Lock()
        self._closed = False
        context = multiprocessing.get_context('spawn')
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker, args=(child, loader), daemon=True)
        try:
            self._process.start()
            child.close()
            status, value = self._receive(load_timeout_s)
            if status == 'version':
                raise FeatureVersionMismatch('artifact feature_version mismatch')
            if status != 'ready':
                raise ModelUnavailable(f'model load failed: {value}')
            self.model_version, self.feature_version = value
            if expected_feature_version is not None and self.feature_version != expected_feature_version:
                raise FeatureVersionMismatch('selected feature_version mismatch')
        except FeatureVersionMismatch:
            self.close()
            log('suspicion_model', 'warning', '특징 버전 불일치; 모델 사용 거부',
                error_type='FeatureVersionMismatch', model_version=self.model_version,
                feature_version=self.feature_version)
            raise
        except Exception as exc:
            self.close()
            log('suspicion_model', 'warning', '모델 로드 실패; 엔진 폴백 대기',
                error_type=type(exc).__name__, model_version=self.model_version)
        finally:
            child.close()

    def _receive(self, timeout_s):
        if not self._connection.poll(max(0, timeout_s)):
            raise ModelUnavailable('model timeout')
        return self._connection.recv()

    def estimate(self, obs: ObservationSnapshot) -> SuspicionEstimate:
        """추론·IPC·잠금 대기를 한 마감으로 제한하고 실패는 엔진 폴백 예외로 바꾼다."""
        started = time.monotonic()
        self.calls += 1
        if not self._lock.acquire(timeout=self.timeout_s):
            raise ModelUnavailable('model busy')
        try:
            if self._closed:
                raise ModelUnavailable('model unavailable or closed')
            self._connection.send(obs)
            status, result = self._receive(self.timeout_s - (time.monotonic() - started))
            if status == 'version':
                raise FeatureVersionMismatch('inference feature_version mismatch')
            if status != 'result':
                raise ModelUnavailable(f'model inference failed: {result}')
            if not valid_estimate(result, obs) or result.observer != obs.observer:
                raise ModelUnavailable('invalid model output')
            if result.feature_version != self.feature_version:
                raise FeatureVersionMismatch('output feature_version mismatch')
            if result.model_version != self.model_version:
                raise ModelUnavailable('output model_version mismatch')
            self.successes += 1
            return result
        except FeatureVersionMismatch:
            self.close()
            log('suspicion_model', 'warning', '특징 버전 불일치; 모델 사용 거부',
                error_type='FeatureVersionMismatch', model_version=self.model_version,
                feature_version=self.feature_version)
            raise
        except Exception as exc:
            self.close()
            log('suspicion_model', 'warning', '추론 실패; 엔진 규칙 v0 폴백',
                error_type=type(exc).__name__, reason=str(exc), model_version=self.model_version,
                game_id=obs.game_id, duration_ms=round((time.monotonic() - started) * 1000, 3))
            raise ModelUnavailable('suspicion inference unavailable') from exc
        finally:
            self.latencies_ms.append((time.monotonic() - started) * 1000)
            self._lock.release()

    def close(self) -> None:
        """worker를 종료한다. 여러 번 호출해도 안전하며 늦은 결과는 재사용하지 않는다."""
        self._closed = True
        if self._process.pid is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=.01)
        self._connection.close()


class _ArtifactModel:
    def __init__(self, path):
        # pickle은 코드 실행 형식이므로 운영 경로/외부 업로드에서 받지 않는다.
        data = Path(path).read_bytes()
        artifact = pickle.loads(data)
        from ml.suspicion.features import FEATURE_NAMES, FEATURE_VERSION, candidate_features
        from ml.suspicion.evaluate import softmax
        if artifact['feature_version'] != FEATURE_VERSION or tuple(artifact['feature_names']) != FEATURE_NAMES:
            raise FeatureVersionMismatch('D-07-02 feature contract mismatch')
        self.feature_version = FEATURE_VERSION
        self.model_version = 'suspicion-logistic-' + hashlib.sha256(data).hexdigest()[:12]
        self._features, self._names, self._softmax = candidate_features, FEATURE_NAMES, softmax
        self._model = artifact['models']['logistic']
        self._temperature = artifact['temperatures']['logistic']
        if not math.isfinite(self._temperature) or self._temperature <= 0:
            raise ValueError('invalid temperature')
        from threadpoolctl import threadpool_limits
        # 첫 native 라이브러리 탐색·초기화는 판 추론 마감이 아닌 로드 마감에 포함한다.
        self._thread_limits = threadpool_limits(limits=1)
        self._model.decision_function([[0.0] * len(self._names)])

    def estimate(self, obs):
        if obs.n_possessed_remaining != 1 or not obs.candidates:
            raise ModelUnavailable('D-07-02 supports one possessed candidate only')
        matrix = []
        for candidate in obs.candidates:
            features = self._features(obs, candidate)
            matrix.append([features[name] for name in self._names])
        scores = self._model.decision_function(matrix)
        probabilities = self._softmax([float(score) / self._temperature for score in scores])
        # shortcut: 학습 산출물에 근거별 기여가 없음, 설명 계약을 인계받으면 채운다.
        return SuspicionEstimate(obs.observer, list(obs.candidates), dict(zip(obs.candidates, probabilities)),
                                 {candidate: {} for candidate in obs.candidates},
                                 self.model_version, self.feature_version)


def load_model(path: Path) -> SuspicionModelAdapter:
    """D-07-02의 로컬 models.pkl을 읽는다. 특징 계약은 기존 features 모듈이 검증한다."""
    return SuspicionModelAdapter(partial(_ArtifactModel, path), None)
