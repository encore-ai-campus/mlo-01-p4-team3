"""증빙 검사(tools/check_evidence.py) 테스트. README 4절 검사 규칙 1~6, 11절 PR 본문, 규칙마다 실패 사례."""
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from tools import check_evidence as ce
from tools.evidence_md import is_completed, parse_evidence
from tools.gitrepo import GitRepo
from tools.tasks_md import parse_task_file

SAMPLE = """# E

## 11/5(목)

### E-05-01 저장소
- 2h · 완료 조건:
  1. 하나
  2. 둘
- 문서화(+0.25h, 공통 ⑦): 트러블

### E-05-05 스타일 [에셋] [검수]
- 2.5h
- 문서화(+0.25h, 공통 ⑦): 트러블
- 완료 조건:
  1. 하나

## 11/6(금)

### E-06-01 템플릿
- 2h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블
"""
TASKS = parse_task_file(SAMPLE, "E", "docs/tasks/E_pm.md")
TASK = {t.task_id: t for t in TASKS}
README = """# 태스크

## 7. 결정 대기 (PM)

| ID | 결정할 것 | 막히는 태스크 | 기한 |
|---|---|---|---|
| P-07 | 부하 | INT-09 | 11/7 |

**확정된 결정 (11/5 PM)**
- P-22 시간 초과 해소: 가볍게.
- P-11: 저장소.

## 8. 추적표

P-07 언급
"""
HEADER = "| 조건 | 기준 | 측정값 | 측정 방법 | 결과 파일 | 통과 |\n|---|---|---|---|---|---|\n"
TROUBLES = "# E\n\n### E-05-01 저장소\n- 없음\n\n### E-05-05 스타일\n- 없음\n\n### E-06-01 템플릿\n- 없음\n"
SHA = "abc1234"


def doc(task_id="E-05-01", rows=None, author="E", hours=None, failed=False, review=None, commit=SHA,
        drop_heading=None):
    rows = rows if rows is not None else ["| 1 | - | 통과 | 테스트 | - | 예 |", "| 2 | - | 통과 | 테스트 | - | 예 |"]
    parts = [f"# {task_id} 제목"]
    if failed:
        parts.append("상태: 실패 — 원인과 재시도 계획")
    parts += [f"- 커밋: {commit}", f"- 작성자(흐름): {author}", "- 환경: 로컬"]
    if hours is not None:  # 11/5 폐지된 줄. 기존 증빙에 남아 있어도 검사하지 않는다
        parts.append(f"- 실제 소요 시간(h): {hours}")
    sections = {
        "명령": "```\npython -m x\n```",
        "출력": "```\n## 이건 코드 블록 안이라 제목이 아니다\n상태: 실패\n```",
        "수치": HEADER + "\n".join(rows),
        "로그": "out/logs/ci.jsonl\n```\n{}\n{}\n{}\n```",
    }
    if review is not None:
        sections["검수"] = "| 검수자(흐름) | 기준표 | 샘플 ID | 판정 |\n|---|---|---|---|\n" + review
    sections["트러블"] = f"[E](../troubles/E.md#{task_id})"
    sections["문서"] = "- docs/troubles/E.md"
    for name, body in sections.items():
        if name != drop_heading:
            parts.append(f"## {name}\n{body}")
    return "\n".join(parts) + "\n"


def result_json(task="E-05-01", key="api_p95", value=412.3, unit="ms", commit=SHA):
    return json.dumps({"task": task, "commit": commit, "generated_at": "2026-11-05T00:00:00Z", "source": "cmd",
                       "metrics": {key: {"value": value, "unit": unit}}})


class FakeRepo:
    def __init__(self, files=None, commits=(SHA,), changes=None, messages=None):
        self.files = {"docs/tasks/README.md": README, "docs/troubles/E.md": TROUBLES, **(files or {})}
        self.commits = set(commits)
        self.changes = changes or []
        self.messages = messages or [("abc1234", "feat: 작업\n")]

    def commit_messages(self, base):
        return self.messages
        self.root = Path(".")

    def read_text(self, path):
        return self.files.get(path)

    def exists(self, path):
        return path in self.files

    def commit_exists(self, sha):
        return any(c.startswith(sha) or sha.startswith(c) for c in self.commits)

    def changed_files(self, base):
        return self.changes

    def tracked_files(self):
        return list(self.files)


def ev_repo(text, task_id="E-05-01", **files):
    return FakeRepo({f"docs/evidence/{task_id}.md": text, **files})


class Format(unittest.TestCase):
    def errors(self, text, task_id="E-05-01"):
        return ce.check_format(task_id, parse_evidence(text), TASK.get(task_id))

    def test_ok_and_failed_status_still_passes_format(self):
        self.assertEqual(self.errors(doc()), [])
        self.assertEqual(self.errors(doc(failed=True)), [])

    def test_missing_heading(self):
        self.assertIn("## 로그", self.errors(doc(drop_heading="로그"))[0])

    def test_heading_inside_code_block_does_not_count(self):
        text = doc(drop_heading="문서").replace("python -m x", "## 문서")
        self.assertIn("## 문서", self.errors(text)[0])

    def test_review_heading_only_for_review_tasks(self):
        self.assertIn("## 검수", self.errors(doc("E-05-05"), "E-05-05")[0])
        self.assertEqual(self.errors(doc("E-05-05", review="| B | 기준표 | s1 | 통과 |"), "E-05-05"), [])

    def test_author_flow(self):
        self.assertIn("작성자(흐름) 값", self.errors(doc(author="PM"))[0])
        self.assertIn("줄이 없음", self.errors(doc().replace("- 작성자(흐름): E\n", ""))[0])

    def test_actual_hours_not_required(self):
        # 11/5 PM 결정: 실제 소요 시간 기록 폐지. 줄이 없어도, 예전 줄이 어떤 값이어도 통과한다
        self.assertNotIn("실제 소요 시간", doc())
        self.assertEqual(self.errors(doc()), [])
        for legacy in ("3", "2시간", ""):
            self.assertEqual(self.errors(doc(hours=legacy)), [], legacy)

    def test_title_and_unknown_id(self):
        self.assertIn("첫 제목", self.errors(doc().replace("# E-05-01 제목", "# 다른 제목"))[0])
        self.assertIn("태스크 문서에 없는 ID", self.errors(doc("E-99-01"), "E-99-01")[0])


class Criterion(unittest.TestCase):
    def test_grammar(self):
        c = ce.parse_criterion("<= 1000 ms")
        self.assertEqual((c.op, c.low, c.unit), ("<=", Decimal("1000"), "ms"))
        self.assertEqual(ce.parse_criterion("40~60 %").op, "~")
        self.assertEqual(ce.parse_criterion("== 0 건").unit, "건")
        self.assertEqual(ce.parse_criterion("<= 0.05").unit, "")
        self.assertEqual(ce.parse_criterion("`>= 80 %`").op, ">=")
        for bad in ("1초 이하", "≤ 1000 ms", "60~40 %", "p95 < 1s", "<= ms", "1000"):
            self.assertIsNone(ce.parse_criterion(bad), bad)

    def test_range_inclusive(self):
        c = ce.parse_criterion("40~60 %")
        self.assertTrue(c.holds(Decimal("40")) and c.holds(Decimal("60")))
        self.assertFalse(c.holds(Decimal("60.01")))

    def test_round_half_up(self):
        self.assertEqual(ce.round_half_up(Decimal("412.35"), 1), Decimal("412.4"))
        self.assertEqual(ce.round_half_up(Decimal("0.5"), 0), Decimal("1"))


class Rule3Rows(unittest.TestCase):
    PATH = "docs/evidence/perf/E-05-01/api.json"

    def check(self, crit, measured, result, passed="예", json_text=None, task_id="E-05-01"):
        row = ["1", crit, measured, "tools/perf/x.py", result, passed]
        files = {self.PATH: json_text if json_text is not None else result_json()}
        load = ce._json_loader(FakeRepo(files))
        return ce.check_numeric_row(task_id, row, load).errors

    def test_ok_rounding(self):
        self.assertEqual(self.check("<= 1000 ms", "412 ms", f"{self.PATH}#api_p95"), [])
        self.assertEqual(self.check("<= 1000 ms", "412.3 ms", f"`{self.PATH}#api_p95`"), [])

    def test_measured_value_mismatch(self):
        self.assertIn("다름", self.check("<= 1000 ms", "413 ms", f"{self.PATH}#api_p95")[0])

    def test_unit_must_match_letters(self):
        self.assertIn("단위", self.check("<= 1 s", "412 ms", f"{self.PATH}#api_p95")[0])
        self.assertIn("단위", self.check("<= 1000 ms", "412", f"{self.PATH}#api_p95")[0])
        self.assertIn("단위", self.check("<= 1000 ms", "412 ms", f"{self.PATH}#api_p95",
                                          json_text=result_json(unit="s"))[0])

    def test_judgement_recomputed_from_unrounded_value(self):
        text = result_json(value=1000.4)
        errs = self.check("<= 1000 ms", "1000 ms", f"{self.PATH}#api_p95", json_text=text)
        self.assertIn("다시 계산한 판정 '아니오'", errs[0])
        self.assertEqual(self.check("<= 1000 ms", "1000 ms", f"{self.PATH}#api_p95", passed="아니오", json_text=text), [])

    def test_unparseable_criterion(self):
        self.assertIn("해석할 수 없음", self.check("1초 이하", "412 ms", f"{self.PATH}#api_p95")[0])

    def test_numeric_criterion_needs_result_file(self):
        self.assertIn("계측값만", self.check("<= 1000 ms", "412 ms", "-")[0])

    def test_dash_criterion_needs_dash_result(self):
        self.assertIn("`-`여야", self.check("-", "통과", f"{self.PATH}#api_p95")[0])
        self.assertEqual(self.check("-", "테스트 12개 통과", "-"), [])

    def test_result_file_problems(self):
        self.assertIn("결과 파일 칸은", self.check("<= 1000 ms", "412 ms", self.PATH)[0])
        self.assertIn("아래가 아님", self.check("<= 1000 ms", "412 ms", "docs/evidence/perf/B-06-02/api.json#api_p95")[0])
        self.assertIn("없음", self.check("<= 1000 ms", "412 ms", "docs/evidence/perf/E-05-01/other.json#api_p95")[0])
        self.assertIn("metrics 키", self.check("<= 1000 ms", "412 ms", f"{self.PATH}#error_count")[0])
        self.assertIn("JSON이 아님", self.check("<= 1000 ms", "412 ms", f"{self.PATH}#api_p95", json_text="{")[0])
        self.assertIn("task", self.check("<= 1000 ms", "412 ms", f"{self.PATH}#api_p95",
                                         json_text=result_json(task="B-06-02"))[0])
        self.assertIn("숫자가 아님", self.check("<= 1000 ms", "412 ms", f"{self.PATH}#api_p95",
                                            json_text=result_json(value="412"))[0])


class Complete(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(ce.complete("E-05-01", ev_repo(doc()), TASKS), [])

    def test_missing_evidence(self):
        self.assertIn("이 없음", ce.complete("E-05-01", FakeRepo(), TASKS)[0])

    def test_rule1_missing_condition_number(self):
        errs = ce.complete("E-05-01", ev_repo(doc(rows=["| 1 | - | 통과 | 테스트 | - | 예 |"])), TASKS)
        self.assertTrue(any("번호 2" in e and "규칙 1" in e for e in errs), errs)
        errs = ce.complete("E-05-01", ev_repo(doc(rows=["| 1, 2 | - | 통과 | t | - | 예 |"])), TASKS)
        self.assertTrue(any("번호 하나" in e for e in errs), errs)

    def test_rule2_not_passed_or_failed_status(self):
        rows = ["| 1 | - | 통과 | 테스트 | - | 예 |", "| 2 | - | 실패 | 테스트 | - | 아니오 |"]
        self.assertTrue(any("규칙 2" in e for e in ce.complete("E-05-01", ev_repo(doc(rows=rows)), TASKS)))
        self.assertTrue(any("상태: 실패" in e for e in ce.complete("E-05-01", ev_repo(doc(failed=True)), TASKS)))

    def test_status_failed_inside_code_block_is_ignored(self):
        self.assertFalse(parse_evidence(doc()).failed)

    def test_rule3_wrong_measured_value(self):
        rows = ["| 1 | <= 1000 ms | 413 ms | perf | docs/evidence/perf/E-05-01/api.json#api_p95 | 예 |",
                "| 2 | - | 통과 | 테스트 | - | 예 |"]
        repo = ev_repo(doc(rows=rows), **{"docs/evidence/perf/E-05-01/api.json": result_json()})
        self.assertTrue(any("규칙 3" in e for e in ce.complete("E-05-01", repo, TASKS)))

    def test_rule4_unknown_commits(self):
        self.assertTrue(any("규칙 4" in e for e in ce.complete("E-05-01", ev_repo(doc(commit="deadbee")), TASKS)))
        self.assertTrue(any("규칙 4" in e for e in ce.complete("E-05-01", ev_repo(doc(commit="")), TASKS)))
        rows = ["| 1 | <= 1000 ms | 412 ms | perf | docs/evidence/perf/E-05-01/api.json#api_p95 | 예 |",
                "| 2 | - | 통과 | 테스트 | - | 예 |"]
        repo = ev_repo(doc(rows=rows), **{"docs/evidence/perf/E-05-01/api.json": result_json(commit="fff0000")})
        errs = ce.complete("E-05-01", repo, TASKS)
        self.assertTrue(any("결과 JSON의 commit" in e for e in errs), errs)

    def test_rule5_review(self):
        rows = ["| 1 | - | 통과 | 검수 | - | 예 |"]
        base = {"rows": rows}
        cases = {
            None: "## 검수",                                   # 절 없음(형식 검사에서도 걸림)
            "": "표 행이 없음",
            "| E | 기준표 | s1 | 통과 |": "= 작성자 흐름",
            "| 홍길동 | 기준표 | s1 | 통과 |": "흐름 ID",
            "| B | | s1 | 통과 |": "칸이 비었음",
        }
        for review, expect in cases.items():
            errs = ce.complete("E-05-05", ev_repo(doc("E-05-05", review=review, **base), "E-05-05"), TASKS)
            self.assertTrue(any(expect in e for e in errs), (review, errs))
        ok = doc("E-05-05", review="| B | 에셋5 체크리스트 | s1, s2 | 통과 |", **base)
        self.assertEqual(ce.complete("E-05-05", ev_repo(ok, "E-05-05"), TASKS), [])

    def test_rule5_not_checked_for_non_review_task(self):
        text = doc(review="| E | 기준표 | s1 | 통과 |")
        self.assertEqual(ce.complete("E-05-01", ev_repo(text), TASKS), [])

    def test_rule6_check_docs_failure(self):
        repo = ev_repo(doc())
        repo.files["docs/troubles/E.md"] = "# E\n"
        errs = ce.complete("E-05-01", repo, TASKS)
        self.assertTrue(errs and all("규칙 6, check_docs" in e for e in errs), errs)

    def test_all_until(self):
        res = ce.check_all("11-05", ev_repo(doc()), TASKS)
        self.assertEqual(list(res), ["E-05-01", "E-05-05"])
        self.assertEqual(res["E-05-01"], [])
        self.assertTrue(res["E-05-05"])

    def test_completed_definition(self):
        self.assertTrue(is_completed(parse_evidence(doc())))
        self.assertFalse(is_completed(parse_evidence(doc(failed=True))))
        self.assertFalse(is_completed(None))


BODY_OK = "Task: E-05-01\n\nCloses #12\n\n## 바뀐 점\n- x\n"


class PrBody(unittest.TestCase):
    IDS = {t.task_id for t in TASKS}

    def check(self, body):
        return ce.check_pr_body(body, self.IDS, ce.confirmed_decisions(README))

    def test_closes_and_refs(self):
        self.assertEqual(self.check(BODY_OK).mode, "complete")
        self.assertEqual(self.check(BODY_OK.replace("Closes", "Refs")).mode, "format")

    def test_task_line_count_and_ids(self):
        self.assertIn("1개", self.check(BODY_OK.replace("Task: E-05-01\n", "")).errors[0])
        self.assertIn("2개", self.check("Task: E-05-01\n" + BODY_OK).errors[0])
        self.assertIn("ID 하나", self.check(BODY_OK.replace("Task: E-05-01", "Task: E-05-01, E-06-01")).errors[0])
        self.assertIn("태스크 문서에 없음", self.check(BODY_OK.replace("E-05-01", "E-09-09")).errors[0])

    def test_link_keywords(self):
        for kw in ("Fixes", "Resolves", "Close", "closed"):
            errs = self.check(BODY_OK.replace("Closes", kw)).errors
            self.assertTrue(any("닫기 키워드" in e for e in errs), kw)
        self.assertIn("정확히 하나", self.check(BODY_OK + "Refs #13\n").errors[0])
        self.assertIn("정확히 하나", self.check(BODY_OK.replace("Closes #12", "Closes #")).errors[0])
        url = "Fixes https://github.com/o/r/issues/13"
        errs = self.check(BODY_OK.replace("Closes #12", "Refs #12") + url + "\n").errors
        self.assertTrue(any("URL 형태" in e for e in errs), errs)
        self.assertTrue(any("URL 형태" in e for e in self.check(BODY_OK + url + "\n").errors))
        for line in ("Closes #12, #13", "Closes #12 and #13", "Refs #12, #13",
                     "Closes #12 https://github.com/o/r/issues/13"):
            errs = self.check(BODY_OK.replace("Closes #12", line)).errors
            self.assertTrue(any("둘 이상" in e for e in errs), line)

    def test_actual_hours_not_required(self):
        # 11/5 PM 결정: PR 본문의 실제 소요 시간 줄은 없어도 되고, 예전 줄이 남아 있어도 보지 않는다
        self.assertNotIn("실제 소요 시간", BODY_OK)
        self.assertEqual(self.check(BODY_OK).errors, [])
        for legacy in ("2.5", "두 시간", ""):
            body = BODY_OK.replace("Task: E-05-01\n", f"Task: E-05-01\n실제 소요 시간(h): {legacy}\n")
            self.assertEqual(self.check(body).errors, [], legacy)

    def test_html_comments_are_ignored(self):
        body = "<!-- 예시 Task: A-05-01, Fixes #1 -->\n" + BODY_OK
        self.assertEqual(self.check(body).errors, [])

    def test_decision_pr(self):
        body = "Task: P-22\n\nCloses #40\n"
        self.assertEqual(self.check(body).mode, "decision")
        self.assertIn("확정된 결정", self.check(body.replace("P-22", "P-07")).errors[0])

    def test_confirmed_decisions_only_from_section_7_confirmed_lists(self):
        self.assertEqual(ce.confirmed_decisions(README), {"P-22", "P-11"})

    def test_pr_closes_runs_complete_and_refs_runs_format(self):
        repo = ev_repo(doc(), changes=[("M", "docs/evidence/E-05-01.md")])
        self.assertEqual(ce.check_pr(BODY_OK, "base", repo, TASKS), [])
        repo.files["docs/evidence/E-05-01.md"] = doc(failed=True)
        self.assertTrue(ce.check_pr(BODY_OK, "base", repo, TASKS))  # --complete는 실패 문서를 막는다
        self.assertEqual(ce.check_pr(BODY_OK.replace("Closes", "Refs"), "base", repo, TASKS), [])  # --format은 통과

    def test_commit_message_closing_keywords_fail(self):
        for msg in ("fix: 버그\n\nFixes #5\n", "작업\n\nCloses #12\n", "resolves owner/repo#3",
                    "closes https://github.com/o/r/issues/4"):
            repo = ev_repo(doc())
            repo.messages = [("aaa1111", "ok\n"), ("bbb2222", msg)]
            errs = ce.check_pr(BODY_OK, "base", repo, TASKS)
            self.assertTrue(any("커밋 bbb2222" in e for e in errs), (msg, errs))
        repo = ev_repo(doc())
        repo.messages = [("aaa1111", "Refs #12 관련 작업\n"), ("ccc3333", "issue #5 주석 정리\n")]
        self.assertEqual(ce.check_pr(BODY_OK, "base", repo, TASKS), [])

    def test_changed_ids(self):
        changes = [("M", "docs/evidence/E-05-01.md"), ("D", "docs/evidence/E-06-01.md"),
                   ("A", "docs/evidence/perf/E-05-01/a.json"), ("A", "docs/evidence/E-05-05.md")]
        self.assertEqual(ce.changed_evidence_ids(changes), ["E-05-01", "E-05-05"])

    def test_changed_with_zero_sha_checks_all_evidence(self):
        repo = ev_repo(doc(author="PM"))
        self.assertTrue(ce.check_changed("0" * 40, repo, TASKS))

    def test_ci_paths_pass_without_actual_hours(self):
        """증빙 검사의 세 경로(PR 본문 + 변경 증빙, 승격 PR 변경 증빙, push 변경 증빙)가 시간 줄 없이 통과."""
        changes = [("M", "docs/evidence/E-05-01.md")]
        for text in (doc(), doc(hours="모름")):
            repo = ev_repo(text, changes=changes)
            self.assertEqual(ce.check_pr(BODY_OK, "base", repo, TASKS), [])  # PR 본문(Closes → --complete)
            self.assertEqual(ce.check_pr(BODY_OK.replace("Closes", "Refs"), "base", repo, TASKS), [])  # Refs → --changed
            self.assertEqual(ce.check_changed("base", repo, TASKS), [])  # 승격 PR·push의 --changed
            self.assertEqual(ce.check_changed("0" * 40, repo, TASKS), [])  # 새 브랜치 push(증빙 전부)


# ---------------------------------------------------------------- 실제 git으로 E-05-02 조건 6의 위반 1 재현
def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ViolationScenario(unittest.TestCase):
    """조건 6 "증빙 누락 + Closes" PR이 evidence 체크에서 실패하는 것을 로컬 git 저장소로 재현."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        (self.root / "docs" / "tasks").mkdir(parents=True)
        (self.root / "docs" / "tasks" / "E_pm.md").write_text(SAMPLE, encoding="utf-8")
        (self.root / "docs" / "tasks" / "README.md").write_text(README, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "checkout", "-q", "-b", "pr")
        (self.root / "tools").mkdir()
        (self.root / "tools" / "x.py").write_text('"""x."""\n', encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "work without evidence")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, body):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ce.main(["--pr-body-env", "PR_BODY", "--changed", "main"], repo=GitRepo(self.root),
                           environ={"PR_BODY": body})
        return code, out.getvalue()

    def test_closes_without_evidence_fails(self):
        code, out = self.run_main(BODY_OK)
        self.assertEqual(code, 1)
        self.assertIn("docs/evidence/E-05-01.md이 없음", out)

    def test_closing_keyword_in_commit_message_fails(self):
        (self.root / "tools" / "y.py").write_text('"""y."""\n', encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "작업\n\nFixes #9")
        code, out = self.run_main(BODY_OK.replace("Closes", "Refs"))
        self.assertEqual(code, 1)
        self.assertIn("메시지에 닫기 키워드 'Fixes #9'", out)

    def test_refs_without_changed_evidence_passes(self):
        self.assertEqual(self.run_main(BODY_OK.replace("Closes", "Refs"))[0], 0)

    def test_unknown_base_is_failure_not_crash(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ce.main(["--changed", "no-such-ref"], repo=GitRepo(self.root))
        self.assertEqual(code, 1)
        self.assertIn("git 오류", out.getvalue())


if __name__ == "__main__":
    unittest.main()
