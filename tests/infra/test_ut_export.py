"""E-08-10: 사용자 테스트 읽기 전용 내보내기(`server/infra/ut_export.py`), 가짜 컬렉션으로."""
import json
import unittest
from datetime import datetime, timezone

from engine.domain.model import ENGINE_VERSION
from server.infra import ut_export as x


class FakeCollection:
    """`find`만 있는 가짜. 쓰기 메서드를 부르면 AttributeError로 드러난다."""

    def __init__(self, docs):
        self.docs, self.calls = docs, []

    def find(self, query, projection):
        self.calls.append((query, projection))
        wanted = (query.get("state.meta.session_id") or {}).get("$in")
        return [d for d in self.docs if wanted is None or d["state"]["meta"].get("session_id") in wanted]


def doc(gid, session, created):
    envelope = lambda seq, kind, **kw: {"envelope": {"event_id": f"{gid}-{seq}", "seq": seq, "event_type": kind,
                                                     "occurred_at": created, "payload": {"k": seq}, **kw},
                                        "committed_at": datetime(2026, 11, 9, 4, 0)}
    return {"_id": gid,
            "state": {"meta": {"player_pseudonym": "p-" + gid, "session_id": session, "source": "human",
                               "created_at": created, "seed": 910001, "preset": {"preset_id": "base6"},
                               "rule_version": "rule-0.5.decision-1", "engine_version": "paldo-engine",
                               "content_version": "content-1.0"},
                      "result": {"completed": True}, "phase": "ended", "day": 2, "truth": {"secret": 1}},
            "outbox": [envelope(2, "game_ended"), envelope(1, "truth_revealed"), envelope(0, "game_started",
                                                                                            command_id="c0")],
            "commands": [{"command_id": "c0", "response_json": json.dumps(
                {"accepted": True, "view": {"phase": "morning", "day": 1, "state_version": 1, "board": "big"}})},
                         {"command_id": "bad", "response_json": "not json"}]}


class ExportTests(unittest.TestCase):
    def test_game_record_drops_truth_and_skipped_events_and_sorts(self):
        rec = x.game_record(doc("g1", "s1", "2026-11-09T04:00:00+00:00"))
        self.assertEqual([e["event_type"] for e in rec["events"]], ["game_started", "game_ended"])
        self.assertEqual(rec["events"][0]["committed_at"], "2026-11-09T04:00:00+00:00")
        self.assertEqual(rec["preset_id"], "base6")
        self.assertNotIn("truth", json.dumps(rec))
        self.assertEqual(rec["commands"][0], {"command_id": "c0", "accepted": True, "replayed": False,
                                              "state_version": 1, "day": 1, "phase": "morning", "reject": None,
                                              "first_event_at": "2026-11-09T04:00:00+00:00"})
        self.assertFalse(rec["commands"][1]["accepted"])
        self.assertIsNone(rec["commands"][1]["first_event_at"])

    def test_command_time_kept_when_its_events_are_skipped(self):
        """추궁 → 투표 전환처럼 observation·decision만 낸 명령도 시각은 남고 본문은 빠진다(Codex P1)."""
        d = doc("g1", "s1", "2026-11-09T04:00:00+00:00")
        d["outbox"].append({"envelope": {"event_id": "g1-9", "seq": 9, "event_type": "decision",
                                         "occurred_at": "2026-11-09T04:05:00+00:00", "command_id": "c5",
                                         "payload": {"secret_reason": 1}}, "committed_at": None})
        d["commands"].append({"command_id": "c5", "response_json": json.dumps(
            {"accepted": True, "view": {"phase": "vote", "day": 1, "state_version": 2}})})
        rec = x.game_record(d)
        self.assertEqual(rec["commands"][2]["first_event_at"], "2026-11-09T04:05:00+00:00")
        self.assertNotIn("secret_reason", json.dumps(rec))

    def test_export_uses_find_with_projection_and_session_filter(self):
        coll = FakeCollection([doc("g2", "s2", "2026-11-09T05:00:00+00:00"),
                               doc("g1", "s1", "2026-11-09T04:00:00+00:00")])
        out = x.export_games(coll, ["s1"])
        self.assertEqual([g["game_id"] for g in out["games"]], ["g1"])
        self.assertEqual(coll.calls, [({"state.meta.session_id": {"$in": ["s1"]}}, x.GAME_PROJECTION)])
        self.assertNotIn("state.truth", x.GAME_PROJECTION)
        self.assertEqual([g["game_id"] for g in x.export_games(coll)["games"]], ["g1", "g2"])

    def test_versions_exported_per_game_and_exporter_engine_version(self):
        """판별 버전 3개와 내보낸 코드의 ENGINE_VERSION을 남긴다(11/8, Codex 지적: engine_version 누락)."""
        rec = x.game_record(doc("g1", "s1", "2026-11-09T04:00:00+00:00"))
        self.assertEqual((rec["rule_version"], rec["engine_version"], rec["content_version"]),
                         ("rule-0.5.decision-1", "paldo-engine", "content-1.0"))
        out = x.export_games(FakeCollection([doc("g1", None, "2026-11-10T04:00:00+00:00")]))
        self.assertEqual(out["engine_version"], ENGINE_VERSION)
        self.assertEqual(out["export_version"], "ut-export-2")

    def test_accounts_have_no_login_fields(self):
        out = x.export_accounts([("u-1", datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc), None, False, True)])
        self.assertEqual(out["accounts"], [{"player_pseudonym": "u-1", "date_joined": "2026-11-09T03:00:00+00:00",
                                            "consented_at": None, "is_staff": False, "is_active": True}])


if __name__ == "__main__":
    unittest.main()
