"""태스크 마크다운 파서와 이슈 동기화 계획 테스트(GitHub 호출 없이 가짜 gh로)."""
import re
import tempfile
import unittest
from pathlib import Path

from tools.tasks_md import (
    EXTENSION_ROW, FLOW_FILES, TASK_LIMITS, UNASSIGNED_FLOWS, hours_table, is_extension, load_all, parse_decisions,
    parse_task_file,
)
from tools.tasks_to_issues import (
    MILESTONES, ExistingIssue, apply_plan, desired_issues, milestone_for, parse_assign,
    parse_labels_yml, plan_orphans, render_body,
)

ROOT = Path(__file__).resolve().parents[2]

SAMPLE_A = """# A 엔진 태스크

## 11/5(목)

### A-05-01 판 생성 [검수]
- 3.5h · 선행: E-05-01, D-10-01의 ①(대역 봇) · P-19
- 문서화(+0.5h, 공통 ⑦): 모듈 문서
- 완료 조건:
  1. 첫째
  2. 둘째

```yaml
# 코드 블록 안의 제목은 무시한다
### A-05-99 가짜
```

### A-05-02 같은 조건
- 1h · 담당 D(담당 변경, ID 유지) · 조건은 A-05-01과 같음
- 문서화(+0h, 공통 ⑦, 트러블만): 트러블
- 모듈 문서 일괄 갱신(+0.5h, 공통 ⑦) · 대상: A-05-01 · 코드를 바꿨다면: A-05-02 · 모듈: [`engine`](../modules/engine.md), [`sim`](../modules/sim.md) · 할 일: 행

## 11/11(수) 검증·수정 전용

### A-11-01 수정
- 4h · 조건: [INT-11](E_pm.md) 중 A 항목 전부

---

## 일별 시간 합계

### 표 아님
"""

SAMPLE_E = """# E

## 11/5(목)

### E-05-01 저장소
- 1h · 완료 조건: 한 줄 조건

## 통합 태스크

### INT-07 통합 1 (11/7)
| # | 항목 | 담당 | 기준 |
|---|---|---|---|
| 1 | 완주 | 전원 | 1판 |
| 2 | 대조 | C | 0건 |
"""

README = """## 7. 결정 대기
| ID | 결정할 것 | 막히는 태스크 | 기한 |
|---|---|---|---|
| P-19 | 광고 지면 규격 | E-05-05 | 11/5 09:00 |
| P-22 | **시간** 초과 | 전체 | 11/8 18:00 |
"""


def _write_sample_dir(tmp: Path) -> Path:
    d = tmp / "tasks"
    d.mkdir()
    (d / FLOW_FILES["A"]).write_text(SAMPLE_A, encoding="utf-8")
    (d / FLOW_FILES["E"]).write_text(SAMPLE_E, encoding="utf-8")
    (d / "README.md").write_text(README, encoding="utf-8")
    return d


class FakeGh:
    """GhClient와 같은 메서드를 가진 메모리 가짜."""

    def __init__(self):
        self.issues: dict[int, ExistingIssue] = {}
        self.labels: set[str] = set()
        self.milestones: list[str] = []
        self.calls: list[str] = []

    def ensure_label(self, name, color, description):
        self.labels.add(name)

    def list_milestones(self):
        return list(self.milestones)

    def create_milestone(self, title, due):
        self.milestones.append(title)
        self.calls.append("milestone")

    def list_issues(self):
        return [ExistingIssue(i.number, i.title, i.body, list(i.labels), i.milestone, list(i.assignees), i.state)
                for i in self.issues.values()]

    def create_issue(self, title, body, labels, milestone):
        n = len(self.issues) + 1
        self.issues[n] = ExistingIssue(n, title, body, list(labels), milestone, [])
        self.calls.append("create")
        return n

    def edit_issue(self, number, title, body, add, remove, milestone, assignee=None, clear_milestone=False,
                   remove_assignees=None):
        iss = self.issues[number]
        if title:
            iss.title = title
        if body is not None:
            iss.body = body
        iss.labels = sorted((set(iss.labels) | set(add)) - set(remove))
        if milestone:
            iss.milestone = milestone
        elif clear_milestone:
            iss.milestone = None
        if assignee:
            iss.assignees.append(assignee)
        iss.assignees = [a for a in iss.assignees if a not in (remove_assignees or [])]
        self.calls.append("edit")


class ParseTaskFile(unittest.TestCase):
    def setUp(self):
        self.tasks = parse_task_file(SAMPLE_A, "A", "docs/tasks/A_engine.md")
        self.by_id = {t.task_id: t for t in self.tasks}

    def test_only_real_headings_outside_code_blocks(self):
        self.assertEqual(sorted(self.by_id), ["A-05-01", "A-05-02", "A-11-01"])

    def test_dates_from_headings(self):
        self.assertEqual(self.by_id["A-05-01"].date, "11-05")
        self.assertEqual(self.by_id["A-11-01"].date, "11-11")

    def test_hours_and_doc_hours(self):
        t = self.by_id["A-05-01"]
        self.assertEqual((t.hours, t.doc_hours), (3.5, 0.5))

    def test_deps_with_korean_particle_and_decisions(self):
        t = self.by_id["A-05-01"]
        self.assertEqual(t.deps, ["E-05-01", "D-10-01"])
        self.assertEqual(t.decisions, ["P-19"])

    def test_numbered_conditions_and_tags(self):
        t = self.by_id["A-05-01"]
        self.assertEqual(t.conditions, ["1", "2"])
        self.assertEqual(t.tags, ["검수"])
        self.assertEqual(t.title, "판 생성")

    def test_batch_line(self):
        t = self.by_id["A-05-02"]
        self.assertEqual((t.doc_hours, t.batch_hours), (0.0, 0.5))
        self.assertEqual(t.batch_targets, ["A-05-01"])
        self.assertEqual(t.batch_conditional, ["A-05-02"])
        self.assertEqual(t.batch_modules, ["engine", "sim"])
        self.assertEqual(t.deps, [], "일괄 갱신 줄의 ID는 선행이 아니다")
        self.assertEqual(hours_table(self.tasks)["A"]["11-05"], 3.5 + 0.5 + 1 + 0.5)

    def test_inline_condition_counts_as_one(self):
        self.assertEqual(self.by_id["A-11-01"].conditions, ["1"])

    def test_section_rule_ends_task_body(self):
        self.assertNotIn("표 아님", self.by_id["A-11-01"].body)


class LoadAll(unittest.TestCase):
    def test_inherit_conditions_and_int_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, decisions = load_all(_write_sample_dir(Path(tmp)))
        by_id = {t.task_id: t for t in tasks}
        self.assertEqual(by_id["A-05-02"].conditions, ["1", "2"])  # A-05-01을 물려받음
        self.assertEqual(by_id["INT-07"].conditions, ["1", "2"])
        self.assertEqual(by_id["INT-07"].date, "11-07")
        self.assertEqual(by_id["INT-07"].kind, "integration")
        self.assertEqual([d.decision_id for d in decisions], ["P-19", "P-22"])

    def test_parse_decisions_ignores_other_rows(self):
        self.assertEqual(len(parse_decisions(README + "| A-05-01 | x | y | z |\n")), 2)


class DesiredIssues(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tasks, self.decisions = load_all(_write_sample_dir(Path(tmp)))
        self.desired = {d.key: d for d in desired_issues(self.tasks, self.decisions)}

    def test_labels_and_milestone(self):
        d = self.desired["A-05-01"]
        self.assertEqual(d.labels, ["day:11-05", "flow:A", "review-required", "type:task"])
        self.assertEqual(d.milestone, MILESTONES[0][0])
        self.assertEqual(self.desired["A-11-01"].milestone, MILESTONES[2][0])
        self.assertFalse([k for k in self.desired if k.startswith(("P-", "S-"))])   # 결정·발표 트랙은 이슈로 안 만듦(11/5)
        self.assertIn("type:integration", self.desired["INT-07"].labels)

    def test_milestone_boundaries(self):
        self.assertEqual(milestone_for("11-07"), MILESTONES[0][0])
        self.assertEqual(milestone_for("11-08"), MILESTONES[1][0])
        self.assertEqual(milestone_for("11-12"), MILESTONES[3][0])
        self.assertIsNone(milestone_for(None))

    def test_body_has_marker_and_source(self):
        body = self.desired["A-05-01"].body_template
        self.assertTrue(body.startswith("<!-- task-id: A-05-01 -->"))
        self.assertIn("docs/tasks/A_engine.md", body)

    def test_render_body_links_known_numbers_only(self):
        out = render_body("선행 {{ref:E-05-01}} · {{ref:D-10-01}}", {"E-05-01": 7})
        self.assertEqual(out, "선행 #7 E-05-01 · D-10-01")


class ApplyPlan(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks, decisions = load_all(_write_sample_dir(Path(tmp)))
        self.desired = desired_issues(tasks, decisions, "o/r")
        self.labels = [{"name": "flow:A", "color": "000000", "description": ""}]

    def test_second_run_changes_nothing(self):
        gh = FakeGh()
        first = apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        self.assertEqual(first["created"], len(self.desired))
        self.assertEqual(first["milestones_created"], len(MILESTONES))
        gh.calls.clear()
        second = apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        self.assertEqual((second["created"], second["updated"], second["milestones_created"]), (0, 0, 0))
        self.assertEqual(gh.calls, [])

    def test_dependencies_become_issue_links(self):
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        by_key = {re.search(r"id: (\S+) ", i.body).group(1): i for i in gh.issues.values()}
        self.assertNotIn("P-19", by_key)                    # 결정 대기는 이슈가 없고
        self.assertIn("P-19", by_key["A-05-01"].body)       # 본문에는 ID 글자로만 남는다
        self.assertNotRegex(by_key["A-05-01"].body, r"#\d+ P-19")
        dep = next(d for d in self.desired if any(not x.startswith(("P-", "S-")) for x in d.deps))
        k = next(x for x in dep.deps if not x.startswith(("P-", "S-")))
        self.assertIn(f"#{by_key[k].number} {k}", by_key[dep.key].body)

    def test_manual_labels_kept_and_stale_managed_labels_removed(self):
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        iss = next(i for i in gh.issues.values() if "A-05-01" in i.title)
        iss.labels += ["blocker", "day:11-09"]
        apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        self.assertIn("blocker", iss.labels)
        self.assertNotIn("day:11-09", iss.labels)

    def test_stale_milestone_removed_when_none_desired(self):
        import dataclasses
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        desired = [dataclasses.replace(d, milestone=None) if d.key == "A-05-01" else d for d in self.desired]
        apply_plan(gh, desired, self.labels, {}, log=lambda *_: None)
        iss = next(i for i in gh.issues.values() if "A-05-01" in i.title)
        self.assertIsNone(iss.milestone)
        gh.calls.clear()
        again = apply_plan(gh, desired, self.labels, {}, log=lambda *_: None)
        self.assertEqual(again["updated"], 0)

    def test_orphan_issues_reported_not_closed(self):
        """태스크 ID를 옮기면(예: E-11-02 → S-11-06) 옛 이슈는 자동으로 닫지 않고 목록으로만 알린다."""
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {}, log=lambda *_: None)
        old = gh.create_issue("[E-99-01] 옮겨진 태스크", "<!-- task-id: E-99-01 -->", [], None)
        closed = gh.create_issue("[E-99-02] 닫힌 옛 태스크", "<!-- task-id: E-99-02 -->", [], None)
        gh.issues[closed].state = "CLOSED"
        self.assertEqual(plan_orphans(self.desired, gh.list_issues()), [(old, "E-99-01")])
        logs = []
        summary = apply_plan(gh, self.desired, self.labels, {}, log=logs.append)
        self.assertEqual(summary["orphans"], 1)
        self.assertTrue(any("E-99-01" in l for l in logs))
        self.assertEqual(gh.issues[old].state, "OPEN")

    def test_assign_by_flow_once(self):
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {"A": "alice", "E": "pm"}, log=lambda *_: None)
        a = next(i for i in gh.issues.values() if "A-05-01" in i.title)
        integ = next(i for i in gh.issues.values() if "INT-07" in i.title)
        self.assertEqual(a.assignees, ["alice"])
        self.assertEqual(integ.assignees, ["pm"])
        result = apply_plan(gh, self.desired, self.labels, {"A": "alice", "E": "pm"}, log=lambda *_: None)
        self.assertEqual(result["assigned"], 0)

    def test_owner_line_overrides_flow_and_overwrites_assignees(self):
        tasks = parse_task_file(SAMPLE_A, "A", "docs/tasks/A_engine.md")
        self.assertEqual({t.task_id: t.owner for t in tasks},
                         {"A-05-01": "A", "A-05-02": "D", "A-11-01": "A"})
        gh = FakeGh()
        assign = {"A": "alice", "D": "dave", "E": "pm"}
        apply_plan(gh, self.desired, self.labels, assign, log=lambda *_: None)
        a1 = next(i for i in gh.issues.values() if "A-05-01" in i.title)
        a2 = next(i for i in gh.issues.values() if "A-05-02" in i.title)
        self.assertEqual(a2.assignees, ["dave"])
        # 담당 변경: 문서의 담당자 한 명으로 덮어쓰고 이전 담당자를 뺀다
        a1.assignees = ["bob", "alice"]
        a2.assignees = ["alice"]
        result = apply_plan(gh, self.desired, self.labels, assign, log=lambda *_: None)
        self.assertEqual(a1.assignees, ["alice"])
        self.assertEqual(a2.assignees, ["dave"])
        self.assertEqual(result["assigned"], 2)
        # 로그인 대소문자만 다르면 같은 계정이라 바꾸지 않는다
        a1.assignees = ["Alice"]
        result = apply_plan(gh, self.desired, self.labels, assign, log=lambda *_: None)
        self.assertEqual(a1.assignees, ["Alice"])
        self.assertEqual(result["assigned"], 0)
        # 지정할 로그인이 없는 담당은 건드리지 않는다
        apply_plan(gh, self.desired, self.labels, {"A": "alice"}, log=lambda *_: None)
        self.assertEqual(a2.assignees, ["dave"])

    def test_parse_assign(self):
        self.assertEqual(parse_assign("a=x, B=y,bad"), {"A": "x", "B": "y"})
        self.assertEqual(parse_assign(None), {})


class RealTaskDocs(unittest.TestCase):
    """저장소의 실제 태스크 문서가 이 도구와 문서화 기준을 지키는지."""

    @classmethod
    def setUpClass(cls):
        cls.tasks, cls.decisions = load_all(ROOT / "docs" / "tasks")

    def test_every_task_has_hours_conditions_and_doc_line(self):
        for t in self.tasks:
            with self.subTest(task=t.task_id):
                self.assertTrue(t.conditions, "완료 조건 번호 없음")
                if t.kind == "task":
                    self.assertGreater(t.hours, 0)
                    self.assertRegex(t.body, r"(?m)^- 문서화\(\+\d+(\.\d+)?h, 공통 ⑦(, 트러블만)?\)")

    def test_batch_line_on_last_task_of_each_flow_day(self):
        """P-22(가): 흐름별 하루 한 번, 그날 마지막 태스크에 일괄 갱신 줄. 대상은 그날의 문서 산출 외 태스크 전부."""
        days: dict[tuple[str, str], list] = {}
        for t in self.tasks:
            if t.kind == "task" and t.date and not is_extension(t):  # 확장 태스크는 일괄 갱신 대상이 아니다(2절)
                days.setdefault((t.flow, t.date), []).append(t)
        for t in self.tasks:
            if is_extension(t):
                with self.subTest(extension=t.task_id):
                    self.assertFalse(t.batch_hours, "확장 태스크에는 일괄 갱신 줄을 두지 않는다")
                    listed = {x for o in self.tasks for x in o.batch_targets + o.batch_conditional}
                    self.assertNotIn(t.task_id, listed, "확장 태스크는 일괄 갱신 대상에 넣지 않는다")
        for (flow, date), ts in days.items():
            if flow in UNASSIGNED_FLOWS:
                self.assertFalse([t.task_id for t in ts if t.batch_hours], "발표 트랙은 모듈 문서가 없다")
                continue
            with self.subTest(flow=flow, date=date):
                with_batch = [t.task_id for t in ts if t.batch_hours > 0]
                self.assertEqual(with_batch, [ts[-1].task_id], "일괄 갱신 줄은 그날 마지막 태스크 하나에만")
                last = ts[-1]
                listed = set(last.batch_targets) | set(last.batch_conditional)
                need = {t.task_id for t in ts if "산출물 자체가 문서다" not in t.body}
                self.assertEqual(listed, need)
                self.assertTrue(last.batch_modules)
                for m in last.batch_modules:
                    self.assertTrue((ROOT / "docs" / "modules").exists())
                    self.assertRegex(m, r"^[a-z_]+$")
                self.assertEqual(last.batch_hours, 0.5 if last.batch_targets else 0.25)

    def test_required_tasks_do_not_depend_on_extensions(self):
        """11/5 범위 축소: 필수 태스크는 `[확장]` 태스크를 선행으로 갖지 않는다."""
        ext = {t.task_id for t in self.tasks if is_extension(t)}
        self.assertTrue(ext)
        bad = [(t.task_id, d) for t in self.tasks if not is_extension(t) for d in t.deps if d in ext]
        self.assertEqual(bad, [])

    def test_extension_hours_in_separate_row(self):
        table = hours_table(self.tasks)
        self.assertIn(EXTENSION_ROW, table)
        ext_total = sum(t.hours + t.doc_hours + t.batch_hours for t in self.tasks
                        if is_extension(t) and t.kind == "task" and t.date)
        self.assertAlmostEqual(sum(table[EXTENSION_ROW].values()), ext_total)
        labels = {d.key: d.labels for d in desired_issues(self.tasks, self.decisions)}
        for t in self.tasks:
            if is_extension(t) and t.task_id in labels:
                self.assertIn("scope:extension", labels[t.task_id])

    def test_light_tasks_write_only_troubles(self):
        """P-22(가): 2h 미만·가벼운 태스크는 +0h(트러블만), 태스크별 문서화 줄에 모듈 문서 갱신 의무가 없다."""
        for t in self.tasks:
            if t.kind != "task":
                continue
            with self.subTest(task=t.task_id):
                self.assertNotIn("필수 절 6개 갱신과", t.body, "모듈 문서는 일괄 갱신으로 옮겼다")
                if "트러블만" in t.body:
                    self.assertEqual(t.doc_hours, 0.0)
                if t.task_id in {x for o in self.tasks for x in o.batch_conditional}:
                    self.assertIn("트러블만", t.body)

    def test_presentation_track(self):
        """11/5 PM 결정 ②안: 발표 작업은 S 트랙. A~E에는 발표 자료·리허설 태스크가 남지 않는다."""
        s_tasks = [t for t in self.tasks if t.flow == "S"]
        self.assertTrue(s_tasks)
        for t in s_tasks:
            self.assertRegex(t.task_id, r"^S-\d{2}-\d{2}$")
            self.assertIn("../troubles/S.md", t.body)
        for t in self.tasks:
            if t.flow in "ABCDE" and t.kind == "task":
                self.assertNotRegex(t.title, r"발표 자료|발표 구간|구성안|시연 영상|리허설·(시연|발표)", t.task_id)
        self.assertFalse([d for d in desired_issues(self.tasks, self.decisions) if d.key.startswith("S-")])  # 별도 관리(11/5)
        self.assertIn("P-24", {d.decision_id for d in self.decisions})

    def test_presentation_hours_outside_flow_limits(self):
        import io
        from tools.tasks_to_issues import print_hours
        buf = io.StringIO()
        print_hours(self.tasks, buf)
        out = buf.getvalue()
        s_row = next(l for l in out.splitlines() if l.startswith("S("))
        self.assertIn("담당 미정", s_row)
        # 공개 출력에는 한도·초과 열을 두지 않는다(11/5 PM 결정).
        self.assertNotIn("초과", out)
        self.assertNotIn("한도", out)

    def test_llm_bench_before_llm_server(self):
        by_id = {t.task_id: t for t in self.tasks}
        bench = by_id["E-06-07"]
        self.assertEqual(bench.date, "11-06")
        self.assertIn("E-06-07", by_id["E-07-01"].deps)
        self.assertIn("E-06-07", by_id["C-06-04"].deps)
        self.assertEqual(by_id["C-06-04"].date, "11-06")
        self.assertNotIn("E-05-04", by_id)

    def test_limits(self):
        self.assertEqual(TASK_LIMITS["E"], 9.0)
        self.assertTrue(all(TASK_LIMITS[f] == 7.5 for f in "ABCD"))

    def test_dependencies_point_to_existing_tasks(self):
        ids = {t.task_id for t in self.tasks}
        dec = {d.decision_id for d in self.decisions}
        for t in self.tasks:
            for d in t.deps:
                self.assertIn(d, ids, f"{t.task_id}의 선행 {d}가 없음")
            for p in t.decisions:
                self.assertIn(p, dec | {"P-02"}, f"{t.task_id}의 결정 {p}가 결정 대기 표에 없음")

    def test_labels_file(self):
        labels = parse_labels_yml((ROOT / ".github" / "labels.yml").read_text(encoding="utf-8"))
        names = {l["name"] for l in labels}
        for need in ["flow:A", "flow:E", "day:11-05", "day:11-12", "type:task", "type:integration",
                     "type:decision", "blocker", "evidence-fail", "status:blocked", "bug", "fix", "web"]:
            self.assertIn(need, names)
        for l in labels:
            self.assertRegex(l["color"], r"^[0-9a-f]{6}$")
        used = {l for d in desired_issues(self.tasks, self.decisions) for l in d.labels}
        self.assertEqual(used - names, set(), "이슈가 쓰는 라벨이 labels.yml에 없음")


if __name__ == "__main__":
    unittest.main()
