"""B-08-02 시험용 공개 8인 녹화 판을 공용 run_engine_games 실행 경로에서 만든다."""
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
from engine.domain.engine import PaldoEngine
from engine.domain.serde import to_jsonable


def main():
    """large8 한 판의 공개 PlayerView·명령만 저장한다. 진실·NPC 개인 상태는 저장하지 않는다."""
    steps = []

    class Recorder(PaldoEngine):
        """원본 엔진 전이를 바꾸지 않고 성공한 공개 화면만 녹화한다."""
        def new_game(self, meta, deps):
            """처음 공개 화면을 적는다."""
            result = super().new_game(meta, deps)
            steps.append({'view': to_jsonable(self.player_view(result.state, deps.content))})
            return result

        def apply(self, state, truth, env, deps):
            """수락한 명령과 전이 후 공개 화면을 적는다."""
            result = super().apply(state, truth, env, deps)
            if result.accepted:
                steps.append({'request': {'expected_state_version': env.expected_state_version,
                                          'command': to_jsonable(env.command)},
                              'view': to_jsonable(self.player_view(result.state, deps.content))})
            return result

    args = ['tools.run_engine_games', '--preset', 'large8', '--games', '1', '--seed', '1',
            '--out', 'out/handoff/large8']
    with patch('engine.domain.engine.PaldoEngine', Recorder), patch.object(sys, 'argv', args):
        try:
            runpy.run_module('tools.run_engine_games', run_name='__main__')
        except SystemExit as error:
            if error.code:
                raise
    path = Path(__file__).with_name('recorded_large8.json')
    path.write_text(json.dumps({'source': ' '.join(args), 'steps': steps}, ensure_ascii=False,
                               separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({'frames': len(steps), 'fixture_bytes': path.stat().st_size}))


if __name__ == '__main__':
    main()
