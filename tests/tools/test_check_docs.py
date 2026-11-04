"""문서화 검사(tools/check_docs.py) 테스트. README 10절 검사 규칙 1~6, 규칙마다 실패 사례."""
import ast
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools import check_docs as cd
from tools.gitrepo import GitRepo
from tools.tasks_md import parse_task_file

SAMPLE_E = """# E

## 11/5(목)

### E-05-01 저장소
- 2h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블

### E-05-02 CI
- 2.5h · 산출물: `.github/workflows/ci.yml`
- 문서화(+0.25h, 공통 ⑦): 트러블
- 완료 조건:
  1. 하나
  2. 둘

### E-05-04 점검
- 1h · 완료 조건: 하나
- 문서화(+0h, 공통 ⑦, 트러블만): 트러블

### E-05-05 스타일 [에셋] [검수]
- 2.5h
- 문서화(+0.25h, 공통 ⑦): 트러블
- 모듈 문서 일괄 갱신(+0.5h, 공통 ⑦) · 대상: E-05-01, E-05-02, E-05-05 · 코드를 바꿨다면: E-05-04 · 모듈: [`ci`](../modules/ci.md), [`project`](../modules/project.md) · 할 일: x
- 완료 조건:
  1. 하나

## 11/10(화)

### E-10-03 리포트
- 1h · 산출물: `docs/reports/user_test.md`
- 문서화(+0h, 공통 ⑦): 산출물 자체가 문서다. 트러블
- 완료 조건: 하나

### E-10-04 발표 기록
- 1h
- 문서화(+0h, 공통 ⑦): 산출물 자체가 문서다. 트러블
- 완료 조건: 하나

## 통합 태스크

### INT-07 통합 1 (11/7)
- 2h · 조건: 아래 표
| # | 항목 |
|---|---|
| 1 | a |
"""
TASKS = parse_task_file(SAMPLE_E, "E", "docs/tasks/E_pm.md")
MODULES_README = """| 모듈 문서 | 코드 위치 | 주 담당 |
|---|---|---|
| `ci.md` | `.github/`, `tools/check_*.py` | E |
| `project.md` | 저장소 운영(브랜치, 이슈 동기화 `tools/tasks_to_issues.py`) | E |
| `user_test.md` | 사용자 테스트 운영 절차 | E |
"""
TROUBLE_OK = "- 문제: a\n- 원인: b\n- 대응: c\n- 결과: d\n"


def evidence(task_id, flow="E", passed="예", failed=False, link=True, commit="abc1234"):
    top = "상태: 실패\n" if failed else ""
    trouble = f"[트러블](../troubles/{flow}.md#{task_id})" if link else "없음"
    return (f"# {task_id} 제목\n{top}- 커밋: {commit}\n- 작성자(흐름): {flow}\n- 환경: 로컬\n- 실제 소요 시간(h): 1\n"
            f"## 명령\nx\n## 출력\nx\n## 수치\n| 조건 | 기준 | 측정값 | 측정 방법 | 결과 파일 | 통과 |\n"
            f"|---|---|---|---|---|---|\n| 1 | - | 통과 | 테스트 | - | {passed} |\n"
            f"## 로그\nx\n## 트러블\n{trouble}\n## 문서\n- x\n")


def module_doc(rows=(), sections=cd.REQUIRED_MODULE_SECTIONS):
    body = "# m\n\n" + "".join(f"## {s}\n\nx\n\n" for s in sections if s != "변경 이력")
    if "변경 이력" in sections:
        body += "## 변경 이력\n\n| 태스크 | 날짜 | 바뀐 점 |\n|---|---|---|\n" + "".join(f"| {t} | {d} | x |\n" for t, d in rows)
    return body


class FakeRepo:
    """파일 dict와 커밋 정보만 가진 가짜 저장소(check_docs가 쓰는 메서드만)."""

    def __init__(self, files=None, commits=None):
        self.files = {"docs/modules/README.md": MODULES_README, **(files or {})}
        self.commits = commits or {}  # sha → 바뀐 파일 목록
        self.root = Path(".")

    def read_text(self, path):
        return self.files.get(path)

    def exists(self, path):
        return path in self.files

    def commit_exists(self, sha):
        return sha in self.commits

    def commit_files(self, sha):
        return self.commits[sha]


def troubles(*ids, body=TROUBLE_OK):
    return "# 트러블 기록 — E\n\n" + "".join(f"### {i} 제목\n{body}\n" for i in ids)


class Rule1Trouble(unittest.TestCase):
    def test_ok_four_lines(self):
        repo = FakeRepo({"docs/troubles/E.md": troubles("E-05-01"), "docs/evidence/E-05-01.md": evidence("E-05-01")})
        self.assertEqual(cd.check_task("E-05-01", repo, TASKS), [])

    def test_ok_none(self):
        repo = FakeRepo({"docs/troubles/E.md": troubles("E-05-01", body="- 없음\n"),
                         "docs/evidence/E-05-01.md": evidence("E-05-01")})
        self.assertEqual(cd.check_task("E-05-01", repo, TASKS), [])

    def test_missing_file_or_entry(self):
        repo = FakeRepo({"docs/evidence/E-05-01.md": evidence("E-05-01")})
        self.assertIn("항목이 없음", cd.check_task("E-05-01", repo, TASKS)[0])
        repo.files["docs/troubles/E.md"] = troubles("E-05-02")
        self.assertIn("항목이 없음", cd.check_task("E-05-01", repo, TASKS)[0])

    def test_wrong_format(self):
        for body in ("- 문제: a\n- 대응: c\n- 원인: b\n- 결과: d\n", "- 문제: a\n- 원인: b\n- 대응: c\n",
                     "- 문제: a\n- 원인:\n- 대응: c\n- 결과: d\n", "그냥 문장\n"):
            repo = FakeRepo({"docs/troubles/E.md": troubles("E-05-01", body=body),
                             "docs/evidence/E-05-01.md": evidence("E-05-01")})
            self.assertEqual(len(cd.check_task("E-05-01", repo, TASKS)), 1, body)

    def test_evidence_must_link_entry(self):
        repo = FakeRepo({"docs/troubles/E.md": troubles("E-05-01"),
                         "docs/evidence/E-05-01.md": evidence("E-05-01", link=False)})
        self.assertIn("링크가 없음", cd.check_task("E-05-01", repo, TASKS)[0])
        del repo.files["docs/evidence/E-05-01.md"]
        self.assertIn("확인할 수 없음", cd.check_task("E-05-01", repo, TASKS)[0])

    def test_lowercase_anchor_link_ok(self):
        ev = evidence("E-05-01").replace("E.md#E-05-01", "E.md#e-05-01-저장소")
        repo = FakeRepo({"docs/troubles/E.md": troubles("E-05-01"), "docs/evidence/E-05-01.md": ev})
        self.assertEqual(cd.check_task("E-05-01", repo, TASKS), [])

    def test_integration_task_has_no_trouble_entry(self):
        self.assertEqual(cd.check_task("INT-07", FakeRepo(), TASKS), [])

    def test_unknown_id(self):
        self.assertIn("없는 ID", cd.check_task("E-99-01", FakeRepo(), TASKS)[0])


class Rule2Batch(unittest.TestCase):
    def base(self, ci_rows=(), project_rows=(), extra=None):
        files = {
            "docs/troubles/E.md": troubles("E-05-05"),
            "docs/evidence/E-05-05.md": evidence("E-05-05"),
            "docs/modules/ci.md": module_doc(ci_rows),
            "docs/modules/project.md": module_doc(project_rows),
        }
        files.update(extra or {})
        return files

    def test_completed_target_needs_row_with_planned_date(self):
        files = self.base(extra={"docs/evidence/E-05-01.md": evidence("E-05-01")})
        errors = cd.check_task("E-05-05", FakeRepo(files), TASKS)
        self.assertTrue(any("| E-05-01 | 11/5 |" in e for e in errors), errors)
        self.assertTrue(any("E-05-05" in e for e in errors))  # 자기 자신도 대상

    def test_rows_present(self):
        files = self.base(ci_rows=[("E-05-05", "11/5")], project_rows=[("E-05-01", "11/5")],
                          extra={"docs/evidence/E-05-01.md": evidence("E-05-01")})
        self.assertEqual(cd.check_task("E-05-05", FakeRepo(files), TASKS), [])

    def test_row_with_actual_date_instead_of_planned_fails(self):
        files = self.base(ci_rows=[("E-05-05", "11/4")])
        self.assertEqual(len(cd.check_task("E-05-05", FakeRepo(files), TASKS)), 1)

    def test_unfinished_target_is_skipped(self):
        files = self.base(ci_rows=[("E-05-05", "11/5")],
                          extra={"docs/evidence/E-05-02.md": evidence("E-05-02", failed=True),
                                 "docs/evidence/E-05-01.md": evidence("E-05-01", passed="아니오")})
        self.assertEqual(cd.check_task("E-05-05", FakeRepo(files), TASKS), [])

    def test_missing_module_doc_and_sections(self):
        files = self.base(ci_rows=[("E-05-05", "11/5")])
        del files["docs/modules/project.md"]
        self.assertIn("project.md이 없음", cd.check_task("E-05-05", FakeRepo(files), TASKS)[0])
        files["docs/modules/project.md"] = module_doc(sections=("무엇을", "사용법", "왜", "설정값", "한계", "변경 이력"))
        self.assertIn("필수 절", cd.check_task("E-05-05", FakeRepo(files), TASKS)[0])

    def test_conditional_task_needs_row_only_when_module_code_changed(self):
        files = self.base(ci_rows=[("E-05-05", "11/5")],
                          extra={"docs/evidence/E-05-04.md": evidence("E-05-04", commit="c1")})
        touched = FakeRepo(files, commits={"c1": ["tools/check_docs.py", "README.md"]})
        errors = cd.check_task("E-05-05", touched, TASKS)
        self.assertEqual(len(errors), 1)
        self.assertIn("ci.md", errors[0])
        self.assertIn("E-05-04", errors[0])
        untouched = FakeRepo(files, commits={"c1": ["docs/notes.md"]})
        self.assertEqual(cd.check_task("E-05-05", untouched, TASKS), [])
        touched.files["docs/modules/ci.md"] = module_doc([("E-05-05", "11/5"), ("E-05-04", "11/5")])
        self.assertEqual(cd.check_task("E-05-05", touched, TASKS), [])


class Rule3DocOutput(unittest.TestCase):
    def files(self, tid):
        return {"docs/troubles/E.md": troubles(tid), f"docs/evidence/{tid}.md": evidence(tid)}

    def test_output_file_must_exist(self):
        repo = FakeRepo(self.files("E-10-03"))
        self.assertIn("docs/reports/user_test.md", cd.check_task("E-10-03", repo, TASKS)[0])
        repo.files["docs/reports/user_test.md"] = "# 리포트\n"
        self.assertEqual(cd.check_task("E-10-03", repo, TASKS), [])

    def test_task_without_output_path_fails(self):
        self.assertIn("`산출물:` 경로가 없음", cd.check_task("E-10-04", FakeRepo(self.files("E-10-04")), TASKS)[0])


class Rule4All(unittest.TestCase):
    def test_completed_target_without_any_row(self):
        files = {"docs/evidence/E-05-01.md": evidence("E-05-01"), "docs/modules/ci.md": module_doc(),
                 "docs/modules/project.md": module_doc()}
        self.assertEqual(len(cd.check_all("11-05", FakeRepo(files), TASKS)), 1)
        files["docs/modules/project.md"] = module_doc([("E-05-01", "11/5")])
        self.assertEqual(cd.check_all("11-05", FakeRepo(files), TASKS), [])

    def test_not_completed_or_after_until_is_skipped(self):
        files = {"docs/evidence/E-05-01.md": evidence("E-05-01", failed=True), "docs/modules/ci.md": module_doc()}
        self.assertEqual(cd.check_all("11-05", FakeRepo(files), TASKS), [])
        files["docs/evidence/E-05-01.md"] = evidence("E-05-01")
        self.assertEqual(cd.check_all("11-04", FakeRepo(files), TASKS), [])


class Rule5Docstrings(unittest.TestCase):
    SRC = '''"""m"""
def public():
    return 1


def _private():
    return 2


class Thing:
    """문서."""

    def method(self):
        return 3

    def _hidden(self):
        return 4


def documented():
    """있음."""
'''

    def test_whole_file(self):
        errors = cd.missing_docstrings(self.SRC, None, "a.py")
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("`public`" in e for e in errors))
        self.assertTrue(any("`Thing.method`" in e for e in errors))

    def test_only_changed_definitions(self):
        self.assertEqual(cd.missing_docstrings(self.SRC, {20}, "a.py"), [])
        self.assertEqual(len(cd.missing_docstrings(self.SRC, {3}, "a.py")), 1)
        self.assertEqual(len(cd.missing_docstrings(self.SRC, {14}, "a.py")), 1)  # 메서드 본문만 바뀜

    def test_syntax_error_fails(self):
        self.assertIn("구문 오류", cd.missing_docstrings("def (:\n", None, "a.py")[0])

    def test_targets(self):
        self.assertTrue(cd.docstring_target("tools/x.py"))
        self.assertFalse(cd.docstring_target("tests/tools/test_x.py"))
        self.assertFalse(cd.docstring_target("server/app/migrations/0001_init.py"))
        self.assertFalse(cd.docstring_target("docs/x.md"))

    def test_public_defs_include_async_and_decorators(self):
        tree = ast.parse("import functools\n@functools.cache\nasync def f():\n    pass\n")
        self.assertEqual(cd.public_defs(tree), [("f", 2, 4, False)])


class Rule6ContractsAndRouting(unittest.TestCase):
    def test_contract_change_needs_contract_doc(self):
        self.assertEqual(len(cd.check_contract_docs(["engine/ports.py"])), 1)
        self.assertEqual(len(cd.check_contract_docs(["ads/domain/rules.py", "tools/x.py"])), 1)
        self.assertEqual(cd.check_contract_docs(["engine/ports.py", "docs/contracts/ports.md"]), [])
        self.assertEqual(cd.check_contract_docs(["engine/domain/rules_helper.py"]), [])

    def test_new_dirs(self):
        self.assertEqual(cd.candidate_new_dirs(["a/b/c/d.py", "e.py", "a/x.py"]), ["a", "a/b"])
        self.assertEqual(len(cd.check_routing_doc(["ml"], ["ml/x.py"])), 1)
        self.assertEqual(cd.check_routing_doc(["ml"], ["ml/x.py", "README.md"]), [])


# ---------------------------------------------------------------- 실제 git으로 E-05-02 조건 6의 위반 2·3 재현
def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ViolationScenarios(unittest.TestCase):
    """조건 6의 "일부러 어긴 PR" 중 docstring 누락·계약 파일만 변경을 로컬 git 저장소로 재현."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        git(self.root, "init", "-q", "-b", "master")
        git(self.root, "config", "user.email", "t@example.invalid")
        git(self.root, "config", "user.name", "t")
        self.write("README.md", "# r\n")
        self.write("engine/ports.py", '"""포트."""\n\n\nclass Port:\n    """포트."""\n')
        self.write("docs/contracts/ports.md", "# ports\n")
        self.write("tools/util.py", '"""u."""\n\n\ndef old():\n    return 1\n')
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "checkout", "-q", "-b", "pr")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def run_diff(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cd.main(["--diff", "master"], repo=GitRepo(self.root))
        return code, out.getvalue()

    def test_clean_pr_passes(self):
        self.write("tools/util.py", '"""u."""\n\n\ndef old():\n    return 1\n\n\ndef new():\n    """새 함수."""\n')
        git(self.root, "commit", "-qam", "ok")
        self.assertEqual(self.run_diff()[0], 0)

    def test_missing_docstring_pr_fails(self):
        self.write("tools/util.py", '"""u."""\n\n\ndef old():\n    return 1\n\n\ndef new():\n    return 2\n')
        git(self.root, "commit", "-qam", "no docstring")
        code, out = self.run_diff()
        self.assertEqual(code, 1)
        self.assertIn("`new`에 docstring이 없음", out)
        self.assertNotIn("`old`", out)  # 바뀌지 않은 정의는 보지 않는다

    def test_contract_only_pr_fails(self):
        self.write("engine/ports.py", '"""포트."""\n\n\nclass Port:\n    """포트(바뀜)."""\n')
        git(self.root, "commit", "-qam", "contract only")
        code, out = self.run_diff()
        self.assertEqual(code, 1)
        self.assertIn("계약 파일 변경(engine/ports.py)", out)

    def test_new_folder_without_readme_fails_and_uncommitted_counts(self):
        self.write("ml/model.py", '"""모델."""\n')  # 커밋하지 않은 새 파일도 변경으로 본다
        code, out = self.run_diff()
        self.assertEqual(code, 1)
        self.assertIn("새 폴더(ml/)", out)


if __name__ == "__main__":
    unittest.main()
