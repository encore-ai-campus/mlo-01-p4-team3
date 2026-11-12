"""웹 운영 게임 서비스 조립(A-07-03). `PALDO_GAME_SERVICE_FACTORY=server.infra.game_factory.build_game_service`.

계층: 인프라(조립). Mongo 연결·게임·진척·콘텐츠·회차 저장소는 B의 `server.paldo_web.mongo.MongoRuntime`
(B-06-03)을 그대로 쓰고, 여기서는 그 위에 A 몫만 끼운다. 규칙은 두지 않는다.

- 엔진: 판의 콘텐츠 버전마다 `PaldoEngine(RuleV0Policy(content))`(`ContentPolicyEngine`).
- 의심 모델: 기본은 규칙 의심도를 확률로 바꾸는 `FakeSuspicionModel`. `PALDO_SUSPICION_MODEL_VERSION`을 주면
  프로세스 보관소(`ProcessSuspicionModels`)가 실제 모델을 버전별로 소유하고 판들이 빌려 쓴다.
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
import re
from typing import Callable

from engine.domain import params
from engine.domain.content import ContentBundle
from engine.domain.engine import PaldoEngine, abstain_margin_of, home_alibi
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
    rule-0.7 이하로 시작한 판(`home_alibi` 거짓)은 무당 의심 상한을 `params.LEGACY_DECISION.q_max`로 고정한
    정책을 따로 만들어, rule-0.8 배포 뒤에도 판 메타 버전의 수치로 이어 간다(A-10-01 1번).
    기권 규칙 판(rule-0.9~, `abstain_margin_of`)은 마을 주민 기권 문턱을 준 정책을 따로 만든다.
    `mix_source`(씐 자 전략 밴딧, `possessed_bandit.PinnedMixSource`)가 있으면 정책에 끼우고, 엔진 호출 직전마다
    판 메타를 알려 판에 고정된 팔을 쓰게 한다. 없으면 기존처럼 균등 혼합이다.
    """

    def __init__(self, make_policy: Callable[[ContentBundle], object] = RuleV0Policy, model_version='unknown',
                 mix_source=None):
        """콘텐츠별 엔진 캐시와 관측용 모델 버전을 보관한다."""
        self.make_policy = make_policy
        self.model_version = model_version
        self.mix_source = mix_source
        self._engines: dict[tuple, PaldoEngine] = {}
        self._lock = threading.Lock()

    def _policy(self, content: ContentBundle, legacy: bool, abstain_margin=None):
        kw = {}
        if legacy:
            kw["q_max"] = params.LEGACY_DECISION.q_max
        if abstain_margin is not None:
            kw["abstain_margin"] = abstain_margin
        if self.mix_source is not None:
            kw["mix_source"] = self.mix_source
        if not kw:
            return self.make_policy(content)
        try:
            return self.make_policy(content, **kw)
        except TypeError:   # q_max·abstain_margin을 받지 않는 정책 공급자(시험용 등)는 그대로 쓴다
            return self.make_policy(content)

    def _for(self, content: ContentBundle, rule_version: str = "") -> PaldoEngine:
        legacy = bool(rule_version) and not home_alibi(rule_version)
        margin = abstain_margin_of(rule_version) if rule_version else None
        key = (content.content_version, legacy, margin)
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = self._engines[key] = PaldoEngine(self._policy(content, legacy, margin))
            return engine

    def _note(self, meta):
        if self.mix_source is not None:
            self.mix_source.note(meta)

    def new_game(self, meta, deps):
        """판 콘텐츠 버전(과 규칙 버전)의 엔진으로 판을 만든다."""
        self._note(meta)
        return self._for(deps.content, meta.rule_version).new_game(meta, deps)

    def apply(self, state, truth, env, deps):
        """판 콘텐츠 버전(과 규칙 버전)의 엔진으로 명령을 적용한다."""
        from server.infra.observed_models import observe_decisions
        self._note(state.meta)
        transition=self._for(deps.content, state.meta.rule_version).apply(state, truth, env, deps)
        observe_decisions(transition,self.model_version)
        return transition

    def player_view(self, state, content):
        """판 콘텐츠 버전의 엔진으로 플레이어 화면 정보를 만든다."""
        self._note(state.meta)
        return self._for(content, state.meta.rule_version).player_view(state, content)


def engine_parts(mix_source=None):
    """운영 조립이 B 실행 자원에 넘기는 엔진과 의심 모델. `mix_source`는 씐 자 전략 밴딧(없으면 균등)."""
    from engine.testing.fakes import FakeSuspicionModel
    from server.infra.observed_models import ObservedSuspicionModel
    model=ObservedSuspicionModel(FakeSuspicionModel())
    return ContentPolicyEngine(model_version=model.model_version, mix_source=mix_source), model


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


def attach_npc_lines(service, store, narrator, settings):
    """게임 서비스에 NPC 대사 LLM(E-11-13)을 끼우고, 실시간 생성 렌더러를 돌려준다. `narrator`가 None이면 늘 off."""
    from server.app.npc_lines import NpcLineService
    from server.infra.kanana_line_renderer import KananaLineRenderer
    service.npc_lines = NpcLineService(service, store, narrator, settings)
    return KananaLineRenderer(store, settings) if narrator is not None else None


def npc_line_parts(database, prefix):
    """설정 `NPC_LLM_URL`(상한 스위치)·`NPC_LLM_MODE`로 생성기·작업 저장소·운영 설정 읽기를 만든다. 같은 접두어."""
    from django.conf import settings
    from server.infra.kanana_narrator import KananaNarrator
    from server.infra.npc_line_jobs import COLLECTION, MongoNpcLineStore
    from server.infra.ops_settings import COLLECTION as OPS, MongoOpsSettings
    url = getattr(settings, "NPC_LLM_URL", "")
    return (MongoNpcLineStore(database[prefix + COLLECTION]), KananaNarrator(url) if url else None,
            MongoOpsSettings(database[prefix + OPS], url, getattr(settings, "NPC_LLM_MODE", "")))


def build_from_runtime(runtime, parser):
    """B 실행 자원의 게임 서비스에 A 몫(보조 기록·해석기·렌더러·복기 해설·NPC 대사 LLM)을 끼워 돌려준다. 같은 접두어."""
    from server.infra.interaction_store import MongoInteractionStore
    service = runtime.factory_service()
    prefix = runtime_prefix(runtime)
    store = MongoInteractionStore(runtime.database[prefix + INTERACTIONS])
    attach_narration(service, *narration_parts(runtime.database, prefix))
    renderer = attach_npc_lines(service, *npc_line_parts(runtime.database, prefix))
    return attach_interactions(service, store, parser, renderer)


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
        return ObservedSuspicionModel(selector.suspicion_model(v, in_process_logistic=True))

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
    from server.infra.possessed_bandit import PinnedMixSource, attach_possessed_bandit
    from ml.bandit import load_arms
    # 스위치를 꺼 되돌려도 이미 팔을 고정한 판은 그 팔을 계속 쓰도록 혼합 공급자는 늘 끼운다(새 판 고정만 스위치).
    arms = load_arms()
    engine, fake = engine_parts(PinnedMixSource(arms))
    store, production = suspicion_models(fake, getattr(settings, "SUSPICION_MODEL_VERSION", ""),
                                         getattr(settings, "SUSPICION_MODEL_ROOT", "mlruns"))
    atexit.register(store.close)
    runtime = open_mongo_runtime(engine=engine, suspicion=fake)
    parser = BaselineIntentParser()
    service = build_from_runtime(runtime, parser)
    if path := getattr(settings, "WIN_PROBABILITY_MODEL", ""):
        from server.infra.win_probability import WinProbabilityModel
        service.win_probability = WinProbabilityModel.from_path(path)
    intent_version = getattr(getattr(parser, "model", None), "model_version", "")
    registry = StaticModelRegistry({INTENT_KEY: intent_version, SUSPICION_KEY: production})
    if configured := getattr(settings, "MLFLOW_MODELS", ""):
        import json
        from mlflow import MlflowClient
        from ml.suspicion.features import FEATURE_VERSION
        from server.infra.mlflow_artifacts import MlflowArtifacts, PinnedIntentParser
        from server.infra.mlflow_registry import MlflowModelRegistry
        from server.infra.model_selector import ProcessSuspicionModels
        from server.infra.observed_models import ObservedSuspicionModel
        models = json.loads(configured)
        if set(models) != {INTENT_KEY, SUSPICION_KEY} or any(
                not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name)
                for name in models.values()):
            raise ValueError('configured_mlflow_model_pair_required')
        artifacts = MlflowArtifacts(MlflowClient(), models, settings.MLFLOW_MODEL_CACHE, FEATURE_VERSION)
        registry = MlflowModelRegistry(artifacts.client, models, artifacts.prepare)
        previous_store = store
        store = ProcessSuspicionModels(lambda v: fake if v == fake.model_version else
                                       ObservedSuspicionModel(artifacts.suspicion_model(v)),
                                       registry.cached_versions, feature_version=FEATURE_VERSION)
        initial = registry.production_versions()  # 시작할 때 실제 가중치와 출처를 확인한다.
        if not store.preload(initial[SUSPICION_KEY]):
            raise ValueError('production_suspicion_weights_unavailable')
        artifacts.intent_parser(initial[INTENT_KEY])
        previous_store.close()
        atexit.register(store.close)
        service.interactions.parser = PinnedIntentParser(service.repository, artifacts, parser)
    service.release_game = store.release
    attach_model_versions(service, registry, store, lambda: getattr(settings, "MODEL_CANARY_RATIO", 0.0))
    return attach_possessed_bandit(service, runtime.database, runtime_prefix(runtime), arms,
                                   lambda: getattr(settings, "POSSESSED_BANDIT", False))
