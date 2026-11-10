"""B 게임 응용 흐름. Django와 저장소 구현을 모르며 주입한 포트만 호출한다."""
import json
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from engine.domain.events import EventEnvelope, topic_of
from engine.domain.model import CONTRACT_VERSION, CommandEnvelope, DataSource, RejectCode
from engine.domain.serde import dumps, from_jsonable
from engine.ports import StoredCommand, VersionConflict
from engine.domain.abandon import AlreadyEnded, abandon
from engine.domain.progress import ending_available
from .dto import CommandOutput, GameOutput, ProgressOutput, ReviewItem, ReviewOutput
from .clue_notice import clue_notices
from .progress import apply_progress, sweep_game

LOGGER = logging.getLogger("paldo.progress")


class ResourceNotFound(Exception):
    """존재하지 않거나 소유하지 않은 자원. 두 경우를 구분해 노출하지 않는다."""


class ConsentRequired(Exception):
    """판 시작에 필요한 수집 동의가 없다."""


class StartUnavailable(Exception):
    """테스트 세션·시드 배정 정책이 아직 연결되지 않았다."""


class ReviewNotReady(Exception):
    """종료 전에는 역할·비밀을 공개하는 복기를 열 수 없다."""


class OpenGameInProgress(Exception):
    """진행 중인 일반 판이 있는데 중단 동의 없이 새 일반 판을 시작하려 했다(11/10 사용자 결정)."""

    def __init__(self, game_id):
        super().__init__(game_id)
        self.game_id = game_id   # 이어 할 가장 최근 진행 판


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
        # 판 시작 때 모델 버전을 고정하는 정책(B-09-01, `model_versions.VersionPinning`). None이면 고정하지 않는다.
        self.pin_versions = None
        # 저장에 성공한 엔진 결과 이벤트를 받는 관측 함수(B-09-01 폴백 지표). None이면 부르지 않는다.
        self.observe_events = None
        # 업데이트 내역 화면이 읽는 모델 레지스트리(B-09-01, `model_versions.ModelRegistry`). None이면 빈 내역.
        self.model_registry = None
        # 계정별 일반 판 시작 잠금(11/10 진행 판 양자택일). 같은 프로세스 안의 동시 시작을 줄 세운다.
        self._start_locks = {}
        self._start_locks_guard = threading.Lock()
        # 판 종료를 받는 함수(B-09-01 1번 의심 모델 보관소 `release`). None이면 부르지 않는다.
        self.release_game = None

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
        return self._detail(game_id, player_pseudonym)

    def _detail(self, game_id, player_pseudonym, deps=None):
        """저장된 최신 판을 읽고 같은 콘텐츠 버전의 요청 내 의존성만 재사용한다."""
        state, _ = self.owned(game_id, player_pseudonym)
        if deps is not None and deps.content.content_version != state.meta.content_version:
            deps = None
        view = self._player_view(state, deps)
        return GameOutput(view, list(view.pending_render_jobs), state.meta.mudang_variant)

    def _player_view(self, state, deps=None):
        view = self.engine.player_view(state, (deps if deps is not None else self.dependencies(state.meta)).content)
        if self.interactions is not None:
            with self.interactions.participant_records(state.meta.game_id) as records:
                if records.notebook:
                    view = replace(view, player=replace(view.player, notebook=dict(records.notebook["entries"])))
        return view

    def start(self, data, player_pseudonym, consented):
        """서버의 세션 배정 정책을 통과한 메타로만 판을 시작한다."""
        if not consented:
            raise ConsentRequired
        if data.player_pseudonym != player_pseudonym or data.mudang_variant not in ("f", "m"):
            raise ValueError("invalid_start")
        if not ending_available(self.progress.load(player_pseudonym), data.ending_choice):
            raise ValueError("ending_locked")
        if self.prepare_start is None:
            raise StartUnavailable
        meta = self.prepare_start(data, player_pseudonym)
        # HTTP 입력으로 소유자·출처·판 ID를 선택할 수 없다.
        fixed_id = meta.game_id
        replay = self._replay_start(fixed_id, player_pseudonym)
        if replay is not None:
            if meta.session_id is None and data.replace_open:
                with self._start_lock(player_pseudonym):
                    self._abandon_older(fixed_id, player_pseudonym)   # 앞 요청이 중단 도중 실패했으면 마저 한다
            return replay
        if meta.session_id is not None:
            return self._create(meta, data, fixed_id, player_pseudonym, ())
        # 일반 판: 진행 중인 일반 판이 있으면 이어 하기 / 중단하고 새로 시작 중 하나만(11/10 사용자 결정).
        # 확인·중단·생성을 계정별로 줄 세워 같은 프로세스 안의 동시 시작이 진행 판을 둘 만들지 않게 한다.
        with self._start_lock(player_pseudonym):
            replay = self._replay_start(fixed_id, player_pseudonym)   # 잠금을 기다리는 사이 같은 시작 UUID 판이 생김
            if replay is not None:
                if data.replace_open:
                    self._abandon_older(fixed_id, player_pseudonym)
                return replay
            others = [gid for gid in self.unfinished_open_games(player_pseudonym) if gid != fixed_id]
            if others and not data.replace_open:
                raise OpenGameInProgress(others[-1])
            return self._create(meta, data, fixed_id, player_pseudonym, others)

    def _replay_start(self, fixed_id, player_pseudonym):
        """같은 시작 UUID·회차 순번의 판이 이미 있으면 그 판 화면, 없으면 None."""
        if not fixed_id:
            return None
        try:
            output = self.detail(fixed_id, player_pseudonym)
        except ResourceNotFound:
            return None
        self.progress.add_game(player_pseudonym, fixed_id)
        return output

    def _abandon_older(self, game_id, player_pseudonym):
        """같은 시작 UUID 재요청: 이 판보다 먼저 시작한 진행 중 일반 판을 중단한다(뒤에 시작한 판은 두고)."""
        game_ids = self.progress.load(player_pseudonym).game_ids
        if game_id not in game_ids:
            return
        older = set(game_ids[:game_ids.index(game_id)])
        self._abandon_each([gid for gid in self.unfinished_open_games(player_pseudonym) if gid in older],
                           player_pseudonym)

    def _abandon_each(self, game_ids, player_pseudonym):
        for gid in game_ids:
            if not self.abandon_game(gid, player_pseudonym) and gid in self.unfinished_open_games(player_pseudonym):
                # 충돌로 중단하지 못한 판은 진행 중으로 남는다(지우지 않음). 다음 새로 시작·재요청 때 다시 중단한다.
                LOGGER.warning("open game abandon conflict; left unfinished: %s", gid)

    def _start_lock(self, player_pseudonym):
        with self._start_locks_guard:
            return self._start_locks.setdefault(player_pseudonym, threading.Lock())

    def _create(self, meta, data, fixed_id, player_pseudonym, replaced):
        """새 판을 준비·저장한 뒤 replaced(중단할 진행 판)를 중단한다."""
        meta = replace(meta, game_id=fixed_id or str(uuid.uuid4()), source=DataSource.HUMAN,
                       player_pseudonym=player_pseudonym, mudang_variant=data.mudang_variant,
                       ending_choice=data.ending_choice)
        if self.pin_versions is not None:
            # 새로 만드는 판에만 버전을 고른다. 이미 저장된 판은 위에서 그대로 반환했다.
            meta = self.pin_versions(meta)
        deps = self.dependencies(meta)
        result = self.engine.new_game(meta, deps)
        if not result.accepted or result.state is None or result.truth is None:
            raise ValueError("start_rejected")
        try:
            self.repository.create(result.state, result.truth, self._events(result.state, result.events))
        except ValueError:
            if not fixed_id:
                raise
            # 같은 순번의 동시 시작에서 먼저 저장된 판만 반환한다.
            self.owned(fixed_id, player_pseudonym)
        else:
            self._observe(result.events)
        self.progress.add_game(meta.player_pseudonym, meta.game_id)
        # 새 판이 저장된 뒤에만 기존 판을 중단한다. 준비·저장이 실패하면 기존 판은 그대로 이어 할 수 있다.
        self._abandon_each(replaced, player_pseudonym)
        return self._detail(meta.game_id, player_pseudonym, deps)

    def unfinished_open_games(self, player_pseudonym):
        """계정의 진행 중인 일반 판(회차 없음) ID를 시작 순서대로."""
        from .open_start import unfinished_open_games   # open_start가 이 모듈을 import하므로 늦게 불러온다
        return unfinished_open_games(self.progress.load(player_pseudonym).game_ids, self.repository.load)

    def abandon_game(self, game_id, player_pseudonym):
        """진행 중인 일반 판을 중단 처리한다(삭제하지 않음). 이번 호출로 중단했으면 True, 아니면 False.

        False는 이미 끝난 판이거나 버전 충돌이 두 번 이어진 경우다. 호출자는 판이 아직 진행 중인지 다시 확인한다.

        같은 판에는 같은 명령 ID(`abandon:<판 ID>`)를 써서 재요청·동시 요청이 이벤트를 두 번 남기지 않는다.
        """
        command_id = f"abandon:{game_id}"
        for _ in range(2):
            state, truth = self.owned(game_id, player_pseudonym)
            if state.meta.session_id is not None:
                raise ValueError("session_game_not_abandonable")
            if state.result is not None or self.repository.find_command(game_id, command_id):
                return False
            try:
                new_state, events = abandon(state)
            except AlreadyEnded:
                return False
            envelopes = self._events(new_state, events, command_id)
            stored = StoredCommand(command_id, json.dumps({"abandoned": game_id}))
            try:
                self.repository.commit(game_id, state.state_version, new_state, truth, envelopes, stored)
            except VersionConflict:
                continue   # 그사이 바뀐 판을 다시 읽어 한 번 더 판단한다
            self._observe(events)
            self._sweep_saved(player_pseudonym, new_state)   # 중단 전 고백 단서는 승패 무관 규칙대로 남긴다
            self._release(game_id)
            return True
        return False

    def player_progress(self, player_pseudonym):
        """로그인 계정 자신의 진척만 조회한다."""
        return ProgressOutput(self.progress.load(player_pseudonym))

    def guide_info(self, game_id, player_pseudonym):
        """대사창 안내 판단: (이 판이 계정의 첫 판인지, 이번 판 씐 자 수, 고백 뒤 단서 알림 목록).

        씐 자 수는 프리셋 공개 값이다. 단서 알림은 판의 공개 고백과 판 밖 진척으로 고른다(`clue_notice`).
        """
        state, _ = self.owned(game_id, player_pseudonym)
        progress = self.progress.load(player_pseudonym)
        game_ids = progress.game_ids
        notices = []
        if state.public.confessions:
            content = self.dependencies(state.meta).content
            notices = clue_notices(state.meta.game_id, state.public.confessions, content.secrets,
                                   content.grudge_clues, progress.clues, game_ids)
        return bool(game_ids) and game_ids[0] == state.meta.game_id, state.meta.preset.n_possessed, notices

    def review(self, game_id, player_pseudonym):
        """종료된 자기 판의 DTO 허용 정보만 복기로 반환한다."""
        state, truth = self.owned(game_id, player_pseudonym)
        if state.result is None:
            raise ReviewNotReady
        game_id = state.meta.game_id
        self._sweep_saved(player_pseudonym, state)
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
        return self.command_from_owned(data, player_pseudonym, state, truth)

    def command_from_owned(self, data, player_pseudonym, state, truth):
        """서버가 읽은 요청 범위 스냅샷을 재사용한다. 소유자·판과 저장 CAS는 재검사한다."""
        if (state.meta.game_id != data.game_id or truth.game_id != data.game_id
                or state.meta.player_pseudonym != player_pseudonym):
            raise ResourceNotFound
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
            return CommandOutput(False, state.state_version, self._player_view(state, deps),
                                 result.reject, result.reject_detail)
        # 엔진이 콘텐츠 버전을 바꿨다면 이전 의존성을 재사용하지 않는다.
        view = self._player_view(result.state, deps if result.state.meta.content_version == state.meta.content_version else None)
        output = CommandOutput(True, result.state.state_version, view,
                               pending_render_jobs=list(view.pending_render_jobs))
        args = (data.game_id, data.expected_state_version, result.state, result.truth,
                self._events(result.state, result.events, data.command_id),
                StoredCommand(data.command_id, dumps(output)))
        if self.interactions is not None:
            self.interactions.prepare_command_effects(data.command_id, args[-1].response_json,
                                                       state, result.state, result.render_requests)
        committed = False
        try:
            try:
                self.repository.commit(*args)
            except (ConnectionError, TimeoutError):
                # 결과가 불명확한 전송 오류에만 같은 commit을 한 번 재전송한다.
                self.repository.commit(*args)
            committed = True
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
        if committed:
            # 경합 경로는 이 요청의 저장인지 같은 명령의 동시 요청인지 가릴 수 없어 세지 않는다(중복 집계 방지).
            self._observe(result.events)
        if result.state.result is not None:
            # 판 종료: 이 판의 모든 고백을 다시 맞춘다(앞 명령에서 실패한 반영 복구 포함, 이번 전이 고백도 포함).
            self._progress_guard(sweep_game, player_pseudonym, result.state, deps.content)
            self._release(data.game_id)
        else:
            self._progress_guard(apply_progress, player_pseudonym, args[4], deps.content.grudge_clues)
        if self.interactions is not None:
            self.interactions.complete_command_effects(data.game_id, args[-1], player_pseudonym)
        return output

    def _release(self, game_id):
        """끝난 판을 의심 모델 보관소 사용 판에서 뺀다. 실패해도 저장된 명령 응답은 바꾸지 않는다."""
        if self.release_game is None:
            return
        try:
            self.release_game(game_id)
        except Exception:  # noqa: BLE001 - 보관소 정리 실패로 판 종료 응답을 막지 않는다
            LOGGER.exception("model store release failed")

    def _observe(self, events):
        """이 요청이 저장한 엔진 결과 이벤트를 관측 함수에 넘긴다. 관측 실패는 게임 응답을 막지 않는다."""
        if self.observe_events is None:
            return
        try:
            self.observe_events(events)
        except Exception:  # noqa: BLE001 - 지표 실패로 저장된 명령 응답을 바꾸지 않는다.
            pass
    def _progress_guard(self, apply, player_pseudonym, *args):
        """고백 단서·결말 해금을 진척에 반영한다(E-09-08). 판 상태는 이미 저장됐으므로 진척 저장 실패는
        명령 응답을 바꾸지 않고 기록만 남긴다. 판 종료·같은 명령 재요청·복기 때 `sweep_game`이 따라잡고,
        그래도 남는 경우는 아웃박스 재처리(C-09-04)로 복구한다."""
        try:
            apply(self.progress, player_pseudonym, *args)
        except Exception:  # noqa: BLE001 - 진척 장애가 게임 진행을 막지 않게 한다
            LOGGER.exception("progress apply failed; will retry on game end, command retry, review or outbox replay")

    def _sweep_saved(self, player_pseudonym, state):
        """저장된 판으로 진척을 따라잡는다. 콘텐츠 조회 실패도 가드 안에서 처리해 재요청·복기를 막지 않는다."""
        self._progress_guard(lambda repo, player: sweep_game(repo, player, state, self.dependencies(state.meta).content),
                             player_pseudonym)

    def replay_command(self, game_id, saved, player_pseudonym):
        """명령·카드 경로 공통: 소유권 확인 뒤 후속 처리를 복구하고 저장 응답을 반환한다."""
        state, _ = self.owned(game_id, player_pseudonym)
        if state.public.confessions:
            # 저장 뒤 진척 반영 전에 끊긴 요청을 같은 명령 ID로 다시 보내면 여기서 따라잡는다.
            self._sweep_saved(player_pseudonym, state)
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
