"""운영자가 확정한 고정 시드 판 배정과 광고 가명 대응표의 B 응용."""
import re
import uuid
from typing import Protocol
from engine.domain.model import DataSource, GameMeta, PRESETS
from .usecases import ResourceNotFound


class SessionStore(Protocol):
    """테스트 회차와 광고 가명만 저장한다. 로그인 ID·세션 쿠키는 받지 않는다."""

    def create(self, session):
        """이미 있는 ID는 덮어쓰지 않고 ValueError로 거부한다."""
        ...

    def list_sessions(self):
        """운영자에게 등록된 불변 세션 구성을 반환한다."""
        ...

    def get(self, session_id):
        """없는 회차는 KeyError다."""
        ...

    def for_player(self, player):
        """참가자에게 배정된 회차만 조회한다."""
        ...

    def ad_identity(self, player, session_key):
        """같은 플레이어·서버 내부 키에는 같은 무작위 광고 UUID를 반환한다."""
        ...

    def delete_player(self, player):
        """참가 배정과 광고 대응표를 삭제한다. 삭제 전에 광고 ID를 따로 조회한다."""
        ...

    def ad_identities(self, player):
        """C 삭제 작업이 먼저 조회할 광고 세션 가명 목록."""
        ...


class TestSessionService:
    """등록된 참가자에게 같은 고정 판 목록·버전을 배정한다."""

    def __init__(self, store):
        self.store = store

    def create(self, session_id, plans, participants, versions):
        """서버 설정 화면에서 받은 회차를 엄격히 검증하고 불변으로 저장한다."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", session_id):
            raise ValueError("invalid_session_id")
        if not 1 <= len(plans) <= 100 or not 1 <= len(participants) <= 1000:
            raise ValueError("invalid_session_size")
        participants = sorted({str(uuid.UUID(p)) for p in participants})
        if set(versions) != {"rule_version", "engine_version", "content_version"} or any(
                not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", v) for v in versions.values()):
            raise ValueError("invalid_versions")
        normalized = []
        for plan in plans:
            if (set(plan) != {"preset_id", "seed"} or not isinstance(plan["preset_id"], str)
                    or plan["preset_id"] not in PRESETS or type(plan["seed"]) is not int
                    or not 0 <= plan["seed"] < 2**63 or plan in normalized):
                raise ValueError("invalid_fixed_plan")
            normalized.append(dict(plan))
        self.store.create({"session_id": session_id, "plans": normalized, "participants": participants, "versions": dict(versions)})

    @staticmethod
    def _next_plan(session, player, load_game):
        for number, plan in enumerate(session["plans"], 1):
            # 같은 참가자가 같은 순번을 다시 요청하면 저장된 판을 이어 간다.
            game_id = str(uuid.uuid5(uuid.UUID(player), f'paldo:{session["session_id"]}:{number}'))
            try:
                state, _ = load_game(game_id)
            except KeyError:
                return dict(plan, number=number, game_id=game_id, started=False)
            if state.result is None:
                return dict(plan, number=number, game_id=game_id, started=True)
        return None

    def assigned(self, player, load_game):
        """참가자 화면에는 자기 회차의 다음 순번만 전달한다."""
        return [{"session_id": session["session_id"], "plan": self._next_plan(session, player, load_game)}
                for session in self.store.for_player(player)]

    def prepare(self, data, player, load_game):
        """등록 순서에 따라 진행 중이거나 새로 시작할 판의 메타를 결정한다."""
        try:
            session = self.store.get(data.session_id)
        except KeyError:
            raise ResourceNotFound from None
        if player not in session["participants"]:
            raise ResourceNotFound
        plan = self._next_plan(session, player, load_game)
        if (plan is None or plan["preset_id"] != data.preset_id
                or data.seed is not None and plan["seed"] != data.seed):
            raise ValueError("unassigned_plan")
        return GameMeta(plan["game_id"], PRESETS[plan["preset_id"]], plan["seed"], DataSource.HUMAN,
                        **session["versions"], player_pseudonym=player, session_id=data.session_id,
                        mudang_variant=data.mudang_variant, ending_choice=data.ending_choice)

    def ad_identity(self, player, session_key):
        """광고 전용 세션 가명을 서버에 저장한 뒤 반환한다."""
        return self.store.ad_identity(str(uuid.UUID(player)), session_key)
