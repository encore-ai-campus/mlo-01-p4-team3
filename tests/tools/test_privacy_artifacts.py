"""승인 목록의 파일만 정리하고 경로·내용 변경을 거절한다."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.privacy_artifacts import purge_files


class ArtifactCleanupTests(unittest.TestCase):
    """실제 임시 파일을 대상으로 작업한다."""

    def test_preflight_and_delete_are_scoped_and_repeatable(self):
        """사전 검사는 파일을 보존하고 재실행은 신규 삭제가 없다."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            target, other = root / "target", root / "other"
            target.write_bytes(b"target")
            other.write_bytes(b"other")
            scope = [{"path": "target", "sha256": sha256(b"target").hexdigest()}]
            self.assertEqual(purge_files(root, scope)["deleted"], 0)
            self.assertTrue(target.exists())
            self.assertEqual(purge_files(root, scope, apply=True)["deleted"], 1)
            self.assertEqual(purge_files(root, scope, apply=True)["already_missing"], 1)
            self.assertTrue(other.exists())

    def test_changed_content_does_not_delete_any_file(self):
        """목록 중 하나가 변해도 먼저 확인한 파일을 지우지 않는다."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("first", "changed"):
                (root / name).write_bytes(b"current")
            with self.assertRaisesRegex(ValueError, "artifact_hash_mismatch"):
                purge_files(root, [{"path": name, "sha256": sha256(data).hexdigest()}
                                   for name, data in [("first", b"current"), ("changed", b"old")]], apply=True)
            self.assertTrue((root / "first").exists())
