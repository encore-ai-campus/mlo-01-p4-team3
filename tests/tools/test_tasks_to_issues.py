"""태스크 마크다운 파서와 이슈 동기화 계획 테스트(GitHub 호출 없이 가짜 gh로)."""
import re
import tempfile
import unittest
from pathlib import Path

from tools.tasks_md import FLOW_FILES, load_all, parse_decisions, parse_task_file
from tools.tasks_to_issues import (
    MILESTONES, ExistingIssue, apply_plan, desired_issues, milestone_for, parse_assign,
    parse_labels_yml, render_body,
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
- 1h · 조건은 A-05-01과 같음

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
        return [ExistingIssue(i.number, i.title, i.body, list(i.labels), i.milestone, list(i.assignees))
                for i in self.issues.values()]

    def create_issue(self, title, body, labels, milestone):
        n = len(self.issues) + 1
        self.issues[n] = ExistingIssue(n, title, body, list(labels), milestone, [])
        self.calls.append("create")
        return n

    def edit_issue(self, number, title, body, add, remove, milestone, assignee=None, clear_milestone=False):
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
        self.assertEqual(self.desired["P-22"].milestone, MILESTONES[1][0])
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
        p19 = by_key["P-19"].number
        self.assertIn(f"#{p19} P-19", by_key["A-05-01"].body)

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

    def test_assign_by_flow_once(self):
        gh = FakeGh()
        apply_plan(gh, self.desired, self.labels, {"A": "alice", "E": "pm"}, log=lambda *_: None)
        a = next(i for i in gh.issues.values() if "A-05-01" in i.title)
        integ = next(i for i in gh.issues.values() if "INT-07" in i.title)
        self.assertEqual(a.assignees, ["alice"])
        self.assertEqual(integ.assignees, ["pm"])
        result = apply_plan(gh, self.desired, self.labels, {"A": "alice", "E": "pm"}, log=lambda *_: None)
        self.assertEqual(result["assigned"], 0)

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
                    self.assertRegex(t.body, r"(?m)^- 문서화\(\+\d+(\.\d+)?h, 공통 ⑦\)")

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
