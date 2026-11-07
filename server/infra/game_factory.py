"""웹 운영 게임 서비스 조립(A-07-03). `PALDO_GAME_SERVICE_FACTORY=server.infra.game_factory.build_game_service`.

계층: 인프라(조립). Mongo 연결·게임·진척·콘텐츠·회차 저장소는 B의 `server.paldo_web.mongo.MongoRuntime`
(B-06-03)을 그대로 쓰고, 여기서는 그 위에 A 몫만 끼운다. 규칙은 두지 않는다.

- 엔진: 판의 콘텐츠 버전마다 `PaldoEngine(RuleV0Policy(content))`(`ContentPolicyEngine`).
- 의심 모델: 규칙 의심도를 확률로 바꾸는 `FakeSuspicionModel`(통합 판 구성, 실제 모델은 D-08-01 뒤 교체).
- 보조 기록: `MongoInteractionStore`, 컬렉션은 B 규칙대로 `<접두어>b_interactions`.
- 해석기: 프로세스마다 한 번 만든 D-07-01 기준선. 웹 조립이 관측 래퍼를 씌운다.
- 렌더러: 런타임 LLM 없이(P-31 (나)) 엔진의 템플릿 문장(`fallback_text`)을 쓰게 하는 `TemplateLineRenderer`.
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

    def __init__(self, make_policy: Callable[[ContentBundle], object] = RuleV0Policy):
        self.make_policy = make_policy
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
        return self._for(deps.content).apply(state, truth, env, deps)

    def player_view(self, state, content):
        """판 콘텐츠 버전의 엔진으로 플레이어 화면 정보를 만든다."""
        return self._for(content).player_view(state, content)


def engine_parts():
    """운영 조립이 B 실행 자원에 넘기는 엔진과 의심 모델."""
    from engine.testing.fakes import FakeSuspicionModel
    return ContentPolicyEngine(), FakeSuspicionModel()


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


def build_from_runtime(runtime, parser):
    """B 실행 자원의 게임 서비스에 A 몫(보조 기록·해석기·렌더러)을 끼워 돌려준다. 보조 기록은 같은 접두어."""
    from server.infra.interaction_store import MongoInteractionStore
    service = runtime.factory_service()
    store = MongoInteractionStore(runtime.database[runtime_prefix(runtime) + INTERACTIONS])
    return attach_interactions(service, store, parser)


def build_game_service():
    """`PALDO_GAME_SERVICE_FACTORY`가 부르는 운영 조립. 연결 실패·미설정은 B의 `GameServiceUnavailable`."""
    from intent.parser import BaselineIntentParser
    from server.paldo_web.mongo import open_mongo_runtime
    engine, suspicion = engine_parts()
    runtime = open_mongo_runtime(engine=engine, suspicion=suspicion)
    return build_from_runtime(runtime, BaselineIntentParser())
