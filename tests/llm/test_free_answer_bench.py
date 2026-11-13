"""E-08-13 인프라 테스트: 가짜 스트리밍 서버로 부하 클라이언트, 가짜 runpodctl로 watch_pods·run_pod.sh.

실제 RunPod·Tailscale·GPU는 부르지 않는다.
"""
from __future__ import annotations

import io
import contextlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from llm.bench import free_answer_load as fl
from llm.bench import watch_pods as wp
from tests.llm.test_free_answer import ITEM, LEX

ROOT = Path(__file__).resolve().parents[2]


# --- 가짜 스트리밍 서버 ---
class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # 조용히
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.auth.append(self.headers.get("Authorization"))
        kind, payload = self.server.script.pop(0) if self.server.script else self.server.default
        if kind == "slowhead":   # 응답 머리를 늦게 보냄
            time.sleep(0.4)
            kind = "ok"
        if kind == "http500":
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for piece in payload:
                if isinstance(piece, float):
                    time.sleep(piece)
                    continue
                data = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            if kind == "ok":
                self.wfile.write(b"data: [DONE]\n\n")
            # "disconnect"는 끝 표시 없이 닫는다
        except OSError:
            pass   # 클라이언트가 먼저 끊음(부분 답·시간 초과 경로)


class FakeServer:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        self.httpd.script, self.httpd.auth, self.httpd.default = [], [], ("http500", None)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def push(self, *responses):
        self.httpd.script.extend(responses)


def chunks(text, n=4):
    return [text[i:i + n] for i in range(0, len(text), n)]


GOOD = "근거: f3,f4\n초저녁엔 주막에 있었소. 한밤엔 집에 있었소."
BAD_FIRST = "근거: f1,f2\n대장장이는 주막에 있었소."
BAD_SECOND = "근거: f1\n한밤엔 우물가에서 대장장이를 봤소. 대장장이가 범인이오."


class LoadClientTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer().__enter__()
        self.client = fl.OpenAIStream(self.srv.url, "fake", key="k-test", timeout=5)

    def tearDown(self):
        self.srv.__exit__()

    def run1(self, timeout=fl.TIMEOUT_S):
        return fl.run_request(self.client, ITEM, LEX, timeout=timeout)

    def test_ok(self):
        self.srv.push(("ok", chunks(GOOD)))
        r = self.run1()
        self.assertEqual((r.outcome, r.attempts, len(r.published)), ("ok", 1, 2))
        self.assertTrue(0 <= r.first_token <= r.first_display <= r.complete < 5)
        self.assertEqual(self.srv.httpd.auth, ["Bearer k-test"])

    def test_first_display_before_stream_end(self):
        self.srv.push(("ok", [*chunks("근거: f3\n초저녁엔 주막에 있었소. "), 0.4, "그뿐이오."]))
        r = self.run1()
        self.assertLess(r.first_display, 0.35)   # 첫 문장은 0.4초 쉬기 전에 공개
        self.assertEqual(r.outcome, "partial")    # 둘째 문장은 인용 요소 없음 → 버림

    def test_regenerate_then_ok(self):
        self.srv.push(("ok", chunks(BAD_FIRST)), ("ok", chunks(GOOD)))
        r = self.run1()
        self.assertEqual((r.outcome, r.attempts), ("regen_ok", 2))
        self.assertIn("pair_binding", r.reasons)

    def test_fallback_after_two_failures(self):
        self.srv.push(("ok", chunks(BAD_FIRST)), ("ok", ["근거: f9\n집에 있었소."]))
        r = self.run1()
        self.assertEqual((r.outcome, r.published), ("fallback", [ITEM.facts[0].sentence]))
        self.assertTrue(math.isfinite(r.first_display) and r.first_display == r.complete)

    def test_partial(self):
        self.srv.push(("ok", chunks(BAD_SECOND)))
        r = self.run1()
        self.assertEqual((r.outcome, r.published), ("partial", ["한밤엔 우물가에서 대장장이를 봤소."]))

    def test_disconnect_is_infinite(self):
        self.srv.push(("disconnect", chunks("근거: f3\n초저녁엔 주막에 있었소. 한밤")))
        r = self.run1()
        self.assertEqual(r.outcome, "error")
        self.assertTrue(math.isinf(r.complete))
        self.assertTrue(math.isfinite(r.first_display))   # 끊기기 전에 첫 문장은 공개됨
        self.assertEqual(r.published, ["초저녁엔 주막에 있었소."])   # 공개한 문장은 기록에 남는다(Codex 11/8)

    def test_http_error_and_timeout(self):
        self.srv.push(("http500", None))
        r = self.run1()
        self.assertEqual(r.outcome, "error")
        self.assertTrue(math.isinf(r.first_display) and math.isinf(r.complete))
        self.srv.push(("ok", ["근거: f3\n", 0.3, "초저녁엔 주막에 있었소."]))
        self.assertEqual(self.run1(timeout=0.2).outcome, "timeout")

    def test_deadline_cuts_stalled_and_dripping_streams(self):
        # 서버가 멈춤(2초 무응답) / 조각을 0.1초마다 조금씩 계속 보냄: 둘 다 남은 시간(0.5초) 근처에서 끊는다(Codex 11/8)
        self.srv.push(("ok", ["근거: f3\n", 2.0, "초저녁엔 주막에 있었소."]))
        t0 = time.monotonic()
        r = self.run1(timeout=0.5)
        self.assertEqual(r.outcome, "timeout")
        self.assertLess(time.monotonic() - t0, 1.5)
        drip = ["근거: f3\n"] + [p for _ in range(30) for p in (0.1, "가")]
        self.srv.push(("ok", drip))
        t0 = time.monotonic()
        self.assertEqual(self.run1(timeout=0.5).outcome, "timeout")
        self.assertLess(time.monotonic() - t0, 1.5)
        # 머리를 0.4초 늦게 보내도 마감은 연결 전부터 센다(남은 0.1초만 더 기다림, Codex 재검토 11/8)
        self.srv.push(("slowhead", drip))
        t0 = time.monotonic()
        self.assertEqual(self.run1(timeout=0.5).outcome, "timeout")
        self.assertLess(time.monotonic() - t0, 0.85)

    def test_closed_loop_and_summary(self):
        self.srv.httpd.default = ("ok", chunks(GOOD, 8))   # 대본이 비면 늘 정상 답
        cfg = fl.LoadConfig(sessions=3, think_lo=0.0, think_hi=0.01, warmup_s=0.0, duration_s=0.3, seed=1, timeout=5)
        recs, samples, wall = fl.run_closed_loop(self.client, [("x", ITEM)], LEX, cfg, sample_every=0.05)
        self.assertGreater(len(recs), 3)
        self.assertTrue(samples and all(0 <= s["in_flight"] <= 3 for s in samples))
        s = fl.summarize(recs, wall)
        self.assertEqual(s["error_rate"], 0)
        self.assertIsNotNone(s["first_display_s"]["p95"])


class PureTest(unittest.TestCase):
    def test_percentile_with_inf(self):
        self.assertEqual(fl.percentile([1, 2, 3, 4], 50), 2)
        self.assertTrue(math.isinf(fl.percentile([1.0] * 19 + [math.inf] * 2, 95)))
        self.assertEqual(fl.percentile([1.0] * 20 + [math.inf], 95), 1.0)

    def test_summary_inf_is_null(self):
        r = fl.Record(0, "x", 0.0)
        s = fl.summarize([r], 1.0)
        self.assertIsNone(s["complete_s"]["p95"])
        self.assertEqual(s["error_rate"], 1.0)

    def test_eval_mix_includes_chat_types(self):
        # E-11-12: 부하 클라이언트는 평가 세트에서 고르게 뽑으므로 자유 대화 유형이 섞이고, 결과에 유형별 수가 남는다
        qtypes = fl.item_qtypes(ROOT / "fixtures" / "llm" / "free_answer_eval.json")
        self.assertTrue({"intro", "personality", "impression", "incident", "smalltalk"} <= set(qtypes.values()))
        recs = [fl.Record(0, "fa_001", 0.0, outcome="ok"), fl.Record(0, "fa_082", 0.0, outcome="fallback"),
                fl.Record(0, "fa_083", 0.0, outcome="ok")]
        mix = fl.qtype_mix(recs, qtypes)
        self.assertEqual(mix["intro"], {"n": 2, "fallback": 1})
        self.assertEqual(mix[qtypes["fa_001"]], {"n": 1, "fallback": 0})

    def test_parse_sse(self):
        self.assertEqual(fl.parse_sse_line('data: {"choices":[{"delta":{"content":"가"}}]}'), "가")
        self.assertEqual(fl.parse_sse_line("data: [DONE]"), "")
        self.assertIsNone(fl.parse_sse_line(": keepalive"))
        self.assertIsNone(fl.parse_sse_line('data: {"choices":[{"delta":{"role":"assistant"}}]}'))


# --- watch_pods ---
FAKE_CTL = r'''
import json, os, sys
state_path = os.environ["FAKE_STATE"]
st = json.load(open(state_path, encoding="utf-8"))
args = sys.argv[1:]
with open(state_path + ".log", "a", encoding="utf-8") as f:
    f.write(" ".join(args) + "\n")
if args[:2] == ["pod", "list"]:
    print(json.dumps(st["pods"]))
elif args[:2] in (["pod", "delete"], ["remove", "pod"]):
    if st.get("fail_delete", 0) > 0:
        st["fail_delete"] -= 1
        json.dump(st, open(state_path, "w", encoding="utf-8"))
        sys.exit(1)
    st["pods"] = [p for p in st["pods"] if p["id"] != args[2]]
    json.dump(st, open(state_path, "w", encoding="utf-8"))
else:
    sys.exit(2)
'''


FAKE_ENV_CTL = r'''
import json, os
with open(ENV_LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps([os.environ.get("RUNPOD_API_KEY"),
                        sorted(k for k in os.environ if k.startswith("PALDO_RUNPOD_KEY_"))]) + "\n")
print("[]")
'''


class WatchPodsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.state, self.ledger = d / "state.json", d / "ledger.json"
        fake = d / "fake_runpodctl.py"
        fake.write_text(FAKE_CTL, encoding="utf-8")
        self.ctl = wp.RunpodCtl(shlex.join([sys.executable, str(fake)]))
        os.environ["FAKE_STATE"] = str(self.state)

    def tearDown(self):
        os.environ.pop("FAKE_STATE", None)
        self.tmp.cleanup()

    def set_pods(self, pods, fail_delete=0):
        self.state.write_text(json.dumps({"pods": pods, "fail_delete": fail_delete}), encoding="utf-8")

    def calls(self):
        p = Path(str(self.state) + ".log")
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def test_cost_80pct_deletes_prefix_pods_only(self):
        self.set_pods([{"id": "a", "name": "paldo-e1112-s1-e-1", "costPerHr": 0.89},
                       {"id": "b", "name": "paldo-e1112-s1-e-2", "costPerHr": 0.89},
                       {"id": "z", "name": "my-other-pod", "costPerHr": 2.0}])
        t = [0.0]
        wp.watch(self.ctl, self.ledger, "E", once=True, clock=lambda: t[0], sleep=lambda s: None)
        self.assertNotIn("pod delete a", self.calls())
        t[0] = 2.3 * 3600   # 2개 × 0.89 × 2.3h = 4.09 ≥ 4.0
        led = wp.watch(self.ctl, self.ledger, "E", once=True, clock=lambda: t[0], sleep=lambda s: None)
        self.assertIn("pod delete a", self.calls())
        self.assertIn("pod delete b", self.calls())
        self.assertNotIn("pod delete z", self.calls())
        self.assertNotIn("z", led)

    def test_max_minutes_plus_grace(self):
        self.set_pods([{"id": "a", "name": "paldo-e1112-s0-e-1", "costPerHr": 0.89}])
        t = [0.0]
        run = lambda: wp.watch(self.ctl, self.ledger, "E", once=True, clock=lambda: t[0], sleep=lambda s: None)  # noqa: E731
        run()
        t[0] = (20 + 10) * 60 - 1
        run()
        self.assertNotIn("pod delete a", self.calls())
        t[0] = (20 + 10) * 60
        run()
        self.assertIn("pod delete a", self.calls())

    def test_delete_retries_three_times(self):
        self.set_pods([{"id": "a", "name": "paldo-e1112-s0-e-1", "costPerHr": 0.89}], fail_delete=2)
        self.assertTrue(self.ctl.delete_pod("a", sleep=lambda s: None))
        self.assertEqual(self.calls().count("pod delete a"), 3)
        self.set_pods([{"id": "b", "name": "paldo-e1112-s0-e-1", "costPerHr": 0.89}], fail_delete=5)
        self.assertFalse(self.ctl.delete_pod("b", sleep=lambda s: None))

    def test_delete_cmd_override(self):
        """설치된 runpodctl의 삭제 하위 명령이 다르면 `--delete-cmd`로 바꾼다(11/11, 0단계 확인 대비)."""
        ctl = wp.RunpodCtl(shlex.join(self.ctl.bin), delete_cmd="remove pod")
        self.set_pods([{"id": "a", "name": "paldo-e1112-s0-e-1", "costPerHr": 0.89}])
        self.assertTrue(ctl.delete_pod("a", sleep=lambda s: None))
        self.assertEqual(self.calls(), ["remove pod a"])

    def test_gone_pod_stays_in_ledger(self):
        led = {}
        wp.update_ledger(led, [{"id": "a", "name": "paldo-e1112-s1-e-1", "cost_per_hr": 1.0}], 0)
        wp.update_ledger(led, [{"id": "a", "name": "paldo-e1112-s1-e-1", "cost_per_hr": 1.0}], 1800)
        wp.update_ledger(led, [], 3600)
        self.assertTrue(led["a"]["gone"])
        self.assertAlmostEqual(wp.total_cost(led, 60), 1.0)   # 사라진 것을 본 3600초까지 센다(Codex 11/8)
        led2 = {}
        wp.update_ledger(led2, [{"id": "b", "name": "paldo-e1112-s1-e-1", "cost_per_hr": 1.0}], 0)
        wp.update_ledger(led2, [], 60)   # 한 번 보이고 다음 관측 전에 사라진 Pod도 0원이 아니다
        self.assertAlmostEqual(wp.total_cost(led2), 60 / 3600)

    def test_delete_timeout_does_not_stop_watch(self):
        from unittest import mock

        def hang(cmd, **kw):
            if "delete" in cmd:
                raise subprocess.TimeoutExpired(cmd, 60)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                [{"id": "a", "name": "paldo-e1112-s0-e-1", "costPerHr": 0.89}]), "")
        t = [0.0]
        with mock.patch.object(wp.subprocess, "run", side_effect=hang):
            self.assertFalse(self.ctl.delete_pod("a", sleep=lambda s: None))
            wp.watch(self.ctl, self.ledger, "E", once=True, clock=lambda: t[0], sleep=lambda s: None)
            t[0] = 31 * 60
            led = wp.watch(self.ctl, self.ledger, "E", max_loops=2, clock=lambda: t[0], sleep=lambda s: None)
        self.assertTrue(led["a"]["delete_requested"])

    def test_confirm_required(self):
        self.set_pods([])
        args = ["--account", "E", "--once", "--ledger", str(self.ledger),
                "--runpodctl", shlex.join([sys.executable, str(Path(self.tmp.name) / "fake_runpodctl.py")])]
        self.assertEqual(wp.main(args, input_fn=lambda _: "no"), 2)
        self.assertFalse(self.ledger.exists())
        self.assertEqual(wp.main(args, input_fn=lambda _: "yes"), 0)
        self.assertTrue(self.ledger.exists())

    def test_account_guard_counts_and_deletes_own_account_only(self):
        # 11/8 사용자 결정: E가 네 계정을 한 노트북에서 감시한다. 키 격리가 틀려 다른 계정 Pod가 보여도 세지도 지우지도 않는다.
        self.set_pods([{"id": "b1", "name": "paldo-e1112-s1-b-1", "costPerHr": 0.89},
                       {"id": "c1", "name": "paldo-e1112-s1-c-1", "costPerHr": 1.09},
                       {"id": "x1", "name": "paldo-e1112-s1-1", "costPerHr": 0.89}])
        t = [0.0]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.watch(self.ctl, self.ledger, "B", once=True, clock=lambda: t[0], sleep=lambda s: None)
            t[0] = 4.6 * 3600   # b1만: 0.89 × 4.6h = 4.09 ≥ 4.0(c1·x1을 셌다면 더 일찍 닿음)
            led = wp.watch(self.ctl, self.ledger, "B", once=True, clock=lambda: t[0], sleep=lambda s: None)
        self.assertEqual(set(led), {"b1"})
        self.assertIn("pod delete b1", self.calls())
        self.assertNotIn("pod delete c1", self.calls())
        self.assertNotIn("pod delete x1", self.calls())
        self.assertIn("paldo-e1112-s1-c-1", out.getvalue())   # 매 주기 오류로 알림
        self.assertEqual(wp.account_of("paldo-e1112-s2-d-1"), "D")
        self.assertIsNone(wp.account_of("paldo-e1112-s2-z-1"))
        self.assertEqual(wp.account_of("paldo-e1112-s3-b-1"), "B")   # 단계를 못 읽어도 계정은 읽고 가장 긴 상한(Codex 11/8)
        self.assertEqual(wp.max_minutes("paldo-e1112-s3-b-1"), 90)
        # 다른 계정 항목이 든 장부를 넘기면 시작하지 않고 파일도 그대로 둔다(Codex 11/8)
        self.ledger.write_text(json.dumps({"old_c": {"name": "paldo-e1112-s1-c-9", "first_seen": 0, "last_seen": 36000,
                                                     "cost_per_hr": 1.09, "gone": True, "delete_requested": False}}),
                               encoding="utf-8")
        before = self.ledger.read_text(encoding="utf-8")
        self.set_pods([{"id": "b2", "name": "paldo-e1112-s1-b-2", "costPerHr": 0.89}])
        with self.assertRaises(ValueError):   # 다른 계정 장부를 덮어쓰지 않고 거부(Codex 11/8 재리뷰)
            wp.watch(self.ctl, self.ledger, "B", once=True, clock=lambda: 0.0, sleep=lambda s: None)
        args = ["--account", "B", "--once", "--ledger", str(self.ledger),
                "--runpodctl", shlex.join([sys.executable, str(Path(self.tmp.name) / "fake_runpodctl.py")])]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wp.main(args, input_fn=lambda _: "yes"), 2)
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), before)
        self.assertNotIn("pod delete b2", self.calls())
        led2 = {}
        _, foreign = wp.update_ledger(led2, [{"id": "c1", "name": "paldo-e1112-s1-c-1", "cost_per_hr": 1.0}], 0, "B")
        self.assertEqual((led2, foreign), ({}, ["paldo-e1112-s1-c-1"]))

    def test_key_env_isolated_and_not_printed(self):
        env_log = Path(self.tmp.name) / "env.log"
        fake = Path(self.tmp.name) / "fake_env_ctl.py"
        fake.write_text(FAKE_ENV_CTL.replace("ENV_LOG", repr(str(env_log))), encoding="utf-8")
        environ = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                   "PALDO_RUNPOD_KEY_B": "<fake-key-b>", "PALDO_RUNPOD_KEY_C": "<fake-key-c>", "RUNPOD_API_KEY": "<stale>"}
        env = wp.ctl_env("PALDO_RUNPOD_KEY_B", environ)
        ctl = wp.RunpodCtl(shlex.join([sys.executable, str(fake)]), env=env)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wp.watch(ctl, self.ledger, "B", once=True, clock=lambda: 0.0, sleep=lambda s: None)
        self.assertEqual(json.loads(env_log.read_text(encoding="utf-8").splitlines()[0]), ["<fake-key-b>", []])
        self.assertNotIn("fake-key", out.getvalue())
        self.assertIsNone(wp.ctl_env(None, environ))
        with self.assertRaises(ValueError):
            wp.ctl_env("PALDO_RUNPOD_KEY_D", environ)
        args = ["--account", "D", "--once", "--key-env", "PALDO_RUNPOD_KEY_D_MISSING", "--ledger", str(self.ledger)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wp.main(args, input_fn=lambda _: "yes"), 2)

    def test_limits_and_stage(self):
        self.assertEqual(wp.LIMITS, {"E": 5.0, "B": 5.0, "C": 4.5, "D": 4.5})
        self.assertEqual(wp.max_minutes("paldo-e1112-s2-c-1"), 90)
        self.assertEqual(wp.max_minutes("paldo-e1112-x"), 90)
        with self.assertRaises(ValueError):
            wp.parse_pod_list('[{"id": "a"}]')


# --- run_pod.sh ---
def _bash() -> str | None:
    cands = [r"C:\Program Files\Git\bin\bash.exe"] if os.name == "nt" else []
    cands.append(shutil.which("bash") or "")
    for c in cands:
        if c and Path(c).exists() and "system32" not in c.lower():
            return c
    return None


@unittest.skipUnless(_bash(), "bash 없음")
class RunPodShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        fake = self.d / "fake_runpodctl"
        fake.write_text('#!/usr/bin/env bash\necho "$@" >> "$FAKE_LOG"\n'
                        'n=$(cat "$FAKE_FAILS" 2>/dev/null || echo 0)\n'
                        'if [ "$n" -gt 0 ]; then echo $((n-1)) > "$FAKE_FAILS"; exit 1; fi\nexit 0\n',
                        encoding="utf-8", newline="\n")
        self.fake, self.log = fake, self.d / "calls.log"

    def tearDown(self):
        self.tmp.cleanup()

    def run_sh(self, env, *cmd, fails=0):
        (self.d / "fails").write_text(str(fails))
        full = {**os.environ, "RUNPODCTL": self.fake.as_posix(), "FAKE_LOG": self.log.as_posix(),
                "FAKE_FAILS": (self.d / "fails").as_posix(), "RUNPOD_POD_ID": "pod-test", "TICK_SECONDS": "1",
                "RETRY_SLEEP": "0", **env}
        script = (ROOT / "llm" / "bench" / "run_pod.sh").as_posix()
        return subprocess.run([_bash(), script, *cmd], env=full, capture_output=True, text=True, encoding="utf-8",
                              timeout=60)

    def calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_rejects_over_cap(self):
        for stage, m in (("s0", "21"), ("s1", "91"), ("s2", "100"), ("s1", "abc"), ("s9", "5")):
            r = self.run_sh({"STAGE": stage, "MAX_MINUTES": m}, "true")
            self.assertEqual(r.returncode, 2, (stage, m))
        self.assertEqual(self.calls(), [])

    def test_export_failure_is_reported_and_delete_still_runs(self):
        r = self.run_sh({"STAGE": "s0", "MAX_MINUTES": "20", "EXPORT_CMD": "exit 7"}, "true")
        self.assertIn("반출 실패", r.stdout)   # 파이프 뒤 redact의 성공에 가려지지 않는다(Codex 11/8)
        self.assertNotIn("반출 완료", r.stdout)
        self.assertEqual((r.returncode, self.calls()), (0, ["pod delete pod-test"]))

    def test_job_done_then_export_and_delete(self):
        out = self.d / "exported"
        r = self.run_sh({"STAGE": "s0", "MAX_MINUTES": "20", "EXPORT_CMD": f"echo done > '{out.as_posix()}'"},
                        "echo", "authkey=SECRET123")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(out.exists())
        self.assertEqual(self.calls(), ["pod delete pod-test"])
        self.assertNotIn("SECRET123", r.stdout + r.stderr)

    def test_timeout_kills_job(self):
        t0 = time.monotonic()
        r = self.run_sh({"STAGE": "s0", "MAX_MINUTES": "2"}, "sleep", "30")
        self.assertLess(time.monotonic() - t0, 20)
        self.assertIn("시간 초과", r.stdout)
        self.assertEqual(self.calls(), ["pod delete pod-test"])

    def test_delete_subcommand_override(self):
        """RUNPODCTL_DELETE로 삭제 하위 명령을 바꾼다(예: "remove pod")."""
        r = self.run_sh({"STAGE": "s0", "MAX_MINUTES": "20", "RUNPODCTL_DELETE": "remove pod"}, "true")
        self.assertEqual((r.returncode, self.calls()), (0, ["remove pod pod-test"]))

    def test_delete_retry_and_give_up(self):
        r = self.run_sh({"STAGE": "s1", "MAX_MINUTES": "90"}, "true", fails=2)
        self.assertEqual((r.returncode, len(self.calls())), (0, 3))
        self.log.unlink()
        r = self.run_sh({"STAGE": "s1", "MAX_MINUTES": "90"}, "true", fails=5)
        self.assertEqual((r.returncode, len(self.calls())), (3, 3))


if __name__ == "__main__":
    unittest.main()
