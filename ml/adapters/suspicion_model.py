"""신뢰된 D-07-02 로컬 산출물의 직접 추론과 격리 worker 어댑터.

Windows에서도 멈춘 호출을 중단하려고 모델은 같은 Python 환경의 지속 worker에 둔다.
D-07-02 특징/학습 코드는 지연 import하며, 신뢰된 로컬 models.pkl만 로드한다.
"""
from __future__ import annotations

from functools import partial
from collections import deque
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
    request_id = None
    try:
        model = loader()
        connection.send(('ready', (model.model_version, model.feature_version)))
        while True:
            request_id, snapshot = connection.recv()
            try:
                connection.send((request_id, 'result', model.estimate(snapshot)))
            except FeatureVersionMismatch:
                connection.send((request_id, 'version', None))
    except FeatureVersionMismatch:
        connection.send(('version', None))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        error = ('unavailable', type(exc).__name__)
        connection.send(error if request_id is None else (request_id, *error))
    finally:
        connection.close()


class _RequestTimeout(ModelUnavailable):
    pass


def _validate_output(result, obs, feature_version, model_version):
    if not valid_estimate(result, obs) or result.observer != obs.observer:
        raise ModelUnavailable('invalid model output')
    if result.feature_version != feature_version:
        raise FeatureVersionMismatch('output feature_version mismatch')
    if result.model_version != model_version:
        raise ModelUnavailable('output model_version mismatch')


class SuspicionModelAdapter:
    """호출만 폴백하고 동일 버전 worker를 복구한다. close는 소유 보관소가 호출한다."""

    COOLDOWN_S = 30
    HARD_TIMEOUT_S = 1.5

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
        self.latencies_ms = deque(maxlen=10000)
        self.wait_observer = None
        self._lock = threading.Lock()
        self._closed = False
        self._loader = loader
        self._expected_feature = expected_feature_version
        self._load_timeout = load_timeout_s
        self._ready = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._request_id = 0
        self._stop = threading.Event()
        self._process = self._connection = None
        self._recovery_thread = None
        self._permanent = False
        self._failures = 0
        self._timeouts = []
        self.recovery_ms = deque(maxlen=10000)
        self._cooldown_until = 0
        try:
            self._start_worker()
        except FeatureVersionMismatch:
            self.close()
            log('suspicion_model', 'warning', '특징 버전 불일치; 모델 사용 거부',
                error_type='FeatureVersionMismatch', model_version=self.model_version,
                feature_version=self.feature_version)
            raise
        except Exception as exc:
            self._dispose(self._process, self._connection)
            self._failures = 1
            if not self._permanent:
                self._schedule_recovery()
            log('suspicion_model', 'warning', '모델 로드 실패; 엔진 폴백 대기',
                error_type=type(exc).__name__, model_version=self.model_version)

    @staticmethod
    def _dispose(process, connection):
        if connection is not None:
            connection.close()
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)

    def _start_worker(self):
        context = multiprocessing.get_context('spawn')
        self._connection, child = context.Pipe()
        self._process = context.Process(target=_worker, args=(child, self._loader), daemon=True)
        try:
            self._process.start()
        finally:
            child.close()
        deadline = time.monotonic() + self._load_timeout
        while not self._connection.poll(.02):
            if self._stop.is_set() or time.monotonic() >= deadline:
                raise ModelUnavailable('model load timeout or closed')
        status, value = self._connection.recv()
        if status == 'version':
            self._permanent = True
            raise FeatureVersionMismatch('artifact feature_version mismatch')
        if status != 'ready':
            self._permanent = value in ('FileNotFoundError', 'NotADirectoryError')
            raise ModelUnavailable(f'model load failed: {value}')
        version, feature = value
        if ((self._expected_feature is not None and feature != self._expected_feature)
                or (self.model_version != 'unavailable' and version != self.model_version)
                or (self.model_version != 'unavailable' and feature != self.feature_version)):
            self._permanent = True
            raise FeatureVersionMismatch('reloaded model identity or feature_version mismatch')
        self.model_version, self.feature_version = version, feature
        self._ready.set()

    def _schedule_recovery(self):
        self._ready.clear()
        self._recovery_thread = threading.Thread(target=self._recover, daemon=True)
        self._recovery_thread.start()

    def _recover(self):
        with self._lock:
            while not self._stop.is_set() and not self._permanent:
                self._dispose(self._process, self._connection)
                if self._failures >= 3:
                    self._cooldown_until = time.monotonic() + self.COOLDOWN_S
                    log('suspicion_model', 'warning', '연속 worker 실패; 30초 복구 대기',
                        model_version=self.model_version, failures=self._failures)
                    if self._stop.wait(self.COOLDOWN_S):
                        return
                if self._stop.is_set():
                    return
                try:
                    self._start_worker()
                    return
                except Exception as exc:
                    self._ready.clear()
                    self._failures += 1
                    log('suspicion_model', 'warning', '동일 버전 worker 재기동 실패',
                        model_version=self.model_version, error_type=type(exc).__name__)
            self._dispose(self._process, self._connection)

    def _receive(self, request_id, timeout_s):
        if not self._connection.poll(max(0, timeout_s)):
            raise _RequestTimeout('model timeout')
        received_id, status, result = self._connection.recv()
        if received_id != request_id:
            raise ModelUnavailable('model reply request mismatch')
        return status, result

    def _failed_worker(self):
        if not self._stop.is_set():
            self._failures += 1
            self._schedule_recovery()

    def _discard_late_reply(self, request_id, sent_at):
        """A single in-flight call owns the pipe until it replies or the hard deadline expires."""
        try:
            with self._lock:
                while not self._stop.is_set():
                    remaining = sent_at + self.HARD_TIMEOUT_S - time.monotonic()
                    if remaining <= 0:
                        log('suspicion_model', 'warning', '추론 강제 한도 초과; 동일 버전 재기동',
                            model_version=self.model_version, request_id=request_id)
                        self._failed_worker()
                        return
                    try:
                        status, _ = self._receive(request_id, min(remaining, .02))
                    except _RequestTimeout:
                        continue
                    except (EOFError, OSError):
                        self._failed_worker()
                        return
                    if status == 'unavailable':
                        self._failed_worker()
                    return
        finally:
            self._idle.set()

    def estimate(self, obs: ObservationSnapshot) -> SuspicionEstimate:
        """추론·IPC·잠금 대기를 한 마감으로 제한하고 실패는 엔진 폴백 예외로 바꾼다."""
        started = time.monotonic()
        self.calls += 1
        acquired = False
        try:
            if self._closed or self._permanent or not self._ready.is_set():
                raise ModelUnavailable('model closed or recovering')
            wait_started = time.monotonic()
            try:
                if not self._idle.wait(max(0, self.timeout_s - (time.monotonic() - started))):
                    raise ModelUnavailable('model busy')
                acquired = self._lock.acquire(timeout=max(0, self.timeout_s - (time.monotonic() - started)))
            finally:
                if self.wait_observer is not None:
                    self.wait_observer(wait_started, time.monotonic())
            if not acquired:
                raise ModelUnavailable('model busy')
            if not self._ready.is_set() or self._closed:
                raise ModelUnavailable('model recovering')
            if not self._idle.is_set() or time.monotonic() - started >= self.timeout_s:
                raise ModelUnavailable('model busy')
            self._request_id += 1
            request_id = self._request_id
            sent_at = time.monotonic()
            try:
                self._connection.send((request_id, obs))
                status, result = self._receive(request_id, self.timeout_s - (time.monotonic() - started))
            except _RequestTimeout:
                self._timeouts.append(started)
                self._idle.clear()
                threading.Thread(target=self._discard_late_reply, args=(request_id, sent_at), daemon=True).start()
                raise
            except (EOFError, OSError):
                self._failed_worker()
                raise
            if status == 'version':
                raise FeatureVersionMismatch('inference feature_version mismatch')
            if status == 'unavailable':
                self._failed_worker()
            if status != 'result':
                raise ModelUnavailable(f'model inference failed: {result}')
            _validate_output(result, obs, self.feature_version, self.model_version)
            self.successes += 1
            self._failures = 0
            for failed_at in self._timeouts:
                duration = (time.monotonic() - failed_at) * 1000
                self.recovery_ms.append(duration)
                log('suspicion_model', 'info', 'timeout 이후 동일 버전 추론 복구',
                    model_version=self.model_version, duration_ms=round(duration, 3))
            self._timeouts.clear()
            return result
        except FeatureVersionMismatch:
            log('suspicion_model', 'warning', '입력 특징 불일치; 해당 결정 폴백',
                error_type='FeatureVersionMismatch', model_version=self.model_version,
                feature_version=self.feature_version)
            raise
        except Exception as exc:
            log('suspicion_model', 'warning', '추론 실패; 엔진 규칙 v0 폴백',
                error_type=type(exc).__name__, reason=str(exc), model_version=self.model_version,
                game_id=obs.game_id, duration_ms=round((time.monotonic() - started) * 1000, 3))
            raise ModelUnavailable('suspicion inference unavailable') from exc
        finally:
            self.latencies_ms.append((time.monotonic() - started) * 1000)
            if acquired:
                self._lock.release()

    def close(self) -> None:
        """worker를 종료한다. 여러 번 호출해도 안전하며 늦은 결과는 재사용하지 않는다."""
        self._closed = True
        self._ready.clear()
        self._stop.set()
        with self._lock:
            self._dispose(self._process, self._connection)


class _BorrowedModel:
    def __init__(self, model):
        self.model_version, self.feature_version = model.model_version, model.feature_version
        self._model = model

    def estimate(self, obs):
        return self._model.estimate(obs)

    def close(self):
        """판 종료는 프로세스 공유 worker를 닫지 않는다."""


class SharedModelStore:
    """웹 프로세스가 버전별 모델을 소유하고 판에는 포트를 빌려준다."""

    def __init__(self):
        self._models = {}
        self._lock = threading.Lock()
        self._closed = False

    def get(self, version, loader):
        """첫 호출만 로드한다. 판은 반환된 포트를 빌려 쓰며 버전을 바꾸지 않는다."""
        with self._lock:
            if self._closed:
                raise ModelUnavailable('model store closed')
            if version not in self._models:
                model = loader()
                if model.model_version != version:
                    model.close()
                    raise ModelUnavailable('shared model version mismatch')
                self._models[version] = model
            return _BorrowedModel(self._models[version])

    def close(self):
        """프로세스 종료 시 소유 worker를 모두 닫는다."""
        with self._lock:
            self._closed = True
            for model in self._models.values():
                model.close()

    def worker_pids(self):
        """시험 계측용으로 현재 살아 있는 버전별 worker PID를 돌려준다."""
        with self._lock:
            return {version: model._process.pid for version, model in self._models.items()
                    if isinstance(model, SuspicionModelAdapter)
                    and model._process is not None and model._process.is_alive()}


class _ArtifactModel:
    def __init__(self, path, *, threadpool_api=None):
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
        self._thread_limits = threadpool_limits(limits=1, user_api=threadpool_api)
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


class InProcessSuspicionModelAdapter:
    """웹 프로세스에서 버전별 로지스틱 모델을 읽기 전용으로 공유한다."""

    def __init__(self, path: Path):
        self._model = _ArtifactModel(path, threadpool_api='blas')
        self.model_version = self._model.model_version
        self.feature_version = self._model.feature_version

    def estimate(self, obs: ObservationSnapshot) -> SuspicionEstimate:
        """직접 추정하고 기존 포트 예외와 결과 검증을 적용한다."""
        model = self._model
        try:
            if model is None:
                raise ModelUnavailable('model closed')
            result = model.estimate(obs)
            _validate_output(result, obs, self.feature_version, self.model_version)
            return result
        except FeatureVersionMismatch:
            log('suspicion_model', 'warning', '입력 특징 불일치; 해당 결정 폴백',
                error_type='FeatureVersionMismatch', model_version=self.model_version,
                feature_version=self.feature_version)
            raise
        except Exception as exc:
            log('suspicion_model', 'warning', '추론 실패; 엔진 규칙 v0 폴백',
                error_type=type(exc).__name__, reason=str(exc), model_version=self.model_version,
                game_id=obs.game_id)
            raise ModelUnavailable('suspicion inference unavailable') from exc

    def close(self) -> None:
        """프로세스 보관소가 참조를 해제하면 이후 호출을 거부한다."""
        self._model = None


def load_model(path: Path) -> SuspicionModelAdapter:
    """D-07-02의 로컬 models.pkl을 읽는다. 특징 계약은 기존 features 모듈이 검증한다."""
    return SuspicionModelAdapter(partial(_ArtifactModel, path), None)
