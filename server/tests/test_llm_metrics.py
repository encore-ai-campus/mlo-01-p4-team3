"""LLM 작업 프로세스 지표(E-12-01): 결과 분류·히스토그램·렌더 형식·파일 쓰기·작업 프로세스 연결."""
import os
import re
import tempfile
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from server.app.llm_metrics import (BUCKETS, Histogram, WorkerMetrics, metric_path, npc_outcome, review_outcome)
from server.app.npc_lines import JobOutcome
from server.app.review_narration import ProcessOutcome
from server.infra.metric_file import MetricFile

LINE = re.compile(r'^[a-z_]+(\{[a-z_]+="[^"]*"(,[a-z_]+="[^"]*")*\})? -?[0-9.e+]+$')


class OutcomeMappingTest(SimpleTestCase):
    """작업 결과 → outcome 대응."""
    def test_npc(self):
        """test_npc."""
        self.assertEqual(npc_outcome("done", None), "ok")
        self.assertEqual(npc_outcome("discarded", "late"), "late")
        self.assertEqual(npc_outcome("failed", "timeout"), "late")
        self.assertEqual(npc_outcome("failed", "error"), "error")
        self.assertEqual(npc_outcome("rejected", "lexicon"), "rejected")
        self.assertIsNone(npc_outcome("closed", "mode_off"))

    def test_review(self):
        """test_review."""
        self.assertEqual(review_outcome("done", True), "ok")
        self.assertEqual(review_outcome("done", False), "late")
        self.assertEqual(review_outcome("timeout", False), "late")
        self.assertEqual(review_outcome("failed", True), "error")


class HistogramRenderTest(SimpleTestCase):
    """히스토그램 누적과 텍스트 형식."""
    def test_cumulative_buckets(self):
        """test_cumulative_buckets."""
        h = Histogram()
        for v in (0.5, 3, 25, 500, -1):
            h.observe(v)
        self.assertEqual(h.total, 5)
        self.assertEqual(dict(zip(BUCKETS, h.counts)),
                         {1.0: 2, 2.0: 2, 5.0: 3, 10.0: 3, 20.0: 3, 30.0: 4, 60.0: 4, 120.0: 4})
        self.assertAlmostEqual(h.sum, 528.5)

    def test_render_format_and_contract_names(self):
        """test_render_format_and_contract_names."""
        m = WorkerMetrics("npc_line")
        m.record("ok", 1.5, 4.0)
        m.record("late", 70.0, 30.0)
        m.record(None, 1.0, 1.0)          # 세지 않는 작업
        m.queue_depth = 3
        text = m.render(1700000000.0)
        for line in text.splitlines():
            if not line.startswith("#"):
                self.assertRegex(line, LINE)
        self.assertIn('paldo_llm_jobs_total{worker="npc_line",outcome="ok"} 1', text)
        self.assertIn('paldo_llm_jobs_total{worker="npc_line",outcome="rejected"} 0', text)
        self.assertIn('paldo_llm_queue_wait_seconds_bucket{worker="npc_line",le="2.0"} 1', text)
        self.assertIn('paldo_llm_queue_wait_seconds_bucket{worker="npc_line",le="+Inf"} 2', text)
        self.assertIn('paldo_llm_generate_seconds_count{worker="npc_line"} 2', text)
        self.assertIn('paldo_llm_queue_depth{worker="npc_line"} 3', text)
        self.assertIn('paldo_llm_worker_last_update_seconds{worker="npc_line"} 1700000000.0', text)

    def test_no_depth_gauge_until_known(self):
        """test_no_depth_gauge_until_known."""
        self.assertNotIn("paldo_llm_queue_depth", WorkerMetrics("review").render(0.0))

    def test_metric_path(self):
        """test_metric_path."""
        self.assertIsNone(metric_path("", "review"))
        self.assertEqual(metric_path("/m/llm_{worker}.prom", "review"), "/m/llm_review.prom")
        self.assertEqual(metric_path("/m/llm.prom", "npc_line"), "/m/npc_line_llm.prom")
        self.assertEqual(metric_path("llm.prom", "review"), "review_llm.prom")
        self.assertEqual(metric_path(r"C:\m\llm.prom", "review"), r"C:\m\review_llm.prom")


class MetricFileTest(SimpleTestCase):
    """원자 교체 쓰기와 갱신 주기."""
    def test_atomic_write_and_interval(self):
        """test_atomic_write_and_interval."""
        with tempfile.TemporaryDirectory() as d:
            now = [100.0]
            f = MetricFile(os.path.join(d, "x.prom"), 60.0, clock=lambda: now[0])
            self.assertTrue(f.due())
            self.assertTrue(f.write(lambda t: f"a {t}\n"))
            self.assertEqual(open(f.path, encoding="utf-8").read(), "a 100.0\n")
            self.assertFalse(os.path.exists(f.path + ".tmp"))
            now[0] = 159.0
            self.assertFalse(f.due())
            now[0] = 160.0
            self.assertTrue(f.due())

    def test_disabled_and_failure_do_not_raise(self):
        """test_disabled_and_failure_do_not_raise."""
        self.assertFalse(MetricFile(None).write(lambda t: "x"))
        self.assertFalse(MetricFile(None).due())
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(MetricFile(os.path.join(d, "missing", "x.prom")).write(lambda t: "x"))


class _Seq:
    """process_next가 정해 둔 결과를 차례로 돌려주고 끝나면 None."""

    def __init__(self, items):
        self.items = list(items)

    def __call__(self):
        item = self.items.pop(0) if self.items else None
        if isinstance(item, Exception):
            raise item
        return item


class WorkerCommandTest(SimpleTestCase):
    """작업 프로세스가 가짜 서비스로 지표 파일을 쓰는지."""
    def _run(self, command, service, worker):
        with tempfile.TemporaryDirectory() as d, \
                override_settings(LLM_METRIC_FILE=os.path.join(d, "llm_{worker}.prom")), \
                mock.patch("server.paldo_web.composition.get_game_service", return_value=service), \
                mock.patch("time.sleep"):
            call_command(command, "--once")
            return open(os.path.join(d, f"llm_{worker}.prom"), encoding="utf-8").read()

    def test_npc_line_worker_writes_file(self):
        """test_npc_line_worker_writes_file."""
        store = SimpleNamespace(count_queued=lambda kind, now: {"live": 1, "polish": 2}[kind])
        lines = SimpleNamespace(narrator=object(), store=store, process_next=_Seq([
            JobOutcome("polish", "done", 1200, 3000, None, True),
            JobOutcome("live", "failed", 100, 8000, "timeout"),
            JobOutcome("polish", "closed", 10, 0, "mode_off"),
            RuntimeError("db")]))
        text = self._run("npc_line_worker", SimpleNamespace(npc_lines=lines), "npc_line")
        self.assertIn('outcome="ok"} 1', text)
        self.assertIn('outcome="late"} 1', text)
        self.assertIn('outcome="error"} 0', text)          # 저장소 예외는 작업 건수가 아니다
        self.assertIn('paldo_llm_worker_loop_errors_total{worker="npc_line"} 1', text)
        self.assertIn('paldo_llm_generate_seconds_count{worker="npc_line"} 2', text)
        self.assertIn('paldo_llm_queue_depth{worker="npc_line"} 3', text)
        self.assertIn('paldo_llm_lines_total{worker="npc_line",source="llm"} 1', text)
        self.assertIn('paldo_llm_lines_total{worker="npc_line",source="template"} 1', text)

    def test_review_worker_writes_file_even_if_depth_fails(self):
        """test_review_worker_writes_file_even_if_depth_fails."""
        def boom():
            raise RuntimeError("db")
        narration = SimpleNamespace(enabled=True, store=SimpleNamespace(count_queued=boom), process_next=_Seq([
            ProcessOutcome("g1", "done", 2000, 25000, llm_npcs=4, template_npcs=1, saved=True),
            ProcessOutcome("g2", "timeout", 170000)]))
        text = self._run("review_worker", SimpleNamespace(narration=narration), "review")
        self.assertIn('paldo_llm_jobs_total{worker="review",outcome="ok"} 1', text)
        self.assertIn('paldo_llm_jobs_total{worker="review",outcome="late"} 1', text)
        self.assertIn('paldo_llm_generate_seconds_count{worker="review"} 1', text)
        self.assertIn('paldo_llm_queue_wait_seconds_count{worker="review"} 2', text)
        self.assertNotIn("paldo_llm_queue_depth", text)
        self.assertIn('paldo_llm_lines_total{worker="review",source="llm"} 4', text)
        self.assertIn('paldo_llm_lines_total{worker="review",source="template"} 1', text)

    def test_idle_worker_still_writes_heartbeat(self):
        """작업이 없어도 첫 대기에서 파일을 쓴다(이후 60초마다는 MetricFile 주기 시험)."""
        store = SimpleNamespace(count_queued=lambda kind, now: 0)
        lines = SimpleNamespace(narrator=object(), store=store, process_next=_Seq([]))
        text = self._run("npc_line_worker", SimpleNamespace(npc_lines=lines), "npc_line")
        self.assertIn('paldo_llm_worker_last_update_seconds{worker="npc_line"}', text)
        self.assertIn('paldo_llm_jobs_total{worker="npc_line",outcome="ok"} 0', text)
