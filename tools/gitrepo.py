"""인프라: 저장소 파일과 git 읽기(검사 도구 `check_evidence`·`check_docs`·`check_secrets` 공용).

검사 규칙(도메인 함수)은 이 모듈을 직접 부르지 않고, 이 클래스와 같은 메서드를 가진
객체를 인자로 받는다. 테스트는 같은 메서드를 가진 가짜 객체나 임시 git 저장소로 바꿔 끼운다.
쓰기는 하지 않는다.

표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZERO_SHA_RE = re.compile(r"^0+$")
RECORD_SEP, FIELD_SEP = chr(0x1E), chr(0x1F)  # git log 형식의 %x1e·%x1f
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    """git 명령이 실패했다(없는 커밋, 저장소 아님 등)."""


class GitRepo:
    """작업 트리 하나. 경로는 모두 저장소 루트 기준 `/` 구분 상대 경로다."""

    def __init__(self, root: Path | str = ROOT):
        self.root = Path(root)

    # --- 파일 ---
    def read_text(self, path: str) -> str | None:
        """파일 내용(UTF-8). 없으면 None."""
        p = self.root / path
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")

    def read_bytes(self, path: str) -> bytes | None:
        """파일 바이트. 없으면 None."""
        p = self.root / path
        return p.read_bytes() if p.is_file() else None

    def exists(self, path: str) -> bool:
        """파일이나 폴더가 있는지."""
        return (self.root / path).exists()

    # --- git ---
    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8",
        )
        if check and proc.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout

    def tracked_files(self) -> list[str]:
        """`git ls-files`(추적 중인 파일)."""
        out = self._git("-c", "core.quotepath=off", "ls-files", "-z")
        return [p for p in out.split("\0") if p]

    def commit_exists(self, sha: str) -> bool:
        """커밋 SHA(축약 포함)가 이 저장소에 있는지."""
        proc = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self.root, capture_output=True,
        )
        return proc.returncode == 0

    def commit_files(self, sha: str) -> list[str]:
        """그 커밋이 첫 부모 대비 바꾼 파일. 루트 커밋이면 모든 파일."""
        parents = self._git("rev-list", "--parents", "-n", "1", sha).split()
        if len(parents) > 1:
            out = self._git("-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", parents[1], sha)
        else:
            out = self._git("-c", "core.quotepath=off", "diff-tree", "--root", "-r", "--name-only",
                            "--no-commit-id", sha)
        return [p for p in out.splitlines() if p]

    def merge_base(self, base: str) -> str:
        """`base`와 HEAD의 공통 조상."""
        return self._git("merge-base", base, "HEAD").strip()

    def changed_files(self, base: str) -> list[tuple[str, str]]:
        """`base`(와 HEAD의 공통 조상) 이후 바뀐 파일 [(상태, 경로)]. 상태는 A·M·D.

        작업 트리의 커밋 전 변경과 추적되지 않은 새 파일(A)도 포함한다(로컬에서도 CI와 같은 결과).
        """
        mb = self.merge_base(base)
        out = self._git("-c", "core.quotepath=off", "diff", "--name-status", "--no-renames", mb)
        result: list[tuple[str, str]] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                result.append((parts[0][0], parts[-1]))
        for path in self.untracked_files():
            result.append(("A", path))
        return result

    def untracked_files(self) -> list[str]:
        """추적되지 않은 새 파일(.gitignore 제외)."""
        out = self._git("-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard", "-z")
        return [p for p in out.split("\0") if p]

    def changed_lines(self, base: str, path: str) -> set[int] | None:
        """새 쪽 기준으로 바뀐 줄 번호. 추적되지 않은 새 파일이면 None(전부 새 줄)."""
        if path in set(self.untracked_files()):
            return None
        mb = self.merge_base(base)
        out = self._git("diff", "-U0", "--no-renames", mb, "--", path)
        lines: set[int] = set()
        for line in out.splitlines():
            m = _HUNK_RE.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                lines.update(range(start, start + count))
        return lines

    def dir_existed(self, base: str, directory: str) -> bool:
        """`base`(공통 조상) 시점에 그 폴더가 있었는지."""
        mb = self.merge_base(base)
        proc = subprocess.run(
            ["git", "cat-file", "-t", f"{mb}:{directory}"], cwd=self.root, capture_output=True, text=True,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "tree"

    def commit_messages(self, base: str) -> list[tuple[str, str]]:
        """`base..HEAD` 커밋의 [(짧은 SHA, 전체 메시지)]."""
        out = self._git("log", "--format=%h%x1f%B%x1e", f"{base}..HEAD")
        result = []
        for rec in out.split(RECORD_SEP):
            rec = rec.strip("\n")
            if FIELD_SEP in rec:
                sha, msg = rec.split(FIELD_SEP, 1)
                result.append((sha.strip(), msg))
        return result

    def head(self) -> str:
        """현재 HEAD 커밋 SHA(40자)."""
        return self._git("rev-parse", "HEAD").strip()
