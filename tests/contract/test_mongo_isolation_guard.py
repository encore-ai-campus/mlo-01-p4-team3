"""운영 복사 도구는 잘못된 DB/호스트 인자를 파일·DB 접근 전에 거절한다."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from ops.provision_mongo import validate


class IsolationGuard(unittest.TestCase):
    """시스템 DB와 동일 원본/대상에 대한 복사를 막는다."""
    def arguments(self,**changes):
        """파일 읽기 이전에 실패해야 하는 명시 설정을 만든다."""
        values={'source':'pipeline_ops','target':'paldo_web','app_user':'test_app','replica_set':'test_rs','seed':'db.example.invalid',
                'admin_profile':Path('must-not-be-read'),'profile_out':Path('must-not-be-created'),'backup_dir':Path('must-not-be-created'),'apply':True}
        values.update(changes);return SimpleNamespace(**values)

    def test_system_target_is_rejected(self):
        for target in ('admin','local','config'):
            with self.assertRaisesRegex(ValueError,'dedicated_target_required'):validate(self.arguments(target=target))

    def test_system_source_is_rejected(self):
        for source in ('admin','local','config'):
            with self.assertRaisesRegex(ValueError,'dedicated_target_required'):validate(self.arguments(source=source))

    def test_source_cannot_be_target(self):
        with self.assertRaisesRegex(ValueError,'dedicated_target_required'):validate(self.arguments(target='pipeline_ops'))

    def test_seed_cannot_be_uri_or_multiple_hosts(self):
        for seed in ('mongodb://db.example.invalid','db-a,db-b','db.example.invalid:27017'):
            with self.assertRaisesRegex(ValueError,'single_seed_host_required'):validate(self.arguments(seed=seed))
