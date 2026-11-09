"""사용자 테스트 자격정보가 잘못된 출력 위치로 새지 않게 확인한다."""
from pathlib import Path
import tempfile,unittest
from ops.user_test_ops import private_output,release_archive


class UserTestOpsTests(unittest.TestCase):
    """승인된 비공개 위치와 기존 E 태그만 사용한다."""
    def test_private_output_guards(self):
        """경로 우회·덮어쓰기·일반 폴더는 거절한다."""
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);private=root/'4th_private';private.mkdir(mode=0o700)
            self.assertEqual(private_output(private/'reader.json'),(private/'reader.json').resolve())
            for bad in (root/'reader.json',private/'../reader.json'):
                with self.assertRaises(ValueError):private_output(bad)
            (private/'reader.json').write_text('existing')
            with self.assertRaises(ValueError):private_output(private/'reader.json')

    def test_unconfirmed_release_rejected_before_git(self):
        """main/dev/SHA를 테스트 태그처럼 취급하지 않는다."""
        for tag in ('dev','main','v-usertest-3','0'*40):
            with self.assertRaises(ValueError):release_archive(Path('.'),tag,Path('must-not-exist.zip'))
