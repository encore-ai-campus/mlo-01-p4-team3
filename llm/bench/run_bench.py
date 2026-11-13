"""인프라: LLM 벤치마크(E-06-07). llama-cpp-python으로 추궁 요청의 지연·처리량·메모리를 잰다.

측정 구조(서비스 E-08-09와 같게): 모델 인스턴스 1개 + FIFO 큐 + 작업자 스레드 1개. 한 라운드에 요청 N개를
동시에 큐에 넣고, 요청마다 큐 대기·프롬프트 처리·생성·검증을 나눠 잰다. 전체 지연 = 큐 대기 + 처리 + 검증.

프롬프트와 검증은 E-07-07의 실제 함수(`llm/prompt.py`, `llm/validator.py`)를 부른다(E-06-07 때는 임시 `bench-tmp-1`).

    python -m llm.bench.run_bench --model <gguf> --label qwen3_4b --stage s1 --threads 6 --concurrency 1 --repeats 5
    python -m llm.bench.run_bench --model <gguf> --label kanana2_3b --eval fixtures/llm/interrogation_eval.json

`--eval`(E-07-07 완료 조건 3): 평가 세트를 `llm.mvp_metrics.run_eval`로 차례로 돌려(동시 1건) 성공률·사유 분포·
지연을 결과 JSON(`docs/evidence/perf/E-07-07/`)으로 쓴다. 생성 설정 기본값은 `llm.prompt.GEN_SETTINGS`다.
`--eval … --rejudge`: 모델을 부르지 않고 `out/perf/E-07-07/`의 요청 기록을 지금 검증기로 다시 판정해(`mvp_metrics.rejudge`)
결과 JSON을 다시 쓴다. 검증 규칙만 바꾸고 프롬프트는 그대로일 때 쓴다(11/6 PM 결정). 인자는 원래 측정과 같게 준다.

계층: 위쪽 순수 함수(백분위·검증·로그 해석·요약, 단독 테스트) / 아래쪽 인프라(llama.cpp 호출, 프로세스 메모리,
결과 기록) / `main`(인자 파싱과 흐름 조립). `llama_cpp`·`jinja2`는 함수 안에서 늦게 import한다
(CI `core`는 이 패키지 없이 순수 함수 테스트를 돌린다).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from llm import mvp_metrics
from llm.prompt import GEN_SETTINGS, PROMPT_VERSION, STOP, build_messages
from llm.validator import Lexicon, lexicon_from_bundle, normalize_output, validate

ROOT = Path(__file__).resolve().parents[2]
TASK = "E-06-07"
EVAL_TASK = "E-07-07"
FIXTURE = ROOT / "fixtures" / "llm" / "interrogation_requests.json"
BUNDLE = ROOT / "fixtures" / "content_min" / "bundle.json"
_BUFFER_RE = re.compile(r"(\S+)\s+(model|KV|RS|compute|output) buffer size\s*=\s*([0-9]+(?:\.[0-9]+)?) MiB")
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


# ---------------------------------------------------------------- 순수 함수

percentile = mvp_metrics.percentile   # 최근접 순위(보간 없음). 정의는 llm/mvp_metrics.py


def strip_think(text: str) -> str:
    """사고 블록(`<think>…</think>`)과 앞뒤 공백을 지운다."""
    return _THINK_RE.sub("", text).strip()


def validate_output(text: str, req: dict, lexicon: Lexicon) -> tuple[bool, str]:
    """`llm.validator.validate`로 검증한다. (통과, 사유). 사유는 "ok" 또는 대표 사유 코드 4종 중 하나."""
    result = validate(normalize_output(text), req, lexicon)
    return result.ok, result.reason or "ok"


def parse_buffer_sizes(log_text: str) -> dict[str, float]:
    """llama.cpp 로드 로그의 `<버퍼> <종류> buffer size = N MiB` 줄을 종류별로 더한다.

    model → weights(파일 매핑) / weights_repack(CPU_REPACK: AVX2용으로 다시 배열한 사본), KV → kv,
    RS → rs(순환 상태), compute, output. weights_total = weights + weights_repack.
    """
    keys = {"KV": "kv", "RS": "rs", "compute": "compute", "output": "output"}
    out = {k: 0.0 for k in ("weights", "weights_repack", "kv", "rs", "compute", "output")}
    for buf, kind, mib in _BUFFER_RE.findall(log_text):
        if kind == "model":
            out["weights_repack" if "REPACK" in buf.upper() else "weights"] += float(mib)
        else:
            out[keys[kind]] += float(mib)
    out["weights_total"] = out["weights"] + out["weights_repack"]
    return {k: round(v, 2) for k, v in out.items()}


@dataclass
class RequestRecord:
    """요청 1건의 측정 기록. 시각은 time.perf_counter 초, 길이는 ms."""
    job_id: str
    submit_s: float
    start_s: float
    end_s: float
    prompt_ms: float
    gen_ms: float
    prompt_tokens: int
    gen_tokens: int
    validate_ms: float
    ok: bool
    reason: str

    @property
    def total_ms(self) -> float:
        """큐 대기 + 처리 + 검증."""
        return (self.end_s - self.submit_s) * 1000

    @property
    def queue_ms(self) -> float:
        """큐에 넣은 뒤 처리 시작까지."""
        return (self.start_s - self.submit_s) * 1000


def summarize(records: list[RequestRecord]) -> dict[str, dict]:
    """요청 기록을 결과 JSON metrics(README 4절 형식)로 요약한다."""
    if not records:
        raise ValueError("기록이 없음")
    total = [r.total_ms for r in records]
    qwait = [r.queue_ms for r in records]
    tps = [r.gen_tokens / (r.gen_ms / 1000) for r in records if r.gen_ms > 0 and r.gen_tokens > 0]

    def m(value, unit):
        return {"value": round(value, 2), "unit": unit}

    out = {
        "total_p50_ms": m(percentile(total, 50), "ms"),
        "total_p95_ms": m(percentile(total, 95), "ms"),
        "total_max_ms": m(max(total), "ms"),
        "queue_wait_p50_ms": m(percentile(qwait, 50), "ms"),
        "queue_wait_p95_ms": m(percentile(qwait, 95), "ms"),
        "prompt_p50_ms": m(percentile([r.prompt_ms for r in records], 50), "ms"),
        "gen_p50_ms": m(percentile([r.gen_ms for r in records], 50), "ms"),
        "validate_p95_ms": m(percentile([r.validate_ms for r in records], 95), "ms"),
        "prompt_tokens_mean": m(sum(r.prompt_tokens for r in records) / len(records), "tok"),
        "gen_tokens_mean": m(sum(r.gen_tokens for r in records) / len(records), "tok"),
        "n_requests": {"value": len(records), "unit": "건"},
        "validation_fail_count": {"value": sum(not r.ok for r in records), "unit": "건"},
        "error_count": {"value": sum(r.reason == "error" for r in records), "unit": "건"},
    }
    if tps:
        out["gen_tps_p50"] = m(percentile(tps, 50), "tok/s")
        out["gen_tps_min"] = m(min(tps), "tok/s")
    return out


def pick_requests(fixtures: list[dict], round_no: int, n: int) -> list[dict]:
    """라운드 round_no에 넣을 요청 n건. 고정 입력을 차례로 돌려 쓴다."""
    return [fixtures[(round_no * n + i) % len(fixtures)] for i in range(n)]


def result_name(stage: str, label: str, mode: str, threads: int, concurrency: int, tag: str = "") -> str:
    """결과 JSON 이름. 예: s1_qwen3_4b_win_t6_c1."""
    name = f"{stage}_{label}_{mode}_t{threads}_c{concurrency}"
    return f"{name}_{tag}" if tag else name


# ---------------------------------------------------------------- 인프라

def log(component: str, level: str, msg: str, **extra) -> None:
    """구조화 로그(README 5절) 한 줄을 표준 출력과 `out/logs/llm_bench.jsonl`에 쓴다. 주소·계정은 넣지 않는다."""
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "level": level,
              "service": "llm", "component": component, "msg": msg, **extra}
    line = json.dumps(record, ensure_ascii=False)
    print(line, flush=True)
    log_dir = Path(os.environ.get("PALDO_LOG_DIR", ROOT / "out" / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "llm_bench.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:
        print(json.dumps({**record, "level": "warning", "msg": "로그 파일 쓰기 실패",
                          "error_type": type(exc).__name__}, ensure_ascii=False), flush=True)


def process_memory_mib() -> dict[str, float]:
    """현재 프로세스 메모리(보조 지표). Windows: 작업 집합·최대 작업 집합, Linux: VmRSS·VmHWM."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return {}
        mib = 1024 * 1024
        return {"rss": pmc.WorkingSetSize / mib, "rss_peak": pmc.PeakWorkingSetSize / mib}
    try:
        text = Path("/proc/self/status").read_text()
    except OSError:
        return {}
    vals = dict(re.findall(r"^(VmRSS|VmHWM):\s+(\d+) kB", text, re.M))
    return {"rss": int(vals.get("VmRSS", 0)) / 1024, "rss_peak": int(vals.get("VmHWM", 0)) / 1024}


_POWER_NAMES = {
    "381b4222-f694-41f0-9685-ff5bb260df2e": "균형", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "고성능",
    "a1841308-3541-4fab-bc81-f71556f20b4a": "절전", "e9a42b02-d5df-448d-aa00-03f14749eb61": "최고 성능 계획",
    "00000000-0000-0000-0000-000000000000": "균형", "961cc777-2547-4f9d-8174-7d86181b8a7a": "최고 효율",
    "ded574b5-45a0-4f42-8737-46345c09c238": "최고 성능",
}


def power_status() -> dict[str, str]:
    """Windows 전원 상태(충전기·절전 모드·전원 계획·전원 모드). 노트북은 이 값에 따라 속도가 크게 바뀐다. 그 밖의 OS는 빈 dict."""
    if sys.platform != "win32":
        return {}
    import ctypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                    ("d4", ctypes.c_ubyte * 8)]

        def text(self):
            b = bytes(self.d4)
            return f"{self.d1:08x}-{self.d2:04x}-{self.d3:04x}-{b[:2].hex()}-{b[2:].hex()}"

    out: dict[str, str] = {}
    s = SPS()
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        out["ac_power"] = {0: "배터리", 1: "충전기"}.get(s.ACLineStatus, "모름")
        out["battery_saver"] = "켜짐" if s.SystemStatusFlag & 1 else "꺼짐"
    powrprof = ctypes.WinDLL("powrprof")
    ptr = ctypes.POINTER(GUID)()
    if powrprof.PowerGetActiveScheme(None, ctypes.byref(ptr)) == 0:
        g = ptr.contents.text()
        out["power_plan"] = f"{_POWER_NAMES.get(g, '기타')}({g})"
        ctypes.windll.kernel32.LocalFree(ptr)
    overlay = GUID()
    try:
        if powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(overlay)) == 0:
            g = overlay.text()
            out["power_mode"] = f"{_POWER_NAMES.get(g, '기타')}({g})"
    except AttributeError:   # 오래된 Windows에는 이 함수가 없다
        pass
    return out


class BenchModel:
    """llama.cpp 모델 1개. 채팅 템플릿을 직접 그려 사고 모드를 끄고, 요청마다 perf 카운터로 단계를 나눠 잰다."""

    def __init__(self, path: str, threads: int, n_ctx: int, seed: int):
        """모델을 불러오고 로드 로그(버퍼 크기)를 모은다."""
        import llama_cpp
        from jinja2.sandbox import ImmutableSandboxedEnvironment

        self._llama_cpp = llama_cpp
        buf = io.StringIO()
        t0 = time.perf_counter()
        with contextlib.redirect_stderr(buf):
            self.llm = llama_cpp.Llama(model_path=path, n_threads=threads, n_threads_batch=threads,
                                       n_ctx=n_ctx, seed=seed, verbose=True)
        self.load_ms = (time.perf_counter() - t0) * 1000
        self.load_log = buf.getvalue()
        self.llm.verbose = False   # 요청마다 찍히는 perf 출력을 끈다(값은 llama_perf_context로 읽는다)
        template = self.llm.metadata.get("tokenizer.chat_template")
        if not template:
            raise RuntimeError("GGUF에 tokenizer.chat_template이 없음")
        env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                            extensions=[_generation_extension()])
        env.globals["raise_exception"] = _raise
        env.globals["strftime_now"] = lambda fmt: datetime.now().strftime(fmt)
        self._template = env.from_string(template)
        self.lock = threading.Lock()

    def render(self, messages: list[dict]) -> str:
        """채팅 템플릿으로 프롬프트 문자열을 만든다. BOS는 토큰화 때 붙으므로 빈 문자열로 둔다."""
        return self._template.render(messages=messages, add_generation_prompt=True, enable_thinking=False,
                                     bos_token="", eos_token="")

    def generate(self, prompt: str, max_tokens: int, temperature: float, top_p: float = GEN_SETTINGS["top_p"],
                 top_k: int = GEN_SETTINGS["top_k"], seed: int | None = None) -> tuple[str, float, float, int, int]:
        """한 건 생성. (본문, 프롬프트 처리 ms, 생성 ms, 프롬프트 토큰, 생성 토큰). 앞 요청의 KV는 재사용하지 않는다.

        seed를 주면 그 요청의 샘플링 시드로 쓴다(같은 시드·같은 프롬프트 = 같은 출력).
        """
        lc = self._llama_cpp
        with self.lock:
            self.llm.reset()
            ctx = self.llm._ctx.ctx
            lc.llama_perf_context_reset(ctx)
            out = self.llm.create_completion(prompt, max_tokens=max_tokens, temperature=temperature,
                                             top_p=top_p, top_k=top_k, stop=STOP, seed=seed)
            perf = lc.llama_perf_context(ctx)
        text = out["choices"][0]["text"]
        return text, perf.t_p_eval_ms, perf.t_eval_ms, int(perf.n_p_eval), int(perf.n_eval)


def _raise(msg):
    raise RuntimeError(msg)


def _generation_extension():
    """HF 채팅 템플릿의 `{% generation %}…{% endgeneration %}`(학습용 표시)를 본문 그대로 통과시키는 jinja 확장."""
    from jinja2 import nodes
    from jinja2.ext import Extension

    class GenerationExt(Extension):
        tags = {"generation"}

        def parse(self, parser):
            lineno = next(parser.stream).lineno
            body = parser.parse_statements(("name:endgeneration",), drop_needle=True)
            return nodes.Scope(body, lineno=lineno)

    return GenerationExt


def run_rounds(model: BenchModel, fixtures: list[dict], concurrency: int, repeats: int,
               max_tokens: int, temperature: float, lexicon: Lexicon | None = None,
               top_p: float = GEN_SETTINGS["top_p"], top_k: int = GEN_SETTINGS["top_k"]) -> list[RequestRecord]:
    """FIFO 큐 + 작업자 1개. 라운드마다 요청 concurrency건을 동시에 넣고 모두 끝날 때까지 기다린다.

    검증 어휘 lexicon이 없으면 최소 콘텐츠 묶음(`BUNDLE`)에서 만든다.
    """
    if lexicon is None:
        lexicon = lexicon_from_bundle(json.loads(BUNDLE.read_text(encoding="utf-8")))
    jobs: queue.Queue = queue.Queue()
    records: list[RequestRecord] = []

    def worker():
        while True:
            item = jobs.get()
            if item is None:
                jobs.task_done()
                return
            req, submit_s, job_id = item
            start_s = time.perf_counter()
            try:
                raw, p_ms, g_ms, p_tok, g_tok = model.generate(model.render(build_messages(req)), max_tokens,
                                                               temperature, top_p, top_k)
                v0 = time.perf_counter()
                ok, reason = validate_output(raw, req, lexicon)
                v_ms = (time.perf_counter() - v0) * 1000
            except Exception as exc:   # 실패도 기록하고 다음 요청으로 간다
                log("bench", "error", "생성 실패", job_id=job_id, error_type=type(exc).__name__)
                raw, p_ms, g_ms, p_tok, g_tok, v_ms, ok, reason = "", 0.0, 0.0, 0, 0, 0.0, False, "error"
            end_s = time.perf_counter()
            rec = RequestRecord(job_id, submit_s, start_s, end_s, p_ms, g_ms, p_tok, g_tok, v_ms, ok, reason)
            records.append(rec)
            log("bench", "info", "요청 완료", job_id=job_id, duration_ms=round(rec.total_ms, 1),
                queue_ms=round(rec.queue_ms, 1), prompt_ms=round(p_ms, 1), gen_ms=round(g_ms, 1),
                gen_tokens=g_tok, valid=ok, reason=reason, prompt_version=PROMPT_VERSION)
            jobs.task_done()

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    for r in range(repeats):
        submit_s = time.perf_counter()
        for i, req in enumerate(pick_requests(fixtures, r, concurrency)):
            jobs.put((req, submit_s, f"r{r}_{i}_{req['job_id']}"))
        jobs.join()
    jobs.put(None)
    th.join()
    return records


def write_outputs(name: str, metrics: dict, source: str, out_json: str | None, extra: dict,
                  task: str = TASK) -> Path:
    """결과 기록. 기본은 `write_result`(증빙 결과 JSON), `--out`이면 그 경로에 원본 JSON(다른 기기용)."""
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"task": task, "name": name, "commit": _head(), "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": source, "metrics": metrics, "env": extra}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    sys.path.insert(0, str(ROOT))
    from tools.evidence_result import write_result
    return write_result(task, name, metrics, source)


def _head() -> str:
    """저장소 HEAD 커밋 SHA. git이 없거나 저장소가 아니면 'unknown'."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def import_received(path: str, name: str | None, origin: str) -> Path:
    """다른 기기(VM·노트북 3)에서 `--out`으로 받은 JSON을 증빙 결과 JSON으로 다시 기록한다. 받은 것임을 source에 남긴다."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("task") != TASK:
        raise ValueError(f"task가 {TASK}가 아님: {data.get('task')!r}")
    head = _head()
    if data.get("commit") != head:   # write_result는 지금 HEAD를 적으므로, 측정한 커밋과 같을 때만 받는다
        raise ValueError(f"측정 커밋 {data.get('commit')!r}이 지금 HEAD {head!r}와 다름. 같은 커밋에서 다시 재거나 그 커밋으로 가져온다")
    sys.path.insert(0, str(ROOT))
    from tools.evidence_result import write_result
    source = f"받음({origin}, 측정 {data['generated_at']}): {data['source']}"
    return write_result(TASK, name or data["name"], data["metrics"], source)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """명령행 인자."""
    p = argparse.ArgumentParser(description="E-06-07 LLM 벤치마크")
    p.add_argument("--model", help="GGUF 파일 경로(저장소 밖)")
    p.add_argument("--label", help="모델 짧은 이름(결과 이름에 씀), 예: qwen3_4b")
    p.add_argument("--stage", default="s1", help="측정 단계: s1|s2|s3|colab")
    p.add_argument("--mode", default="win", help="실행 방식: win(Windows 직접) | vm(llm-01)")
    p.add_argument("--threads", type=int, help="스레드 수(기본: 벤치 6, --eval이면 GEN_SETTINGS)")
    p.add_argument("--concurrency", type=int, default=1, help="한 라운드에 동시에 넣는 요청 수")
    p.add_argument("--repeats", type=int, default=5, help="라운드 수(요청 수 = repeats × concurrency)")
    p.add_argument("--max-tokens", type=int, default=GEN_SETTINGS["max_tokens"])
    p.add_argument("--temperature", type=float, default=GEN_SETTINGS["temperature"])
    p.add_argument("--top-p", type=float, default=GEN_SETTINGS["top_p"])
    p.add_argument("--top-k", type=int, default=GEN_SETTINGS["top_k"])
    p.add_argument("--n-ctx", type=int, default=GEN_SETTINGS["n_ctx"])
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--tag", default="", help="결과 이름 꼬리표, 예: devon")
    p.add_argument("--note", default="", help="측정 조건 메모(개발 도구 상태 등). 주소·계정 금지")
    p.add_argument("--fixture", default=str(FIXTURE))
    p.add_argument("--out", help="증빙 대신 이 경로에 JSON을 쓴다(다른 기기에서 실행할 때)")
    p.add_argument("--import-json", help="받은 JSON을 증빙 결과로 기록")
    p.add_argument("--import-name", help="--import-json의 결과 이름(기본: JSON의 name)")
    p.add_argument("--origin", default="VM llm-01", help="--import-json 출처 표시")
    p.add_argument("--eval", help="평가 세트 JSON(E-07-07). 주면 평가 모드: run_eval로 차례로 생성·검증·폴백")
    p.add_argument("--eval-seed", type=int, default=0, help="평가 모드 생성 시드(요청마다 같은 시드)")
    p.add_argument("--eval-n", type=int, help="평가 세트 앞 N건만(기본 전부)")
    p.add_argument("--consistency-n", type=int, default=0, help="일관성: 앞 N건을 --consistency-seeds로 반복(0이면 안 함)")
    p.add_argument("--consistency-seeds", default="1,2,3,4,5")
    p.add_argument("--rejudge", action="store_true",
                   help="--eval과 함께: 모델 없이 저장된 요청 기록을 지금 검증기로 다시 판정(프롬프트가 같을 때만)")
    a = p.parse_args(argv)
    if a.threads is None:
        a.threads = GEN_SETTINGS["n_threads"] if a.eval else 6
    if not a.import_json and not (a.model and a.label):
        p.error("--model과 --label이 필요(또는 --import-json)")
    if a.rejudge and not a.eval:
        p.error("--rejudge는 --eval과 함께 쓴다")
    if a.rejudge and a.out:   # 재판정은 증빙 결과 JSON(이전 측정의 벽시계·커밋·source)을 읽고 다시 쓴다(Codex 11/6)
        p.error("--rejudge는 --out과 함께 쓸 수 없다")
    return a


def eval_name(label: str, mode: str, threads: int, seed: int, n: int, tag: str = "") -> str:
    """평가 모드 결과 이름. 예: eval_kanana2_3b_win_t8_s0_n100."""
    name = f"eval_{label}_{mode}_t{threads}_s{seed}_n{n}"
    return f"{name}_{tag}" if tag else name


def eval_record_log(rec: dict) -> dict:
    """평가 요청 기록 → 로그 필드(생성문 원문은 넣지 않는다. 원문은 out/perf 기록 파일에만)."""
    return {"job_id": rec["case_id"], "seed": rec["seed"], "status": rec["status"], "fallback": rec["fallback"],
            "reason": rec["reasons"][0] if rec["reasons"] else None, "duration_ms": round(rec["total_ms"], 1),
            "prompt_ms": round(rec["prompt_ms"], 1), "gen_ms": round(rec["gen_ms"], 1),
            "gen_tokens": rec["gen_tokens"], "error_type": rec["error_type"], "prompt_version": PROMPT_VERSION}


def _write_records(path: Path, records: list[dict]) -> None:
    """요청 기록(생성문 포함)을 JSON Lines로 쓴다. 위치는 out/perf(커밋하지 않음)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")


def run_eval_mode(a: argparse.Namespace, env: dict) -> int:
    """평가 모드(E-07-07 완료 조건 3): 모델 로드 → 예열 1건 → run_eval → 지표 → 결과 JSON·요청 기록."""
    data = json.loads(Path(a.eval).read_text(encoding="utf-8"))
    lexicon = Lexicon.from_json(data["lexicon"])
    cases = data["cases"][: a.eval_n] if a.eval_n else data["cases"]
    name = eval_name(a.label, a.mode, a.threads, a.eval_seed, len(cases), a.tag)
    log("bench", "info", "평가 시작", run=name, threads=a.threads, n_cases=len(cases), seed=a.eval_seed,
        max_tokens=a.max_tokens, model_version=Path(a.model).name, eval_version=data.get("eval_version"),
        **{k: v for k, v in env.items() if k not in ("os", "model_file", "note")})
    model = BenchModel(a.model, a.threads, a.n_ctx, a.seed)
    log("bench", "info", "모델 로드", run=name, duration_ms=round(model.load_ms, 1))

    def generate(messages: list[dict], *, seed: int) -> dict:
        text, p_ms, g_ms, p_tok, g_tok = model.generate(model.render(messages), a.max_tokens, a.temperature,
                                                        a.top_p, a.top_k, seed)
        return {"text": text, "prompt_ms": p_ms, "gen_ms": g_ms, "prompt_tokens": p_tok, "gen_tokens": g_tok}

    def on_rec(r: dict) -> None:
        log("bench", "error" if r["status"] == "error" else "info", "평가 요청 완료", **eval_record_log(r))

    generate(build_messages(cases[0]["request"]), seed=a.eval_seed)   # 예열 1건(집계 제외)
    t0 = time.perf_counter()
    records = mvp_metrics.run_eval(cases, generate, seed=a.eval_seed, lexicon=lexicon, on_record=on_rec)
    wall_s = time.perf_counter() - t0
    metrics = {**mvp_metrics.success_metrics(records), **mvp_metrics.latency_metrics(records),
               "wall_s": {"value": round(wall_s, 1), "unit": "s"},
               "threads": {"value": a.threads, "unit": ""}, "max_tokens": {"value": a.max_tokens, "unit": "tok"},
               "n_ctx": {"value": a.n_ctx, "unit": "tok"}, "temperature": {"value": a.temperature, "unit": ""},
               "top_p": {"value": a.top_p, "unit": ""}, "top_k": {"value": a.top_k, "unit": ""}}
    rec_dir = ROOT / "out" / "perf" / EVAL_TASK
    _write_records(rec_dir / f"{name}_records.jsonl", records)
    path = write_outputs(name, metrics, _eval_source(a, a.eval_seed, len(cases), env), a.out, env, EVAL_TASK)
    log("bench", "info", "평가 끝", run=name, path=str(path).replace("\\", "/"), records=f"{name}_records.jsonl",
        total_p95_ms=metrics["total_p95_ms"]["value"], success_rate_pct=metrics["success_rate_pct"]["value"],
        duration_ms=round(wall_s * 1000, 1))
    if a.consistency_n:
        seeds = [int(x) for x in a.consistency_seeds.split(",") if x.strip()]
        sub = data["cases"][: a.consistency_n]
        cname = f"consistency_{a.label}_{a.mode}_t{a.threads}_n{len(sub)}_k{len(seeds)}" + (f"_{a.tag}" if a.tag else "")
        cm, runs = mvp_metrics.run_consistency(sub, generate, seeds=seeds, lexicon=lexicon)
        _write_records(rec_dir / f"{cname}_records.jsonl", [r for rs in runs for r in rs])
        csrc = _eval_source(a, None, len(sub), env) + f"; --consistency-n {len(sub)} --consistency-seeds {a.consistency_seeds}"
        cout = consistency_out_path(a.out) if a.out else None   # 평가 결과 파일을 덮어쓰지 않는다
        cpath = write_outputs(cname, cm, csrc, cout, env, EVAL_TASK)
        log("bench", "info", "일관성 끝", run=cname, path=str(cpath).replace("\\", "/"),
            verdict_agree_pct=cm["verdict_agree_pct"]["value"], facts_kept_pct=cm["facts_kept_pct"]["value"])
    return 0


def _read_records(path: Path) -> list[dict]:
    """`_write_records`의 역."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _old_source(task: str, name: str) -> tuple[str, dict, str]:
    """이전 결과 JSON의 (측정 커밋, metrics, source). 없으면 ("unknown", {}, "")."""
    path = ROOT / "docs" / "evidence" / "perf" / task / f"{name}.json"
    if not path.is_file():
        return "unknown", {}, ""
    data = json.loads(path.read_text(encoding="utf-8"))
    src = data.get("source", "")
    prev = re.search(r"생성 기록 ([0-9a-f]+)", src)   # 이미 재판정한 결과면 처음 생성한 커밋을 이어 쓴다
    return (prev.group(1) if prev else data.get("commit", "unknown")), data.get("metrics", {}), src


def _eval_set_changed(path: str, since: str) -> bool:
    """평가 세트 파일이 생성 기록의 커밋 since 이후 바뀌었는가(작업 트리 포함). 커밋을 모르면 True(Codex 11/6)."""
    import subprocess
    if not re.fullmatch(r"[0-9a-f]{7,40}", since):
        return True
    rel = Path(path).resolve().relative_to(ROOT).as_posix() if Path(path).is_absolute() else Path(path).as_posix()
    tracked = subprocess.run(["git", "cat-file", "-e", f"{since}:{rel}"], cwd=ROOT, capture_output=True)
    if tracked.returncode != 0:   # 그 커밋에 없는 파일(추적 안 함 포함)은 내용을 확인할 수 없다
        return True
    r = subprocess.run(["git", "diff", "--quiet", since, "--", rel], cwd=ROOT, capture_output=True)
    return r.returncode != 0


def _check_seeds(records: list[dict], seeds: list[int], what: str) -> None:
    """저장된 기록의 시드가 요청한 시드와 정확히 같은지 본다. 다르면 지표가 조용히 틀리므로 ValueError(Codex 11/6)."""
    got = sorted({r["seed"] for r in records})
    if got != sorted(seeds):
        raise ValueError(f"{what} 기록의 시드 {got}가 요청한 시드 {sorted(seeds)}와 다름. 원래 측정과 같은 인자를 준다")


def run_rejudge_mode(a: argparse.Namespace) -> int:
    """재판정 모드: 저장된 평가·일관성 요청 기록 → 지금 검증기로 다시 판정 → 결과 JSON·요청 기록을 다시 쓴다.

    벽시계(`wall_s`)는 이전 결과 JSON 값을 그대로 옮긴다(생성은 다시 하지 않았으므로). source에 생성 기록의 측정 커밋을 남긴다.
    """
    data = json.loads(Path(a.eval).read_text(encoding="utf-8"))
    lexicon = Lexicon.from_json(data["lexicon"])
    cases = data["cases"][: a.eval_n] if a.eval_n else data["cases"]
    env = {"llama_cpp": "-", "note": a.note}
    name = eval_name(a.label, a.mode, a.threads, a.eval_seed, len(cases), a.tag)
    rec_dir = ROOT / "out" / "perf" / EVAL_TASK
    gen_commit, old, old_src = _old_source(EVAL_TASK, name)
    if _eval_set_changed(a.eval, gen_commit):
        raise ValueError(f"평가 세트 {a.eval}가 생성 기록 커밋 {gen_commit[:7]} 이후 바뀌었거나 커밋을 모름. 다시 측정한다")
    saved = _read_records(rec_dir / f"{name}_records.jsonl")
    _check_seeds(saved, [a.eval_seed], name)
    if len(saved) != len(cases):
        raise ValueError(f"{name} 기록 {len(saved)}건이 사례 {len(cases)}건과 다름")
    records = mvp_metrics.rejudge(saved, cases, lexicon=lexicon)
    metrics = {**mvp_metrics.success_metrics(records), **mvp_metrics.latency_metrics(records),
               "wall_s": old.get("wall_s", {"value": None, "unit": "s"})}
    # 생성 설정은 생성하지 않았으므로 이전 결과 값을 옮긴다(인자를 잘못 줘도 실제 설정이 남게, Codex 11/6)
    cli = {"threads": (a.threads, ""), "max_tokens": (a.max_tokens, "tok"), "n_ctx": (a.n_ctx, "tok"),
           "temperature": (a.temperature, ""), "top_p": (a.top_p, ""), "top_k": (a.top_k, "")}
    for k, (val, unit) in cli.items():
        metrics[k] = old.get(k, {"value": val, "unit": unit})
    _write_records(rec_dir / f"{name}_records.jsonl", records)
    tail = f"; 재판정 --rejudge(생성 기록 {gen_commit[:7]}, 검증기 HEAD)"
    src = old_src.split("; 재판정")[0] or _eval_source(a, a.eval_seed, len(cases), env)   # 원래 측정 명령·전원 메모를 남긴다
    path = write_outputs(name, metrics, src + tail, a.out, env, EVAL_TASK)
    log("bench", "info", "재판정 끝", run=name, path=str(path).replace("\\", "/"),
        success_rate_pct=metrics["success_rate_pct"]["value"], total_p95_ms=metrics["total_p95_ms"]["value"])
    if a.consistency_n:
        seeds = [int(x) for x in a.consistency_seeds.split(",") if x.strip()]
        sub = data["cases"][: a.consistency_n]
        cname = f"consistency_{a.label}_{a.mode}_t{a.threads}_n{len(sub)}_k{len(seeds)}" + (f"_{a.tag}" if a.tag else "")
        cgen, _, cold_src = _old_source(EVAL_TASK, cname)
        if _eval_set_changed(a.eval, cgen):
            raise ValueError(f"평가 세트 {a.eval}가 일관성 기록 커밋 {cgen[:7]} 이후 바뀌었거나 커밋을 모름. 다시 측정한다")
        csaved = _read_records(rec_dir / f"{cname}_records.jsonl")
        _check_seeds(csaved, seeds, cname)
        flat = mvp_metrics.rejudge(csaved, sub, lexicon=lexicon)
        runs = [[r for r in flat if r["seed"] == s] for s in seeds]
        cm = mvp_metrics.consistency_metrics(runs)
        _write_records(rec_dir / f"{cname}_records.jsonl", flat)
        csrc = (cold_src.split("; 재판정")[0]
                or _eval_source(a, None, len(sub), env) + f"; --consistency-n {len(sub)} --consistency-seeds {a.consistency_seeds}")
        csrc += f"; 재판정 --rejudge(생성 기록 {cgen[:7]}, 검증기 HEAD)"
        cout = consistency_out_path(a.out) if a.out else None
        cpath = write_outputs(cname, cm, csrc, cout, env, EVAL_TASK)
        log("bench", "info", "재판정 일관성 끝", run=cname, path=str(cpath).replace("\\", "/"),
            verdict_agree_pct=cm["verdict_agree_pct"]["value"], facts_kept_pct=cm["facts_kept_pct"]["value"])
    return 0


def consistency_out_path(out: str) -> str:
    """`--out` 경로 옆 일관성 결과 경로: a/b.json → a/b_consistency.json."""
    path = Path(out)
    return str(path.with_name(f"{path.stem}_consistency{path.suffix or '.json'}"))


def _eval_source(a: argparse.Namespace, seed: int | None, n: int, env: dict) -> str:
    """평가 결과 JSON의 source(다시 돌릴 명령 + 전원·버전 메모)."""
    parts = ["--model", Path(a.model).name, "--label", a.label, "--mode", a.mode, "--eval", Path(a.eval).as_posix(),
             "--threads", a.threads, "--max-tokens", a.max_tokens, "--temperature", a.temperature, "--top-p", a.top_p,
             "--top-k", a.top_k, "--n-ctx", a.n_ctx]
    if seed is not None:
        parts += ["--eval-seed", seed]
    if a.eval_n:
        parts += ["--eval-n", a.eval_n]
    if a.tag:
        parts += ["--tag", a.tag]
    power = ", ".join(f"{k}={env[k].split('(')[0]}" for k in ("ac_power", "battery_saver", "power_plan", "power_mode")
                      if k in env)
    notes = [x for x in (a.note, power and f"전원 {power}", f"llama_cpp {env['llama_cpp']}",
                         f"prompt {PROMPT_VERSION}", f"n {n}") if x]
    return "python -m llm.bench.run_bench " + " ".join(str(x) for x in parts) + "  # " + "; ".join(notes)


def main(argv: list[str] | None = None) -> int:
    """인자를 읽고 모델 로드 → 예열 1건 → 라운드 측정 → 요약 → 결과 기록 순으로 실행한다(`--eval`이면 평가 모드)."""
    a = parse_args(argv)
    if a.import_json:
        path = import_received(a.import_json, a.import_name, a.origin)
        log("bench", "info", "받은 결과 기록", path=str(path.relative_to(ROOT)).replace("\\", "/"))
        return 0
    if a.rejudge:
        return run_rejudge_mode(a)
    import llama_cpp

    if a.eval:
        env = {"llama_cpp": llama_cpp.__version__, "python": platform.python_version(), "os": platform.platform(),
               "cpu_count": os.cpu_count(), "model_file": Path(a.model).name, "note": a.note,
               "prompt_version": PROMPT_VERSION, **power_status()}
        return run_eval_mode(a, env)

    fixtures = json.loads(Path(a.fixture).read_text(encoding="utf-8"))
    name = result_name(a.stage, a.label, a.mode, a.threads, a.concurrency, a.tag)
    env = {"llama_cpp": llama_cpp.__version__, "python": platform.python_version(), "os": platform.platform(),
           "cpu_count": os.cpu_count(), "model_file": Path(a.model).name, "note": a.note,
           "prompt_version": PROMPT_VERSION, **power_status()}
    log("bench", "info", "측정 시작", run=name, threads=a.threads, concurrency=a.concurrency, repeats=a.repeats,
        max_tokens=a.max_tokens, model_version=Path(a.model).name,
        **{k: v for k, v in env.items() if k not in ("os", "model_file", "note")})
    model = BenchModel(a.model, a.threads, a.n_ctx, a.seed)
    bufs = parse_buffer_sizes(model.load_log)
    log("bench", "info", "모델 로드", run=name, duration_ms=round(model.load_ms, 1), **{f"{k}_mib": v for k, v in bufs.items()})
    if not bufs["weights_total"]:   # 로드 로그를 못 잡았다(llama-cpp-python 로그 콜백 변경 등). 0을 결과로 남기지 않는다
        log("bench", "error", "버퍼 크기를 로드 로그에서 읽지 못함", run=name, error_type="BufferLogMissing")
        return 1
    run_rounds(model, fixtures, 1, 1, a.max_tokens, a.temperature, top_p=a.top_p, top_k=a.top_k)   # 예열 1건(집계 제외)
    records = run_rounds(model, fixtures, a.concurrency, a.repeats, a.max_tokens, a.temperature,
                         top_p=a.top_p, top_k=a.top_k)
    mem = process_memory_mib()
    metrics = summarize(records)
    if metrics["error_count"]["value"]:   # 실패한 생성의 0에 가까운 지연을 결과로 남기지 않는다
        log("bench", "error", "생성 실패가 있어 결과를 쓰지 않음", run=name, error_type="GenerationFailed",
            error_count=metrics["error_count"]["value"])
        return 1
    metrics.update({
        "weights_mib": {"value": bufs["weights"], "unit": "MiB"},
        "weights_repack_mib": {"value": bufs["weights_repack"], "unit": "MiB"},
        "weights_total_mib": {"value": bufs["weights_total"], "unit": "MiB"},
        "kv_mib": {"value": bufs["kv"], "unit": "MiB"},
        "rs_mib": {"value": bufs["rs"], "unit": "MiB"},
        "compute_mib": {"value": bufs["compute"], "unit": "MiB"},
        "load_ms": {"value": round(model.load_ms, 2), "unit": "ms"},
        "threads": {"value": a.threads, "unit": ""},
        "concurrency": {"value": a.concurrency, "unit": ""},
        "repeats": {"value": a.repeats, "unit": "회"},
        "max_tokens": {"value": a.max_tokens, "unit": "tok"},
    })
    if mem:
        metrics["rss_mib"] = {"value": round(mem["rss"], 2), "unit": "MiB"}
        metrics["rss_peak_mib"] = {"value": round(mem["rss_peak"], 2), "unit": "MiB"}
    source = "python -m llm.bench.run_bench " + " ".join(
        f"{x}" for x in ["--model", Path(a.model).name, "--label", a.label, "--stage", a.stage, "--mode", a.mode,
                         "--threads", a.threads, "--concurrency", a.concurrency, "--repeats", a.repeats,
                         "--max-tokens", a.max_tokens, "--temperature", a.temperature, "--n-ctx", a.n_ctx,
                         "--seed", a.seed]
        + (["--tag", a.tag] if a.tag else []))
    power = ", ".join(f"{k}={env[k].split('(')[0]}" for k in ("ac_power", "battery_saver", "power_plan", "power_mode")
                      if k in env)
    notes = [x for x in (a.note, power and f"전원 {power}", f"llama_cpp {env['llama_cpp']}") if x]
    source += "  # " + "; ".join(notes)
    path = write_outputs(name, metrics, source, a.out, env)
    log("bench", "info", "측정 끝", run=name, path=str(path).replace("\\", "/"),
        total_p95_ms=metrics["total_p95_ms"]["value"], gen_tps_p50=metrics.get("gen_tps_p50", {}).get("value"),
        validation_fail_count=metrics["validation_fail_count"]["value"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
