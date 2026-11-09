"""삭제 목록이 다음 적재에서 삭제한 주체와 재판정을 차단하는지 확인한다."""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from pipeline.spark.privacy_filter import load_tombstones, retained_rows
from tools.privacy_delete import register
from tools.privacy_retention import retention_subjects
from tools.privacy_scope import combine


class PrivacyReplayTests(unittest.TestCase):
    """별개 요청·재전송·비대상 레코드를 구분한다."""

    def test_combined_scope_requires_matching_ad_sessions(self):
        """다른 계정의 광고 계획은 대상 범위에 섞지 않는다."""
        player, session = str(uuid4()), str(uuid4())
        game = {"player": player, "ad_sessions": [session], "game_ids": ["game"]}
        ads = {"ad_sessions": [session], "ad_serve_ids": ["serve"], "ad_request_ids": ["request"],
               "ad_event_ids": ["event"], "ad_charge_ids": ["charge"]}
        self.assertEqual(combine(game, ads), {"players": [player], "game_ids": ["game"], **ads})
        with self.assertRaisesRegex(ValueError, "privacy_ad_scope_mismatch"):
            combine(game, {**ads, "ad_sessions": [str(uuid4())]})

    def test_registry_filters_game_and_ad_replays(self):
        player, unrelated = str(uuid4()), str(uuid4())
        with tempfile.TemporaryDirectory() as directory:
            register(directory, player, ["ad-session"], ["evt-old"], ["charge-old"], "v1")
            register(directory, player, ["ad-session"], ["evt-old"], ["charge-old"], "v1")
            (Path(directory) / "unpublished.tmp").write_text("{")
            identities = load_tombstones(directory)
        rows = [{"envelope_json": json.dumps(event)} for event in [
            {"player_pseudonym": player}, {"session_pseudonym": "ad-session"},
            {"source_event_id": "evt-old"}, {"charge_id": "charge-old"},
            {"reserved_charge_id": "charge-old"}, {"player_pseudonym": unrelated},
        ]]
        self.assertEqual(list(retained_rows(rows, identities)), rows[-1:])

    def test_retention_subjects_exclude_staff_and_deduplicate(self):
        player, staff, outsider = str(uuid4()), str(uuid4()), str(uuid4())
        before = "2026-11-10T12:00:00+09:00"
        during = "2026-11-10T14:00:00+09:00"
        rows = [{"player_pseudonym": player, "is_staff": False, "date_joined": before},
                {"player_pseudonym": staff, "is_staff": True, "date_joined": during},
                {"player_pseudonym": outsider, "is_staff": False, "date_joined": before}]
        games = [{"player_pseudonym": player, "source": "human", "created_at": during}]
        self.assertEqual(retention_subjects({"kind": "accounts", "accounts": rows * 2},
                                            {"kind": "games", "games": games}), [player])
