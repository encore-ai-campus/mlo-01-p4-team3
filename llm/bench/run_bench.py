"""인프라: LLM 벤치마크(E-06-07). llama-cpp-python으로 추궁 요청의 지연·처리량·메모리를 잰다.

측정 구조(서비스 E-08-09와 같게): 모델 인스턴스 1개 + FIFO 큐 + 작업자 스레드 1개. 한 라운드에 요청 N개를
동시에 큐에 넣고, 요청마다 큐 대기·프롬프트 처리·생성·검증을 나눠 잰다. 전체 지연 = 큐 대기 + 처리 + 검증.

프롬프트와 검증은 **임시**다(`PROMPT_VERSION`). E-07-07(`llm/prompt.py`, `llm/validator.py`)이 실제 함수로 바꾼다.

    python -m llm.bench.run_bench --model <gguf> --label qwen3_4b --stage s1 --threads 6 --concurrency 1 --repeats 5

계층: 위쪽 순수 함수(백분위·검증·로그 해석·요약, 단독 테스트) / 아래쪽 인프라(llama.cpp 호출, 프로세스 메모리,
결과 기록) / `main`(인자 파싱과 흐름 조립). `llama_cpp`·`jinja2`는 함수 안에서 늦게 import한다
(CI `core`는 이 패키지 없이 순수 함수 테스트를 돌린다).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
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

ROOT = Path(__file__).resolve().parents[2]
TASK = "E-06-07"
FIXTURE = ROOT / "fixtures" / "llm" / "interrogation_requests.json"
PROMPT_VERSION = "bench-tmp-1"   # 임시 프롬프트·검증. E-07-07이 교체
MAX_CHARS = 200                  # 임시 검증: 출력 길이 상한(글자)
STOP = ["<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<end_of_turn>"]
REACTION_KO = {
    "evade": "말을 돌리며 얼버무린다",
    "confess": "망설이다 털어놓는다",
    "counter": "되레 상대에게 따져 묻는다",
}
_BUFFER_RE = re.compile(r"(\S+)\s+(model|KV|RS|compute|output) buffer size\s*=\s*([0-9]+(?:\.[0-9]+)?) MiB")
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


# ---------------------------------------------------------------- 순수 함수

def percentile(values: list[float], q: float) -> float:
    """최근접 순위(nearest-rank) 백분위. q는 0~100. 표본이 없으면 ValueError."""
    if not values:
        raise ValueError("표본이 없음")
    if not 0 <= q <= 100:
        raise ValueError(f"q는 0~100: {q}")
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def build_messages(req: dict) -> list[dict]:
    """추궁 요청 1건을 채팅 메시지로 만든다(임시 프롬프트)."""
    names = ", ".join(req["allowed_names"])
    system = (
        f"너는 조선 시대 마을 사람이다. 인물: {req['persona']}.\n"
        "규칙:\n"
        "1. 아래 '사실 문장'을 한 글자도 바꾸지 말고 그대로 넣는다.\n"
        f"2. 태도: {REACTION_KO.get(req['reaction'], req['reaction'])}.\n"
        f"3. 사람 이름은 {names}만 쓴다. 새 인물·장소·시간을 지어내지 않는다.\n"
        "4. 대사만 1~2문장으로 쓴다. 설명·따옴표·괄호를 붙이지 않는다."
    )
    user = f"사실 문장: {req['fact_sentence']}\n추궁에 대한 대사:"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def strip_think(text: str) -> str:
    """사고 블록(`<think>…</think>`)과 앞뒤 공백을 지운다."""
    return _THINK_RE.sub("", text).strip()


def validate_output(text: str, req: dict) -> tuple[bool, str]:
    """임시 최소 검증: 비어 있지 않고, 사실 문장을 그대로 포함하고, MAX_CHARS 이하. (통과, 사유)."""
    if not text:
        return False, "empty"
    if req["fact_sentence"] not in text:
        return False, "fact_missing"
    if len(text) > MAX_CHARS:
        return False, "too_long"
    return True, "ok"


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

    def generate(self, prompt: str, max_tokens: int, temperature: float) -> tuple[str, float, float, int, int]:
        """한 건 생성. (본문, 프롬프트 처리 ms, 생성 ms, 프롬프트 토큰, 생성 토큰). 앞 요청의 KV는 재사용하지 않는다."""
        lc = self._llama_cpp
        with self.lock:
            self.llm.reset()
            ctx = self.llm._ctx.ctx
            lc.llama_perf_context_reset(ctx)
            out = self.llm.create_completion(prompt, max_tokens=max_tokens, temperature=temperature,
                                             top_p=0.8, top_k=20, stop=STOP)
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
               max_tokens: int, temperature: float) -> list[RequestRecord]:
    """FIFO 큐 + 작업자 1개. 라운드마다 요청 concurrency건을 동시에 넣고 모두 끝날 때까지 기다린다."""
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
                                                               temperature)
                v0 = time.perf_counter()
                ok, reason = validate_output(strip_think(raw), req)
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


def write_outputs(name: str, metrics: dict, source: str, out_json: str | None, extra: dict) -> Path:
    """결과 기록. 기본은 `write_result`(증빙 결과 JSON), `--out`이면 그 경로에 원본 JSON(다른 기기용)."""
    if out_json:
        path = Path(out_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"task": TASK, "name": name, "commit": _head(), "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": source, "metrics": metrics, "env": extra}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    sys.path.insert(0, str(ROOT))
    from tools.evidence_result import write_result
    return write_result(TASK, name, metrics, source)


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
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("--concurrency", type=int, default=1, help="한 라운드에 동시에 넣는 요청 수")
    p.add_argument("--repeats", type=int, default=5, help="라운드 수(요청 수 = repeats × concurrency)")
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--n-ctx", type=int, default=1024)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--tag", default="", help="결과 이름 꼬리표, 예: devon")
    p.add_argument("--note", default="", help="측정 조건 메모(개발 도구 상태 등). 주소·계정 금지")
    p.add_argument("--fixture", default=str(FIXTURE))
    p.add_argument("--out", help="증빙 대신 이 경로에 JSON을 쓴다(다른 기기에서 실행할 때)")
    p.add_argument("--import-json", help="받은 JSON을 증빙 결과로 기록")
    p.add_argument("--import-name", help="--import-json의 결과 이름(기본: JSON의 name)")
    p.add_argument("--origin", default="VM llm-01", help="--import-json 출처 표시")
    a = p.parse_args(argv)
    if not a.import_json and not (a.model and a.label):
        p.error("--model과 --label이 필요(또는 --import-json)")
    return a


def main(argv: list[str] | None = None) -> int:
    """인자를 읽고 모델 로드 → 예열 1건 → 라운드 측정 → 요약 → 결과 기록 순으로 실행한다."""
    a = parse_args(argv)
    if a.import_json:
        path = import_received(a.import_json, a.import_name, a.origin)
        log("bench", "info", "받은 결과 기록", path=str(path.relative_to(ROOT)).replace("\\", "/"))
        return 0
    import llama_cpp

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
    run_rounds(model, fixtures, 1, 1, a.max_tokens, a.temperature)   # 예열 1건(집계 제외)
    records = run_rounds(model, fixtures, a.concurrency, a.repeats, a.max_tokens, a.temperature)
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
