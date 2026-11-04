"""fixtures/scenarios/*.json의 선언형 체크를 기준 규칙 함수·가짜 구현으로 평가한다.

A의 실제 엔진이 나오면 같은 시나리오의 steps를 엔진으로 재생해 같은 기대값을 확인하는 테스트를 추가한다.
"""
from __future__ import annotations

import unittest

from contracts.datasets import Scenario
from engine.domain import serde
from engine.domain.content import Assignment, ContentBundle, validate_assignment
from engine.domain.model import (
    PRESETS, Command, CommandKind, DivinationResult, Phase, PlayerProgress, PlayerState,
    alibi_proposition_kind, check_end, check_player_limits, divination_of, is_legal_kind,
    missing_slots, night_runs, resolve_vote, winner_of,
)
from engine.ports import ParseContext, StoredCommand, VersionConflict
from engine.testing.fakes import FakeIntentParser, InMemoryGameRepository

from ._util import FX, load_json
from .test_fakes import make_state


def _bundle() -> ContentBundle:
    return serde.from_jsonable(ContentBundle, load_json("fixtures/content_min/bundle.json"))


def _ctx(phase: str) -> ParseContext:
    b = _bundle()
    names = ["hunjang", "jumo", "blacksmith", "meosum", "yakcho"]
    aliases = {c.npc_id: c.aliases for c in b.characters if c.npc_id in names}
    from engine.domain.model import PublicRecord
    return ParseContext("g", 1, Phase(phase), PublicRecord(), aliases)


def evaluate(check) -> dict:
    t, i = check.type, check.input
    if t == "end_check":
        r = check_end(i["g"], i["v"], i["mudang_active"], i["mudang_locked"])
        out = {"reason": r.value if r else None, "night_runs": night_runs(r)}
        if r:
            out["winner"] = winner_of(r).value
        return out
    if t == "vote":
        o = resolve_vote(i["main"], i.get("runoff"))
        return {"locked": o.locked, "runoff_candidates": o.runoff_candidates}
    if t == "phase_legal":
        return {"legal": is_legal_kind(Phase(i["phase"]), CommandKind(i["kind"]), i["is_player"])}
    if t == "alibi":
        return {"proposition_kind": alibi_proposition_kind(i["n_possessed"]).value}
    if t == "divination":
        return {"result": divination_of(i["is_possessed"], i["has_unconfessed_secret"]).value}
    if t == "reveal_record":
        # 공개 기록(Claim.divination_result)과 실제 결과(PlayerState.divinations)는 별도 필드다. 거짓 공개도 합법.
        return {"legal": True, "public_result": DivinationResult(i["revealed"]).value,
                "private_result": DivinationResult(i["actual"]).value}
    if t == "missing_slots":
        return {"missing": missing_slots(serde.from_jsonable(Command, i["command"]))}
    if t == "limit":
        p = serde.from_jsonable(PlayerState, i["player"])
        r = check_player_limits(p, CommandKind(i["kind"]))
        return {"reject": r.value if r else None}
    if t == "assignment":
        a = serde.from_jsonable(Assignment, load_json(i["assignment"]))
        return {"violations": [v.code for v in validate_assignment(PRESETS[i["preset_id"]], _bundle(), a)]}
    if t == "progress_clue":
        p = PlayerProgress("p_test")
        return {"added": [p.add_clue(i["clue_id"], f"g{n}", 1) for n in range(i["times"])]}
    if t == "parse":
        r = FakeIntentParser().parse(i["text"], _ctx(i["phase"]))
        return {"failure": r.failure.value if r.failure else None,
                "kinds": sorted(c.command.kind.value for c in r.candidates)}
    if t == "repo_commit":
        repo = InMemoryGameRepository()
        st = make_state(i["stored_version"])
        repo.create(st, _truth(), [])
        nxt = make_state(i["expected_version"] + 1)
        try:
            repo.commit("g_test", i["expected_version"], nxt, _truth(), [], StoredCommand("c", "{}"))
            return {"error": None}
        except VersionConflict:
            return {"error": "state_version_conflict"}
    if t == "idempotent":
        # 응용 계층 규칙: find_command로 먼저 찾고, 있으면 저장된 응답을 그대로 돌려준다.
        repo = InMemoryGameRepository()
        repo.create(make_state(0), _truth(), [])
        commits, responses = 0, []
        for _ in range(i["repeat"]):
            found = repo.find_command("g_test", i["command_id"])
            if found:
                responses.append(found.response_json)
                continue
            repo.commit("g_test", 0, make_state(1), _truth(), [], StoredCommand(i["command_id"], '{"ok":1}'))
            commits += 1
            responses.append('{"ok":1}')
        return {"commits": commits, "same_response": len(set(responses)) == 1}
    raise AssertionError(f"알 수 없는 체크 종류: {t}")


def _truth():
    from engine.domain.model import GameTruth
    return GameTruth("g_test", roles={})


class Scenarios(unittest.TestCase):
    def test_all(self):
        files = sorted((FX / "scenarios").glob("*.json"))
        self.assertTrue(files)
        for f in files:
            s = serde.from_jsonable(Scenario, load_json(str(f.relative_to(FX.parent))))
            for n, c in enumerate(s.checks):
                with self.subTest(scenario=s.scenario_id, check=n, type=c.type):
                    got = evaluate(c)
                    for k, want in c.expect.items():
                        self.assertEqual(got.get(k), want, f"{k}: {c.note}")


if __name__ == "__main__":
    unittest.main()
