"""웹 운영 게임 서비스 조립(A-07-03). `PALDO_GAME_SERVICE_FACTORY=server.infra.game_factory.build_game_service`.

계층: 인프라(조립). Mongo 연결·게임·진척·콘텐츠·회차 저장소는 B의 `server.paldo_web.mongo.MongoRuntime`
(B-06-03)을 그대로 쓰고, 여기서는 그 위에 A 몫만 끼운다. 규칙은 두지 않는다.

- 엔진: 판의 콘텐츠 버전마다 `PaldoEngine(RuleV0Policy(content))`(`ContentPolicyEngine`).
- 의심 모델: 기본은 규칙 의심도를 확률로 바꾸는 `FakeSuspicionModel`. `PALDO_SUSPICION_MODEL_VERSION`을 주면
  프로세스 보관소(`ProcessSuspicionModels`)가 그 실제 모델 버전을 버전당 worker 하나로 띄워 판들이 빌려 쓴다.
- 보조 기록: `MongoInteractionStore`, 컬렉션은 B 규칙대로 `<접두어>b_interactions`.
- 해석기: 프로세스마다 한 번 만든 D-07-01 기준선. 웹 조립이 관측 래퍼를 씌운다.
- 렌더러: 런타임 LLM 없이(P-31 (나)) 엔진의 템플릿 문장(`fallback_text`)을 쓰게 하는 `TemplateLineRenderer`.
- 모델 버전(B-09-01): 판 시작 때 `VersionPinning`이 레지스트리 운영 버전을 `GameMeta.model_versions`에 고정하고,
  엔진 의존성은 판이 고정한 의심 모델 버전으로 만든다. 레지스트리는 C-09-01 전까지 `StaticModelRegistry`(배포된 버전).
- 연결 문자열은 B와 같이 설정 `SESSION_MONGO_URI`(환경 변수 `PALDO_MONGO_URI`)에서 읽는다. 콘텐츠는
  `python -m server.infra.load_content`로 같은 DB의 `content` 컬렉션에 미리 적재한다.
"""
from __future__ import annotations

import threading
from typing import Callable

from engine.domain.content import ContentBundle
from engine.domain.engine import PaldoEngine
from engine.domain.npc import RuleV0Policy
from engine.ports import RenderRequest, RenderResult, RenderStatus
from server.app.interactions import InteractionService

INTERACTIONS = "b_interactions"


class TemplateLineRenderer:
    """런타임 LLM 없이 엔진의 템플릿 문장을 그대로 쓰게 하는 `LineRenderer`(P-31 (나)).

    상태를 두지 않아 여러 프로세스에서 같은 결과를 낸다. `poll`은 바로 끝난 폴백 결과를 돌려주고,
    화면은 `RenderRequest.fallback_text`(오프라인에서 만든 템플릿, 같은 정보량)를 표시한다.
    """

    reason = "runtime_llm_disabled"

    def submit(self, req: RenderRequest) -> None:
        """받은 요청은 생성하지 않는다."""

    def poll(self, job_id: str) -> RenderResult:
        """생성 없이 끝난 결과. 표시 문장은 응용이 `fallback_text`로 채운다."""
        return RenderResult(job_id, RenderStatus.FAILED, None, latency_ms=0, queue_ms=0,
                            failure_reason=self.reason, model_version="template", fallback=True)

    def cancel(self, job_id: str) -> None:
        """진행 중인 생성이 없어 할 일이 없다."""


class ContentPolicyEngine:
    """판의 콘텐츠 버전마다 `PaldoEngine(RuleV0Policy(content))`를 한 번 만들어 위임한다.

    규칙 v0은 인물 성향·동선을 콘텐츠에서 찾으므로 판이 고정한 콘텐츠 버전으로 정책을 만든다.
    """

    def __init__(self, make_policy: Callable[[ContentBundle], object] = RuleV0Policy, model_version='unknown'):
        """콘텐츠별 엔진 캐시와 관측용 모델 버전을 보관한다."""
        self.make_policy = make_policy
        self.model_version = model_version
        self._engines: dict[str, PaldoEngine] = {}
        self._lock = threading.Lock()

    def _for(self, content: ContentBundle) -> PaldoEngine:
        with self._lock:
            engine = self._engines.get(content.content_version)
            if engine is None:
                engine = self._engines[content.content_version] = PaldoEngine(self.make_policy(content))
            return engine

    def new_game(self, meta, deps):
        """판 콘텐츠 버전의 엔진으로 판을 만든다."""
        return self._for(deps.content).new_game(meta, deps)

    def apply(self, state, truth, env, deps):
        """판 콘텐츠 버전의 엔진으로 명령을 적용한다."""
        from server.infra.observed_models import observe_decisions
        transition=self._for(deps.content).apply(state, truth, env, deps)
        observe_decisions(transition,self.model_version)
        return transition

    def player_view(self, state, content):
        """판 콘텐츠 버전의 엔진으로 플레이어 화면 정보를 만든다."""
        return self._for(content).player_view(state, content)


def engine_parts():
    """운영 조립이 B 실행 자원에 넘기는 엔진과 의심 모델."""
    from engine.testing.fakes import FakeSuspicionModel
    from server.infra.observed_models import ObservedSuspicionModel
    model=ObservedSuspicionModel(FakeSuspicionModel())
    return ContentPolicyEngine(model_version=model.model_version), model


def attach_interactions(service, store, parser, renderer=None):
    """게임 서비스에 자유 입력·수첩·설문 서비스를 끼운다. 운영과 시험이 같은 함수를 쓴다."""
    service.interactions = InteractionService(service, store, parser=parser,
                                              renderer=renderer if renderer is not None else TemplateLineRenderer())
    return service


def runtime_prefix(runtime) -> str:
    """B 실행 자원의 컬렉션 접두어. 게임 컬렉션 이름(`<접두어>games`)에서 읽어 따로 넘기지 않는다."""
    games = runtime.collection_names[0]
    if not games.endswith("games"):
        raise ValueError("unexpected_runtime_collections")
    return games[:-len("games")]


def attach_narration(service, store, narrator, deadline_s=180.0, game_exists=lambda _gid: True):
    """게임 서비스에 복기 해설 LLM 요약 작업(E-10-01)을 끼운다. `narrator`가 None이면 기능 꺼짐."""
    from server.app.review_narration import NarrationSettings, ReviewNarrationService
    service.narration = ReviewNarrationService(store, narrator, settings=NarrationSettings(deadline_s=deadline_s),
                                               game_exists=game_exists)
    return service


def narration_parts(database, prefix):
    """설정 `REVIEW_LLM_URL`로 생성기를, 같은 접두어의 `review_jobs`로 작업 저장소를 만든다."""
    from django.conf import settings
    from server.infra.kanana_narrator import KananaNarrator
    from server.infra.review_jobs import COLLECTION, MongoReviewJobStore
    url = getattr(settings, "REVIEW_LLM_URL", "")
    games = database[prefix + "games"]
    return (MongoReviewJobStore(database[prefix + COLLECTION]), KananaNarrator(url) if url else None,
            float(getattr(settings, "REVIEW_DEADLINE_S", 180.0)),
            lambda gid: games.count_documents({"_id": gid}, limit=1) > 0)


def build_from_runtime(runtime, parser):
    """B 실행 자원의 게임 서비스에 A 몫(보조 기록·해석기·렌더러·복기 해설)을 끼워 돌려준다. 같은 접두어."""
    from server.infra.interaction_store import MongoInteractionStore
    service = runtime.factory_service()
    prefix = runtime_prefix(runtime)
    store = MongoInteractionStore(runtime.database[prefix + INTERACTIONS])
    attach_narration(service, *narration_parts(runtime.database, prefix))
    return attach_interactions(service, store, parser)


def attach_model_versions(service, registry, suspicion_models=None, canary_ratio=lambda: 0.0):
    """게임 서비스에 판 시작 버전 고정(B-09-01)과 판 버전별 의심 모델 선택을 끼운다. 운영과 시험이 같은 함수를 쓴다."""
    from server.app.model_versions import VersionPinning, pinned_dependencies
    service.pin_versions = VersionPinning(registry, canary_ratio)
    service.dependencies = pinned_dependencies(service.dependencies, suspicion_models)
    service.model_registry = registry
    return service


def suspicion_models(fake, version: str = "", root: str = "mlruns"):
    """프로세스 의심 모델 보관소와 운영(새 판 고정) 버전을 만든다(B-09-01 1번).

    `version`이 비면 규칙 의심도 `fake`만 쓴다. 값이 있으면 `LocalModelSelector`로 그 버전을 시작 때 미리 띄우고,
    못 띄우면 경고를 남기고 `fake`를 운영 버전으로 쓴다. 값이 비어도 이미 실제 버전을 고정한
    판은 산출물이 남아 있으면 그 버전을 계속 쓴다. 판 진행 중 장애는 보관소·어댑터가 그 결정만 폴백한다.
    """
    from server.infra.model_selector import LocalModelSelector, ProcessSuspicionModels
    from server.infra.observed_models import ObservedSuspicionModel
    from tools.jsonlog import log
    from ml.suspicion.features import FEATURE_VERSION
    # 운영 설정을 비워 되돌려도(롤백) 이미 실제 버전을 고정한 판이 그 버전을 계속 쓰도록 선택기는 늘 만든다.
    selector = LocalModelSelector(root, suspicion_version=version, intent_version="", feature_version=FEATURE_VERSION)

    def load(v):
        if v == fake.model_version:
            return fake
        return ObservedSuspicionModel(selector.suspicion_model(v))

    production = {"value": fake.model_version}
    store = ProcessSuspicionModels(load, lambda: {production["value"]}, feature_version=fake.feature_version)
    if version:
        if store.preload(version):
            production["value"] = version
        else:
            log("model_store", "warning", "실제 의심 모델을 띄우지 못해 규칙 의심도로 시작", model_version=version)
    return store, production["value"]


def build_game_service():
    """`PALDO_GAME_SERVICE_FACTORY`가 부르는 운영 조립. 연결 실패·미설정은 B의 `GameServiceUnavailable`.

    `PALDO_SUSPICION_MODEL_VERSION`이 있으면 실제 의심 모델을 새 판에 고정한다(`suspicion_models`).
    """
    import atexit
    from django.conf import settings
    from intent.parser import BaselineIntentParser
    from server.app.model_versions import INTENT_KEY, SUSPICION_KEY
    from server.infra.model_registry import StaticModelRegistry
    from server.paldo_web.mongo import open_mongo_runtime
    engine, fake = engine_parts()
    store, production = suspicion_models(fake, getattr(settings, "SUSPICION_MODEL_VERSION", ""),
                                         getattr(settings, "SUSPICION_MODEL_ROOT", "mlruns"))
    atexit.register(store.close)
    runtime = open_mongo_runtime(engine=engine, suspicion=fake)
    parser = BaselineIntentParser()
    service = build_from_runtime(runtime, parser)
    intent_version = getattr(getattr(parser, "model", None), "model_version", "")
    registry = StaticModelRegistry({INTENT_KEY: intent_version, SUSPICION_KEY: production})
    service.release_game = store.release
    return attach_model_versions(service, registry, store,
                                 lambda: getattr(settings, "MODEL_CANARY_RATIO", 0.0))
