"""B-08-02 화면 시험에서만 두 녹화 판과 일반 판 정책을 조립한다."""
from pathlib import Path
from engine.domain.model import ENGINE_VERSION
from engine.testing.fakes import ScriptedGameEngine
from server.app.open_start import StartPolicy, StartVersions
from .support import make_service


class PresetRecordingEngine:
    """판 메타의 프리셋으로 공개 녹화 판을 고르는 시험 엔진."""

    def __init__(self):
        """6인 기존 녹화와 공용 엔진 도구로 만든 8인 녹화를 읽는다."""
        self.recordings = {'base6': ScriptedGameEngine(),
                           'large8': ScriptedGameEngine(Path(__file__).with_name('recorded_large8.json'))}

    def new_game(self, meta, deps):
        """선택된 녹화 판을 시작한다."""
        return self.recordings[meta.preset.preset_id].new_game(meta, deps)

    def apply(self, state, truth, env, deps):
        """같은 녹화 판의 다음 공개 상태로 진행한다."""
        return self.recordings[state.meta.preset.preset_id].apply(state, truth, env, deps)

    def player_view(self, state, content):
        """선택한 프리셋의 공개 화면만 반환한다."""
        return self.recordings[state.meta.preset.preset_id].player_view(state, content)


def make_preset_service():
    """운영 조립과 분리된 메모리 저장소·녹화 엔진·일반 판 정책을 만든다."""
    service = make_service()
    service.engine = PresetRecordingEngine()
    service.interactions = None
    service.prepare_start = StartPolicy(service.prepare_start,
        lambda: StartVersions('test-rule', ENGINE_VERSION, service.dependencies(None).content.content_version),
        service.repository.load)
    return service
