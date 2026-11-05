"""엔진 연산 1회 시간 측정(`--op new_game|apply --n N`). 결과 JSON은 `write_result`로 쓴다.

    python -m tools.perf.engine_bench --op new_game --n 1000

계층: 표현·응용(인자 파싱, 판 조립, 시간 측정, 출력). 규칙은 `engine/domain/`에 있다.
시드는 0부터 N-1까지 고정이라 같은 명령으로 다시 돌릴 수 있다. 콘텐츠는 `fixtures/content_min`.
"""
from __future__ import annotations

import argparse
import math
import time

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.model import PRESETS, DataSource, GameMeta
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel, FixtureContentSource
from tools.evidence_result import write_result
from tools.jsonlog import log

COMPONENT = "engine_bench"
TASK_OF_OP = {"new_game": "A-05-01", "apply": "A-05-02"}


def percentile(values: list[float], q: float) -> float:
    """최근접 순위 방식 백분위수(q는 0~100). 값이 없으면 ValueError."""
    if not values:
        raise ValueError("값이 없음")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def bench_new_game(n: int, preset_id: str) -> list[float]:
    """시드 0..n-1로 판을 n번 만들고 1회 시간(ms) 목록을 돌려준다. 콘텐츠 로드는 측정에서 뺀다."""
    content = FixtureContentSource().load("*")
    deps = EngineDeps(content, FakeSuspicionModel())
    engine = PaldoEngine()
    preset = PRESETS[preset_id]
    out = []
    for seed in range(n):
        meta = GameMeta(f"bench_{preset_id}_{seed:05d}", preset, seed, DataSource.BOT,
                        params.RULE_PARAMS_VERSION, "bench", content.content_version)
        t0 = time.perf_counter()
        engine.new_game(meta, deps)
        out.append((time.perf_counter() - t0) * 1000)
    return out


def main(argv=None) -> int:
    """명령행 진입점. 측정값을 로그로 남기고 결과 JSON을 쓴다."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--op", choices=sorted(TASK_OF_OP), required=True)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--preset", default="base6", choices=sorted(PRESETS))
    ap.add_argument("--no-write", action="store_true", help="결과 JSON을 쓰지 않는다(시험 실행)")
    a = ap.parse_args(argv)
    if a.n <= 0:
        ap.error("--n은 1 이상")
    if a.op == "apply":
        log(COMPONENT, "error", "apply 측정은 A-05-02에서 구현", error_type="NotImplemented", op=a.op)
        return 2
    log(COMPONENT, "info", "측정 시작", op=a.op, n=a.n, preset=a.preset,
        rule_version=params.RULE_PARAMS_VERSION)
    times = bench_new_game(a.n, a.preset)
    metrics = {
        "new_game_p50": {"value": round(percentile(times, 50), 3), "unit": "ms"},
        "new_game_p95": {"value": round(percentile(times, 95), 3), "unit": "ms"},
        "new_game_max": {"value": round(max(times), 3), "unit": "ms"},
        "games": {"value": len(times), "unit": "판"},
    }
    log(COMPONENT, "info", "측정 완료", op=a.op, n=a.n, preset=a.preset,
        duration_ms=round(sum(times), 3), **{k: v["value"] for k, v in metrics.items()})
    if not a.no_write:
        source = f"python -m tools.perf.engine_bench --op {a.op} --n {a.n}"
        if a.preset != "base6":
            source += f" --preset {a.preset}"
        path = write_result(TASK_OF_OP[a.op], f"engine_{a.op}", metrics, source)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
