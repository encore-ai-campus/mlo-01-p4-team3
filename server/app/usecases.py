"""B 게임 응용 흐름. Django와 저장소 구현을 모르며 주입한 포트만 호출한다."""
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from engine.domain.events import EventEnvelope, topic_of
from engine.domain.model import CONTRACT_VERSION, CommandEnvelope, DataSource, RejectCode
from engine.domain.serde import dumps, from_jsonable
from engine.ports import StoredCommand, VersionConflict
from .dto import CommandOutput, GameOutput, ProgressOutput, ReviewItem, ReviewOutput


class ResourceNotFound(Exception):
    """존재하지 않거나 소유하지 않은 자원. 두 경우를 구분해 노출하지 않는다."""


class ConsentRequired(Exception):
    """판 시작에 필요한 수집 동의가 없다."""


class StartUnavailable(Exception):
    """테스트 세션·시드 배정 정책이 아직 연결되지 않았다."""


class ReviewNotReady(Exception):
    """종료 전에는 역할·비밀을 공개하는 복기를 열 수 없다."""


class GameService:
    """조회·시작·명령 처리의 공통 계약을 실행한다."""

    def __init__(self, engine, repository, progress, dependencies, prepare_start=None):
        self.engine = engine
        self.repository = repository
        self.progress = progress
        self.dependencies = dependencies
        self.prepare_start = prepare_start
        self.interactions = None
        self.load_test = False

    def owned(self, game_id, player_pseudonym):
        """멱등 응답·버전 확인보다 먼저 판 소유권을 확인한다."""
        try:
            state, truth = self.repository.load(game_id)
        except KeyError:
            raise ResourceNotFound from None
        if state.meta.player_pseudonym != player_pseudonym:
            raise ResourceNotFound
        return state, truth

    def detail(self, game_id, player_pseudonym):
        """엔진이 허용한 PlayerView만 응답으로 만든다."""
        state, _ = self.owned(game_id, player_pseudonym)
        view = self._player_view(state)
        return GameOutput(view, list(view.pending_render_jobs), state.meta.mudang_variant)

    def _player_view(self, state):
        view = self.engine.player_view(state, self.dependencies(state.meta).content)
        if self.interactions is not None:
            with self.interactions.store.edit(state.meta.game_id) as records:
                if records.notebook:
                    view = replace(view, player=replace(view.player, notebook=dict(records.notebook["entries"])))
        return view

    def start(self, data, player_pseudonym, consented):
        """서버의 세션 배정 정책을 통과한 메타로만 판을 시작한다."""
        if not consented:
            raise ConsentRequired
        if data.player_pseudonym != player_pseudonym or data.mudang_variant not in ("f", "m"):
            raise ValueError("invalid_start")
        if data.ending_choice not in self.progress.load(player_pseudonym).endings_unlocked:
            raise ValueError("ending_locked")
        if self.prepare_start is None:
            raise StartUnavailable
        meta = self.prepare_start(data, player_pseudonym)
        # HTTP 입력으로 소유자·출처·판 ID를 선택할 수 없다.
        meta = replace(meta, game_id=str(uuid.uuid4()), source=DataSource.HUMAN,
                       player_pseudonym=player_pseudonym, mudang_variant=data.mudang_variant)
        result = self.engine.new_game(meta, self.dependencies(meta))
        if not result.accepted or result.state is None or result.truth is None:
            raise ValueError("start_rejected")
        self.repository.create(result.state, result.truth, self._events(result.state, result.events))
        self.progress.add_game(meta.player_pseudonym, meta.game_id)
        return self.detail(meta.game_id, player_pseudonym)

    def player_progress(self, player_pseudonym):
        """로그인 계정 자신의 진척만 조회한다."""
        return ProgressOutput(self.progress.load(player_pseudonym))

    def review(self, game_id, player_pseudonym):
        """종료된 자기 판의 DTO 허용 정보만 복기로 반환한다."""
        state, truth = self.owned(game_id, player_pseudonym)
        if state.result is None:
            raise ReviewNotReady
        game_id = state.meta.game_id
        progress = self.progress.load(player_pseudonym)
        # 행동 시점 이유는 review_trace 인수 전까지 비워 둔다. 현재 믿음에서 추론하지 않는다.
        timeline = [ReviewItem(speech.speech_id, speech.speaker, [], list(speech.evidence))
                    for speech in state.public.speeches]
        clues = [clue_id for clue_id, record in sorted(progress.clues.items())
                 if record.first_game_id == game_id]
        return ReviewOutput(game_id, state.result, dict(truth.roles), dict(truth.secrets),
                            timeline, clues_gained=clues)

    def command(self, data, player_pseudonym):
        """중복 → 버전 → 엔진 → 원자 저장 → 경합 처리 순서를 지킨다."""
        state, truth = self.owned(data.game_id, player_pseudonym)
        saved = self.repository.find_command(data.game_id, data.command_id)
        if saved:
            return self.replay_command(data.game_id, saved, player_pseudonym)
        if data.expected_state_version != state.state_version:
            return self._conflict(state)
        if data.command.actor != "mudang":
            raise ValueError("invalid_actor")
        env = CommandEnvelope(data.game_id, data.command_id, data.expected_state_version,
                              data.command, data.intent_attempt_id)
        deps = self.dependencies(state.meta)
        result = self.engine.apply(state, truth, env, deps)
        if not result.accepted:
            return CommandOutput(False, state.state_version, self._player_view(state),
                                 result.reject, result.reject_detail)
        view = self._player_view(result.state)
        output = CommandOutput(True, result.state.state_version, view,
                               pending_render_jobs=list(view.pending_render_jobs))
        args = (data.game_id, data.expected_state_version, result.state, result.truth,
                self._events(result.state, result.events, data.command_id),
                StoredCommand(data.command_id, dumps(output)))
        if self.interactions is not None:
            self.interactions.prepare_command_effects(data.command_id, args[-1].response_json,
                                                       state, result.state, result.render_requests)
        try:
            try:
                self.repository.commit(*args)
            except (ConnectionError, TimeoutError):
                # 결과가 불명확한 전송 오류에만 같은 commit을 한 번 재전송한다.
                self.repository.commit(*args)
        except VersionConflict:
            current, _ = self.owned(data.game_id, player_pseudonym)
            saved = self.repository.find_command(data.game_id, data.command_id)
            if saved is None:
                return self._conflict(current)
            replay = self._replay(saved)
            if saved.response_json != args[-1].response_json:
                # 같은 ID라도 다른 요청이 저장한 결과에 이 요청의 렌더를 붙이지 않는다.
                return self.replay_command(data.game_id, saved, player_pseudonym)
            # 저장 성공 후 확인 응답만 유실된 경우에도 아래 후속 처리를 마친다.
            output = replay
        if self.interactions is not None:
            self.interactions.complete_command_effects(data.game_id, args[-1], player_pseudonym)
        return output

    def replay_command(self, game_id, saved, player_pseudonym):
        """명령·카드 경로 공통: 소유권 확인 뒤 후속 처리를 복구하고 저장 응답을 반환한다."""
        self.owned(game_id, player_pseudonym)
        if self.interactions is not None:
            self.interactions.complete_command_effects(game_id, saved, player_pseudonym)
        return self._replay(saved)

    def _conflict(self, state):
        return CommandOutput(False, state.state_version,
                             self._player_view(state),
                             RejectCode.STATE_VERSION_CONFLICT)

    @staticmethod
    def _replay(saved):
        return replace(from_jsonable(CommandOutput, json.loads(saved.response_json)), replayed=True)

    def _events(self, state, events, command_id=None):
        meta = state.meta
        return [EventEnvelope(
            event_id="", game_id=meta.game_id, seq=0, state_version=state.state_version,
            event_type=e.event_type.value, topic=topic_of(e.event_type),
            occurred_at=datetime.now(timezone.utc).isoformat(), day=e.day, phase=e.phase.value,
            source=meta.source, contract_version=CONTRACT_VERSION, preset_id=meta.preset.preset_id,
            seed=meta.seed, rule_version=meta.rule_version, engine_version=meta.engine_version,
            content_version=meta.content_version, npc_policy_versions=meta.npc_policy_versions,
            model_versions=meta.model_versions, payload=e.payload,
            player_pseudonym=meta.player_pseudonym, session_id=meta.session_id, command_id=command_id,
            load_test=self.load_test,
        ) for e in events]
