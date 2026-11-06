"""E-06-07 벤치 스크립트의 순수 함수 테스트. llama_cpp 없이 돈다(CI core)."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

from llm.bench import run_bench as rb

FIXTURES = json.loads((rb.FIXTURE).read_text(encoding="utf-8"))

LOAD_LOG = """load_tensors: loading model tensors, this can take a while... (load_mode = mmap)
load_tensors:   CPU_Mapped model buffer size =  2362.55 MiB
load_tensors:   CPU_REPACK model buffer size =  1683.28 MiB
llama_context:        CPU  output buffer size =     0.58 MiB
llama_kv_cache:        CPU KV buffer size =   144.00 MiB
llama_memory_recurrent:        CPU RS buffer size =    25.50 MiB
sched_reserve:        CPU compute buffer size =   306.75 MiB
"""


def rec(job, submit, start, end, gen_ms=1000.0, gen_tok=10, ok=True):
    return rb.RequestRecord(job, submit, start, end, 500.0, gen_ms, 100, gen_tok, 0.1, ok, "ok" if ok else "x")


class Percentile(unittest.TestCase):
    def test_nearest_rank(self):
        vals = list(range(1, 21))          # 1..20
        self.assertEqual(rb.percentile(vals, 50), 10)
        self.assertEqual(rb.percentile(vals, 95), 19)
        self.assertEqual(rb.percentile(vals, 100), 20)
        self.assertEqual(rb.percentile(vals, 0), 1)

    def test_small_sample_p95_is_max(self):
        self.assertEqual(rb.percentile([3.0, 1.0, 2.0], 95), 3.0)

    def test_errors(self):
        with self.assertRaises(ValueError):
            rb.percentile([], 50)
        with self.assertRaises(ValueError):
            rb.percentile([1], 101)


class Validate(unittest.TestCase):
    """벤치의 검증은 E-07-07 검증기(llm.validator)를 부른다."""

    def setUp(self):
        self.req = FIXTURES[1]
        self.lex = rb.lexicon_from_bundle(json.loads(rb.BUNDLE.read_text(encoding="utf-8")))

    def test_ok(self):
        self.assertEqual(rb.validate_output("허허… " + self.req["fact_sentence"], self.req, self.lex), (True, "ok"))

    def test_fact_changed(self):
        changed = self.req["fact_sentence"].replace("한밤", "새벽")
        self.assertEqual(rb.validate_output(changed, self.req, self.lex), (False, "fact_changed"))

    def test_empty_and_long(self):
        self.assertEqual(rb.validate_output("", self.req, self.lex), (False, "format_length"))
        long = self.req["fact_sentence"] + "가" * 200
        self.assertEqual(rb.validate_output(long, self.req, self.lex), (False, "format_length"))

    def test_think_block_removed(self):
        raw = "<think>\n음\n</think>\n\n " + self.req["fact_sentence"]
        self.assertEqual(rb.validate_output(raw, self.req, self.lex), (True, "ok"))

    def test_strip_think(self):
        self.assertEqual(rb.strip_think("<think>\n음\n</think>\n\n 대사 "), "대사")


class Prompt(unittest.TestCase):
    def test_messages_carry_fact(self):
        for req in FIXTURES:
            msgs = rb.build_messages(req)   # llm.prompt.build_messages
            self.assertEqual(msgs[0]["role"], "system")
            self.assertEqual(msgs[-1]["role"], "user")
            self.assertIn(req["fact_sentence"], msgs[-1]["content"])

    def test_eval_name(self):
        self.assertEqual(rb.eval_name("kanana2_3b", "win", 8, 0, 100), "eval_kanana2_3b_win_t8_s0_n100")
        self.assertEqual(rb.eval_name("k", "win", 8, 0, 20, "devoff"), "eval_k_win_t8_s0_n20_devoff")

    def test_rejudge_needs_eval(self):
        a = rb.parse_args(["--model", "m.gguf", "--label", "k", "--eval", "e.json", "--rejudge"])
        self.assertTrue(a.rejudge)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rb.parse_args(["--model", "m.gguf", "--label", "k", "--rejudge"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            rb.parse_args(["--model", "m.gguf", "--label", "k", "--eval", "e.json", "--rejudge", "--out", "o.json"])

    def test_eval_set_changed_unknown_commit(self):
        self.assertTrue(rb._eval_set_changed("fixtures/llm/interrogation_eval.json", "unknown"))
        self.assertTrue(rb._eval_set_changed("out/없는_평가세트.json", "f4ac12b"))   # 그 커밋에 없는 파일

    def test_rejudge_seed_mismatch(self):
        recs = [{"seed": 1}, {"seed": 2}]
        rb._check_seeds(recs, [2, 1], "c")
        with self.assertRaises(ValueError):
            rb._check_seeds(recs, [1, 3], "c")

    def test_consistency_out_path(self):
        self.assertEqual(Path(rb.consistency_out_path("d/r.json")).as_posix(), "d/r_consistency.json")

    def test_eval_record_log_has_no_text(self):
        rec = {"case_id": "ev_001", "seed": 0, "status": "failed", "fallback": True, "reasons": ["fact_changed"],
               "total_ms": 1.0, "prompt_ms": 0.5, "gen_ms": 0.4, "gen_tokens": 3, "error_type": None,
               "raw": "원문", "generated": "원문", "text": "폴백"}
        line = rb.eval_record_log(rec)
        self.assertEqual(line["job_id"], "ev_001")
        self.assertEqual(line["reason"], "fact_changed")
        self.assertNotIn("원문", json.dumps(line, ensure_ascii=False))

    def test_pick_requests_cycles(self):
        ids = [r["job_id"] for r in rb.pick_requests(FIXTURES, 1, 4)]
        self.assertEqual(ids, ["job_002", "job_003", "job_001", "job_002"])

    def test_result_name(self):
        self.assertEqual(rb.result_name("s1", "qwen3_4b", "win", 6, 1), "s1_qwen3_4b_win_t6_c1")
        self.assertEqual(rb.result_name("s3", "q", "win", 6, 2, "devon"), "s3_q_win_t6_c2_devon")


@unittest.skipUnless(importlib.util.find_spec("jinja2"), "jinja2 없음(CI core)")
class GenerationTag(unittest.TestCase):
    def test_generation_tag_passes_body(self):
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        env = ImmutableSandboxedEnvironment(extensions=[rb._generation_extension()])
        tpl = env.from_string("{% for m in messages %}{% generation %}[{{ m }}]{% endgeneration %}{% endfor %}")
        self.assertEqual(tpl.render(messages=["a", "b"]), "[a][b]")


class Buffers(unittest.TestCase):
    def test_parse(self):
        b = rb.parse_buffer_sizes(LOAD_LOG)
        self.assertEqual(b["weights"], 2362.55)
        self.assertEqual(b["weights_repack"], 1683.28)
        self.assertEqual(b["weights_total"], 4045.83)
        self.assertEqual(b["kv"], 144.0)
        self.assertEqual(b["rs"], 25.5)
        self.assertEqual(b["compute"], 306.75)

    def test_empty_log(self):
        self.assertEqual(rb.parse_buffer_sizes("")["weights_total"], 0.0)


class Summarize(unittest.TestCase):
    def test_queue_and_total(self):
        # 동시 2건: 둘 다 0초에 넣고, 1번은 바로 시작해 2초에 끝, 2번은 2초에 시작해 5초에 끝
        recs = [rec("a", 0.0, 0.0, 2.0), rec("b", 0.0, 2.0, 5.0, gen_ms=2000.0, ok=False)]
        m = rb.summarize(recs)
        self.assertEqual(m["total_p95_ms"], {"value": 5000.0, "unit": "ms"})
        self.assertEqual(m["total_p50_ms"]["value"], 2000.0)
        self.assertEqual(m["queue_wait_p95_ms"]["value"], 2000.0)
        self.assertEqual(m["n_requests"], {"value": 2, "unit": "건"})
        self.assertEqual(m["validation_fail_count"]["value"], 1)
        self.assertEqual(m["gen_tps_p50"], {"value": 5.0, "unit": "tok/s"})   # 10/1s, 10/2s → 하위 중앙값 5

    def test_metrics_follow_result_format(self):
        sys.path.insert(0, str(rb.ROOT))
        from tools.evidence_result import build_result
        m = rb.summarize([rec("a", 0.0, 0.1, 1.0)])
        build_result(rb.TASK, "s1_x_win_t6_c1", m, "cmd", "0" * 40, "2026-11-06T00:00:00Z")   # 형식 오류면 예외

    def test_empty(self):
        with self.assertRaises(ValueError):
            rb.summarize([])

    def test_error_count(self):
        bad = rb.RequestRecord("e", 0.0, 0.0, 0.001, 0.0, 0.0, 0, 0, 0.0, False, "error")
        m = rb.summarize([rec("a", 0.0, 0.0, 1.0), bad])
        self.assertEqual(m["error_count"], {"value": 1, "unit": "건"})


class ImportReceived(unittest.TestCase):
    def test_rejects_other_commit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps({"task": rb.TASK, "name": "s2_x_vm_t6_c1", "commit": "0" * 40,
                                     "generated_at": "2026-11-06T00:00:00Z", "source": "cmd",
                                     "metrics": {"n_requests": {"value": 1, "unit": "건"}}}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "HEAD"):
                rb.import_received(str(p), None, "VM llm-01")

    def test_rejects_other_task(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps({"task": "E-06-01"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "task"):
                rb.import_received(str(p), None, "VM llm-01")


if __name__ == "__main__":
    unittest.main()
