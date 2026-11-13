"""임시 운영 흐름(tools/interim.py) 테스트: 임시 git 저장소와 임시 폴더(NAS 대신)로
publish → clone → submit → intake → merge → 원장 → status를 돈다. README 10-1절."""
import io
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tools import interim
from tools.interim import InterimError, InterimGit, SubmitInfo
from tools.precheck import clean_env

TASKS_E = """# E

## 11/5(목)

### E-05-01 저장소
- 2h · 완료 조건: 하나
- 문서화(+0.25h, 공통 ⑦): 트러블
"""
LEDGER_HEAD = """# 임시 운영 원장

## 원장

| 태스크 | 작성 흐름 | 제출 번호 | 제출 HEAD SHA | 검토 흐름 | 검토 SHA | 판정 | 병합 SHA | 로컬 CI |
|---|---|---|---|---|---|---|---|---|
"""
STRUCTURE = """# 구조

## 6. 허용 폴더(검사 기준)

```
docs/**
server/
tests/**
```
"""


def _force_remove(func, path, _exc):
    """Windows에서 git 객체 파일이 읽기 전용이라 지워지지 않을 때 쓰기 권한을 주고 다시 지운다."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout.strip()


def ident(cwd):
    git(cwd, "config", "user.email", "t@example.invalid")
    git(cwd, "config", "user.name", "t")
    git(cwd, "config", "core.autocrlf", "false")


def write(root: Path, path: str, text: str):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


# 원격 저장소 전 임시 운영(11/5 전환으로 종료)의 전체 흐름 테스트. 약 28초로 precheck 테스트 시간의 대부분이라
# 기본은 건너뛴다. tools/interim.py를 고칠 때 `PALDO_SLOW_TESTS=1`로 돌린다(docs/modules/ci.md).
@unittest.skipUnless(os.environ.get("PALDO_SLOW_TESTS") == "1", "임시 운영 흐름(느림): PALDO_SLOW_TESTS=1일 때만")
class InterimFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="paldo_interim_test_"))
        # 훅이 물려받은 GIT_DIR 등이 남으면 임시 저장소 대신 실제 저장소에 git 명령이 간다(11/5 사고).
        self.env = {**clean_env(dict(os.environ)), "PALDO_LOG_DIR": str(self.tmp / "logs")}
        self.env.pop("PALDO_SERVICE_TESTS", None)
        self._old_log = os.environ.get("PALDO_LOG_DIR")
        os.environ["PALDO_LOG_DIR"] = str(self.tmp / "logs")
        self.nas = self.tmp / "nas"
        pm = self.tmp / "pm"
        pm.mkdir()
        git(pm, "init", "-q", "-b", "main")
        ident(pm)
        write(pm, "README.md", "# t\n")
        write(pm, "tests/__init__.py", "")
        write(pm, "tests/test_ok.py", "import unittest\n\nclass T(unittest.TestCase):\n"
                                      "    def test_ok(self):\n        self.assertTrue(True)\n")
        write(pm, "docs/tasks/E_pm.md", TASKS_E)
        write(pm, "docs/ops/interim_ledger.md", LEDGER_HEAD)
        write(pm, "docs/contracts/structure.md", STRUCTURE)
        git(pm, "add", "-A")
        git(pm, "commit", "-q", "-m", "init")
        self.pm = InterimGit(pm)

    def tearDown(self):
        if self._old_log is None:
            os.environ.pop("PALDO_LOG_DIR", None)
        else:
            os.environ["PALDO_LOG_DIR"] = self._old_log
        shutil.rmtree(self.tmp, onexc=_force_remove)

    def _publish(self) -> Path:
        errors, bundle = interim.publish(self.pm, self.nas / "main", "11-05", environ=self.env)
        self.assertEqual(errors, [])
        return bundle

    def _teammate(self, main_bundle: Path) -> InterimGit:
        mate = self.tmp / "mate"
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", "-c", "core.autocrlf=false", "-b", "main", str(main_bundle), str(mate)], check=True, capture_output=True)
        ident(mate)
        git(mate, "switch", "-q", "-c", "task/E-05-01")
        return InterimGit(mate)

    def _commit(self, repo: InterimGit, path: str, text: str):
        write(repo.root, path, text)
        git(repo.root, "add", path)
        git(repo.root, "commit", "-q", "-m", f"feat(E-05-01): {path}")

    def _copy_to_nas(self, bundle: Path) -> Path:
        inbox = self.nas / "inbox" / "E"
        inbox.mkdir(parents=True, exist_ok=True)
        for src in (bundle, bundle.with_suffix(".ci.txt")):
            part = inbox / (src.name + ".part")
            shutil.copyfile(src, part)
            part.rename(inbox / src.name)
        return inbox / bundle.name

    def test_full_flow(self):
        main_bundle = self._publish()
        base = self.pm.head()
        self.assertTrue(main_bundle.name.endswith(f"_{base[:12]}.bundle"))
        self.assertTrue(list((self.nas / "main").glob("status_*.md")))
        self.assertEqual(list((self.nas / "main").glob("*.part")), [])
        mate = self._teammate(main_bundle)
        self._commit(mate, "tests/test_more.py", "import unittest\n\nclass M(unittest.TestCase):\n"
                                                 "    def test_m(self):\n        self.assertEqual(1, 1)\n")
        info = SubmitInfo("E-05-01", False)
        out = self.tmp / "mate_out"

        # 커밋 안 된 변경이 있으면 거부
        write(mate.root, "scratch.txt", "x")
        with self.assertRaises(InterimError):
            interim.submit(mate, info, base, out, environ=self.env)
        (mate.root / "scratch.txt").unlink()

        result, bundle = interim.submit(mate, info, base, out, environ=self.env)
        self.assertTrue(result.ok, result.report(info))
        self.assertEqual(bundle.name, f"E-05-01_01_{mate.head()[:12]}.bundle")
        ci_text = bundle.with_suffix(".ci.txt").read_text(encoding="utf-8")
        self.assertIn(f"대상 SHA: {mate.head()}", ci_text)
        self.assertIn(f"base SHA: {base}", ci_text)
        self.assertEqual(git(mate.root, "worktree", "list").count("\n"), 0)  # 임시 작업 트리 정리

        # 재제출은 번호가 오르고 이전 번들은 남는다
        self._commit(mate, "tests/test_more2.py", "")
        result2, bundle2 = interim.submit(mate, info, base, out, environ=self.env)
        self.assertTrue(result2.ok)
        self.assertTrue(bundle2.name.startswith("E-05-01_02_"))
        self.assertTrue(bundle.exists())

        # 로컬 출력 폴더를 바꿔도 공유 폴더(--seen)의 번호를 이어 쓴다
        self._copy_to_nas(bundle2)
        self._commit(mate, "tests/test_more3.py", "")
        result3, bundle3 = interim.submit(mate, info, base, self.tmp / "new_out", environ=self.env,
                                          seen=self.nas / "inbox" / "E")
        self.assertTrue(result3.ok)
        self.assertTrue(bundle3.name.startswith("E-05-01_03_"))
        bundle2 = bundle3

        # 옛 제출(01)도 받아 두면 01은 병합할 수 없다(가장 큰 번호만 유효)
        interim.intake(self.pm, self._copy_to_nas(bundle))
        nas_bundle = self._copy_to_nas(bundle2)
        ref, sha = interim.intake(self.pm, nas_bundle)
        with self.assertRaises(InterimError):
            interim.merge(self.pm, "E-05-01", 1, SubmitInfo("E-05-01"), environ=self.env)
        self.assertEqual((ref, sha), ("refs/inbox/E-05-01/03", mate.head()))

        pre, post, ci, note = interim.merge(self.pm, "E-05-01", 3, SubmitInfo("E-05-01"), environ=self.env)
        self.assertIsNotNone(ci)
        self.assertTrue(ci.ok, ci.report(SubmitInfo()))
        self.assertEqual(pre, base)
        self.assertIn(sha, self.pm.parents(post))
        self.assertEqual(git(self.pm.root, "log", "-1", "--format=%s"), "Task: E-05-01 (제출 03)")
        self.assertIn("원장 행 초안", note)

        # 원장: 같은 흐름 검토는 실패, 다른 흐름이면 통과
        ledger = self.pm.root / "docs/ops/interim_ledger.md"
        row = f"| E-05-01 | E | 03 | {sha} | E | {sha} | 통과 | {post[:12]} | 통과 |\n"
        ledger.write_text(LEDGER_HEAD + row, encoding="utf-8", newline="\n")
        self.assertTrue(any("⑧" in e for e in interim.ledger_errors(self.pm)[1]))
        ledger.write_text(LEDGER_HEAD + row.replace(f"| E | {sha}", f"| B | {sha}"), encoding="utf-8",
                          newline="\n")
        self.assertEqual(interim.ledger_errors(self.pm)[1], [])
        git(self.pm.root, "commit", "-q", "-am", "docs: 원장 E-05-01")

        st = interim.status(self.pm, "11-05", self.nas / "inbox")
        self.assertEqual(st, [("E-05-01", "E", "병합됨")])  # 증빙이 없어 완료는 아니다

    def test_intake_rejects_tampered_bundle(self):
        main_bundle = self._publish()
        base = self.pm.head()
        mate = self._teammate(main_bundle)
        self._commit(mate, "tests/test_more.py", "")
        _, bundle = interim.submit(mate, SubmitInfo("E-05-01", False), base, self.tmp / "o", environ=self.env)
        nas_bundle = self._copy_to_nas(bundle)
        with open(nas_bundle, "ab") as f:
            f.write(b"x")
        with self.assertRaises(InterimError):
            interim.intake(self.pm, nas_bundle)
        self.assertEqual(self.pm.ref_names("refs/inbox"), [])

    def test_intake_does_not_overwrite_ref_on_mismatch(self):
        main_bundle = self._publish()
        base = self.pm.head()
        mate = self._teammate(main_bundle)
        self._commit(mate, "tests/test_more.py", "")
        _, bundle = interim.submit(mate, SubmitInfo("E-05-01", False), base, self.tmp / "o", environ=self.env)
        nas_bundle = self._copy_to_nas(bundle)
        ref, sha = interim.intake(self.pm, nas_bundle)
        # 같은 이름으로 다른 커밋 번들을 만들어 .ci.txt까지 맞춰도 대상 SHA 불일치로 거부되고 참조는 그대로
        self._commit(mate, "tests/test_more2.py", "")
        other = self.tmp / "other.bundle"
        git(mate.root, "bundle", "create", str(other), f"{base}..task/E-05-01")
        shutil.copyfile(other, nas_bundle)
        ci = nas_bundle.with_suffix(".ci.txt")
        text = ci.read_text(encoding="utf-8")
        old_sum = [l for l in text.splitlines() if l.startswith("번들 SHA-256: ")][0]
        ci.write_text(text.replace(old_sum, f"번들 SHA-256: {interim.sha256_of(nas_bundle)}"), encoding="utf-8")
        with self.assertRaises(InterimError):
            interim.intake(self.pm, nas_bundle)
        self.assertEqual(self.pm.rev(ref), sha)
        self.assertEqual(self.pm.ref_names("refs/inbox-tmp"), [])

    def test_service_area_without_service_tests_fails(self):
        main_bundle = self._publish()
        base = self.pm.head()
        mate = self._teammate(main_bundle)
        self._commit(mate, "README.md", "# t\n\n| server/ | 서버 |\n")
        self._commit(mate, "server/app.py", '"""서버."""\n')
        result, bundle = interim.submit(mate, SubmitInfo("E-05-01", False), base, self.tmp / "o",
                                        environ=self.env)
        self.assertIsNone(bundle)
        failed = [name for name, ok, _ in result.steps if not ok]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].startswith("service"))

    def test_folder_outside_structure_contract_fails(self):
        main_bundle = self._publish()
        base = self.pm.head()
        mate = self._teammate(main_bundle)
        self._commit(mate, "README.md", "# t\n\n| misc/ | 메모 |\n")  # 규칙 6(라우팅 문서)은 지킨다
        self._commit(mate, "misc/note.txt", "x\n")
        result, bundle = interim.submit(mate, SubmitInfo("E-05-01", False), base, self.tmp / "o",
                                        environ=self.env)
        self.assertIsNone(bundle)
        steps = {n: (ok, d) for n, ok, d in result.steps}
        ok, detail = steps[f"core: check_structure --diff {base[:12]}"]
        self.assertFalse(ok)
        self.assertIn("misc/", detail)
        self.assertEqual([n for n, (ok, _) in steps.items() if not ok], [f"core: check_structure --diff {base[:12]}"])

    def test_closing_keyword_in_commit_fails(self):
        main_bundle = self._publish()
        base = self.pm.head()
        mate = self._teammate(main_bundle)
        write(mate.root, "tests/test_more.py", "")
        git(mate.root, "add", "-A")
        git(mate.root, "commit", "-q", "-m", "feat: x\n\nCloses #3")
        result, bundle = interim.submit(mate, SubmitInfo("E-05-01", False), base, self.tmp / "o",
                                        environ=self.env)
        self.assertIsNone(bundle)
        self.assertFalse(dict((n, ok) for n, ok, _ in result.steps)[
            "evidence: --format(바뀐 증빙) + 커밋 메시지"])

    def test_cli_has_no_hours_option(self):
        # 11/5 PM 결정: 실제 소요 시간 기록 폐지. --hours는 더 이상 받지 않는다
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            interim.main(["submit", "--task", "E-05-01", "--base", "HEAD", "--hours", "2"], repo=self.pm)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
