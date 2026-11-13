"""LLM 작업 프로세스 지표(E-12-01): 결과 분류·누적·Prometheus 텍스트 렌더.

계층: 응용(순수 계산). 파일·시계·저장소를 모른다. 작업 프로세스(`npc_line_worker`·`review_worker`)가 작업 결과를
`record_*`로 넘기고, 인프라 `server/infra/metric_file.py`가 `render()` 결과를 파일로 쓴다.
지표 이름·라벨은 C-12-03 대시보드와의 계약이다(docs/modules/llm.md 변경 이력).
"""
import ntpath
import posixpath
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

OK, LATE, REJECTED, ERROR = "ok", "late", "rejected", "error"
OUTCOMES = (OK, LATE, REJECTED, ERROR)
BUCKETS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0)


def npc_outcome(status: str, reason: Optional[str]) -> Optional[str]:
    """NPC 대사 작업 결과 → outcome. 관리 스위치로 닫힌 작업(closed/mode_off)은 세지 않는다(None)."""
    if status == "done":
        return OK
    if status == "discarded" or (status == "failed" and reason == "timeout"):
        return LATE
    if status == "rejected":
        return REJECTED
    if status == "failed":
        return ERROR
    return None


def review_outcome(status: str, saved: bool) -> str:
    """복기 해설 작업 결과 → outcome. 생성했지만 저장하지 못한 것(마감·임대 상실)은 늦음."""
    if status == "done":
        return OK if saved else LATE
    if status == "timeout":
        return LATE
    return ERROR


@dataclass
class Histogram:
    """누적 히스토그램(초). counts[i]는 BUCKETS[i] 이하 누적 건수."""
    counts: list = field(default_factory=lambda: [0] * len(BUCKETS))
    total: int = 0
    sum: float = 0.0

    def observe(self, seconds: float) -> None:
        """값 하나를 더한다(음수는 0으로)."""
        v = max(0.0, float(seconds))
        for i, le in enumerate(BUCKETS):
            if v <= le:
                self.counts[i] += 1
        self.total += 1
        self.sum += v


def _fmt(v: float) -> str:
    return repr(float(v)) if isinstance(v, float) else str(v)


class WorkerMetrics:
    """한 작업 프로세스의 누적 지표(프로세스 시작 뒤부터). 재시작하면 0부터."""

    def __init__(self, worker: str):
        self.worker = worker
        self.jobs: Dict[str, int] = {o: 0 for o in OUTCOMES}
        self.wait = Histogram()
        self.gen = Histogram()
        self.queue_depth: Optional[int] = None
        self.lines: Dict[str, int] = {"llm": 0, "template": 0}
        self.loop_errors = 0

    def record(self, outcome: Optional[str], queue_s: Optional[float] = None, gen_s: Optional[float] = None) -> None:
        """작업 하나의 결과. outcome이 None이면(세지 않는 작업) 아무것도 하지 않는다."""
        if outcome is None:
            return
        self.jobs[outcome] += 1
        if queue_s is not None:
            self.wait.observe(queue_s)
        if gen_s is not None:
            self.gen.observe(gen_s)

    def record_loop_error(self) -> None:
        """작업을 꺼내거나 처리하다 예외가 난 횟수. 꺼내기 전 저장소 장애일 수 있어 작업 건수에는 넣지 않는다."""
        self.loop_errors += 1

    def record_lines(self, llm: int, template: int) -> None:
        """화면에 나갈 단위별 출처(LLM 문장 / 정해 둔 문장). NPC 대사는 줄, 복기는 NPC 해설 묶음 단위."""
        self.lines["llm"] += max(0, int(llm))
        self.lines["template"] += max(0, int(template))

    def render(self, now: float) -> str:
        """Prometheus 텍스트 형식."""
        w = f'worker="{self.worker}"'
        out = ["# HELP paldo_llm_jobs_total LLM 작업 처리 건수(프로세스 시작 뒤 누적)",
               "# TYPE paldo_llm_jobs_total counter"]
        out += [f'paldo_llm_jobs_total{{{w},outcome="{o}"}} {self.jobs[o]}' for o in OUTCOMES]
        out += ["# HELP paldo_llm_lines_total 출처별 결과 단위 수(NPC 대사는 줄, 복기는 NPC 해설 묶음)",
                "# TYPE paldo_llm_lines_total counter"]
        out += [f'paldo_llm_lines_total{{{w},source="{k}"}} {self.lines[k]}' for k in ("llm", "template")]
        out += ["# HELP paldo_llm_worker_loop_errors_total 작업 꺼내기·처리 중 예외 횟수(저장소 장애 포함)",
                "# TYPE paldo_llm_worker_loop_errors_total counter",
                f"paldo_llm_worker_loop_errors_total{{{w}}} {self.loop_errors}"]
        for name, help_, h in (("paldo_llm_queue_wait_seconds", "작업 생성부터 처리 시작까지", self.wait),
                               ("paldo_llm_generate_seconds", "생성 소요", self.gen)):
            out += [f"# HELP {name} {help_}", f"# TYPE {name} histogram"]
            out += [f'{name}_bucket{{{w},le="{_fmt(le)}"}} {c}' for le, c in zip(BUCKETS, h.counts)]
            out += [f'{name}_bucket{{{w},le="+Inf"}} {h.total}', f"{name}_sum{{{w}}} {_fmt(h.sum)}",
                    f"{name}_count{{{w}}} {h.total}"]
        if self.queue_depth is not None:
            out += ["# HELP paldo_llm_queue_depth 지금 대기 중인 작업 수", "# TYPE paldo_llm_queue_depth gauge",
                    f"paldo_llm_queue_depth{{{w}}} {self.queue_depth}"]
        out += ["# HELP paldo_llm_worker_last_update_seconds 지표 파일 마지막 갱신 시각(유닉스 초)",
                "# TYPE paldo_llm_worker_last_update_seconds gauge",
                f"paldo_llm_worker_last_update_seconds{{{w}}} {_fmt(float(now))}"]
        return "\n".join(out) + "\n"


def metric_path(template: str, worker: str) -> Optional[str]:
    """`PALDO_LLM_METRIC_FILE` 값에서 워커별 경로. 비면 None(지표 끔). `{worker}`가 없으면 파일 이름 앞에 붙인다."""
    template = (template or "").strip()
    if not template:
        return None
    if "{worker}" in template:
        return template.replace("{worker}", worker)
    paths = ntpath if ("\\" in template or ntpath.splitdrive(template)[0]) else posixpath
    head, tail = paths.split(template)
    return paths.join(head, f"{worker}_{tail}") if head else f"{worker}_{tail}"


def ms_to_s(ms: Optional[int]) -> Optional[float]:
    """밀리초 → 초."""
    return None if ms is None else ms / 1000.0


__all__: Tuple[str, ...] = ("OK", "LATE", "REJECTED", "ERROR", "OUTCOMES", "BUCKETS", "npc_outcome",
                            "review_outcome", "Histogram", "WorkerMetrics", "metric_path", "ms_to_s")
