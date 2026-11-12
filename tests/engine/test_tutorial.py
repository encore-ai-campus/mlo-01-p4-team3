"""튜토리얼 단계 규칙(engine/domain/tutorial.py) 단위 시험과 대본 끝까지 가는 엔진 시험.

- 단위: 단계 번호, 종류별 비교 칸(근거 집합 순서 무관, contradiction 묶음 허용), 자유 입력 단계의 설정별 경로.
- 대본: 튜토리얼 시드·규칙·모델 없음 의존성으로 단계표 명령을 그대로 넣으면 매번 같은 승리(이벤트 해시 3회 동일).
  규칙 수치·NPC 정책·콘텐츠가 바뀌면 이 시험이 먼저 깨진다. 그때 `tools/tutorial_seed_search.py`로 시드를 다시 고른다.
"""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from engine.domain import tutorial
from engine.domain.engine import abstains
from engine.domain.model import (
    ENGINE_VERSION, PLAYER_ID, PRESETS, Command, CommandEnvelope, CommandKind, DataSource, DivinationPayload,
    EvidenceRef, EvidenceType, GameMeta, InterrogationKind, Slot,
)
from engine.domain.serde import dumps
from engine.ports import EngineDeps

ROOT = Path(__file__).resolve().parents[2]


def command_of(exp: tutorial.Expected) -> Command:
    """단계 허용 명령을 웹 양식과 같은 모양의 명령으로 만든다(모순 근거는 claim 두 개)."""
    evidence = [EvidenceRef(EvidenceType.CLAIM, cid) for cid in sorted(exp.evidence)]
    divination = DivinationPayload(*exp.divination) if exp.divination else None
    return Command(exp.kind, PLAYER_ID, target=exp.target, addressee=exp.addressee,
                   interrogation_kind=exp.interrogation_kind, night=exp.night, slot=exp.slot,
                   evidence=evidence, divination=divination)


def wrong_commands(exp: tutorial.Expected) -> list[Command]:
    """같은 단계에서 거부돼야 할 명령들(다른 종류·다른 대상·기권)."""
    out = [Command(CommandKind.ADVANCE if exp.kind is not CommandKind.ADVANCE else CommandKind.SILENCE, PLAYER_ID),
           Command(CommandKind.ABSTAIN, PLAYER_ID) if exp.kind is not CommandKind.ABSTAIN
           else Command(CommandKind.VOTE, PLAYER_ID, target="jumo")]
    right = command_of(exp)
    if exp.target:
        out.append(Command(exp.kind, PLAYER_ID, target="yakcho" if exp.target != "yakcho" else "hunjang",
                           evidence=right.evidence))
    if exp.addressee:
        out.append(Command(exp.kind, PLAYER_ID, addressee="yakcho" if exp.addressee != "yakcho" else "hunjang",
                           interrogation_kind=exp.interrogation_kind,
                           night=exp.night, slot=exp.slot, evidence=right.evidence))
    return out


class StepRules(unittest.TestCase):
    def test_step_index(self):
        self.assertEqual(tutorial.step_index(tutorial.START_VERSION), 0)
        self.assertIsNone(tutorial.step_index(tutorial.START_VERSION + len(tutorial.STEPS)))
        self.assertIsNone(tutorial.step_index(-1))

    def test_every_step_accepts_its_command_and_rejects_others(self):
        for i, step in enumerate(tutorial.STEPS):
            exp = step.expected
            via = tutorial.utterance_step(exp, True)
            self.assertTrue(tutorial.allows(i, command_of(exp), via), i)
            for cmd in wrong_commands(exp):
                self.assertFalse(tutorial.allows(i, cmd, via), (i, cmd))

    def test_contradiction_evidence_is_a_set(self):
        i = next(i for i, s in enumerate(tutorial.STEPS) if s.expected.kind is CommandKind.POINT_CONTRADICTION)
        ids = sorted(tutorial.STEPS[i].expected.evidence)
        rev = Command(CommandKind.POINT_CONTRADICTION, PLAYER_ID,
                      evidence=[EvidenceRef(EvidenceType.CLAIM, c) for c in reversed(ids)])
        pair = Command(CommandKind.POINT_CONTRADICTION, PLAYER_ID,
                       evidence=[EvidenceRef(EvidenceType.CONTRADICTION, "|".join(ids))])
        one = Command(CommandKind.POINT_CONTRADICTION, PLAYER_ID, evidence=[EvidenceRef(EvidenceType.CLAIM, ids[0])])
        self.assertTrue(tutorial.allows(i, rev, False))
        self.assertTrue(tutorial.allows(i, pair, False))
        self.assertFalse(tutorial.allows(i, one, False))

    def test_contradiction_question_has_no_night_slot(self):
        """웹 양식은 모순 질문의 밤·시간 칸을 보내지 않는다. 값이 붙으면 다른 명령으로 본다(압박도가 달라짐)."""
        i = next(i for i, s in enumerate(tutorial.STEPS)
                 if s.expected.interrogation_kind is InterrogationKind.CONTRADICTION)
        exp = tutorial.STEPS[i].expected
        with_slot = Command(CommandKind.INTERROGATE, PLAYER_ID, addressee=exp.addressee,
                            interrogation_kind=exp.interrogation_kind, night=0, slot=Slot.EVENING,
                            evidence=command_of(exp).evidence)
        self.assertFalse(tutorial.allows(i, with_slot, False))

    def test_utterance_step_follows_free_text_setting(self):
        i = next(i for i, s in enumerate(tutorial.STEPS) if s.expected.via_utterance)
        cmd = command_of(tutorial.STEPS[i].expected)
        self.assertTrue(tutorial.allows(i, cmd, True, free_text=True))
        self.assertFalse(tutorial.allows(i, cmd, False, free_text=True))     # 켬: 버튼 거부
        self.assertTrue(tutorial.allows(i, cmd, False, free_text=False))     # 끔: 같은 명령을 버튼으로
        self.assertFalse(tutorial.allows(i, cmd, True, free_text=False))

    def test_rule_version_is_fixed_constant(self):
        self.assertTrue(tutorial.rule_version().startswith(tutorial.RULE_BASE + "."))

    def test_teaches_every_control(self):
        kinds = {s.expected.kind for s in tutorial.STEPS}
        for kind in (CommandKind.ADVANCE, CommandKind.POINT_CONTRADICTION, CommandKind.INTERROGATE, CommandKind.DEFEND,
                     CommandKind.VOTE, CommandKind.DIVINE, CommandKind.REVEAL_DIVINATION, CommandKind.SUSPECT):
            self.assertIn(kind, kinds)
        if abstains(tutorial.rule_version()):
            self.assertIn(CommandKind.ABSTAIN, kinds)

    def test_on_script_checks_day_and_phase_per_step(self):
        """각본 이탈 검사: 상태 버전으로 정한 단계의 날짜·단계와 실제가 같아야 대본대로다."""
        from engine.domain.model import Phase
        for i, step in enumerate(tutorial.STEPS):
            v = tutorial.START_VERSION + i
            self.assertTrue(tutorial.on_script(v, step.day, step.phase, False))
            other = Phase.NIGHT if step.phase is not Phase.NIGHT else Phase.MORNING
            self.assertFalse(tutorial.on_script(v, step.day, other, False))
            self.assertFalse(tutorial.on_script(v, step.day + 1, step.phase, False))
        end = tutorial.START_VERSION + len(tutorial.STEPS)
        self.assertFalse(tutorial.on_script(end, 2, Phase.VOTE, False))       # 단계표 밖인데 안 끝남
        self.assertTrue(tutorial.on_script(end, 2, Phase.VOTE, True, True))   # 끝 버전에서 마을 승리
        self.assertFalse(tutorial.on_script(end, 2, Phase.VOTE, True, False))
        self.assertFalse(tutorial.on_script(end - 3, 1, Phase.NIGHT, True, True))   # 일찍 끝남


def play_script(content=None):
    """튜토리얼 판을 대본대로 끝까지 둔다. (마지막 상태, 단계별 (날, 단계) 목록, 이벤트 해시)."""
    from server.infra.game_factory import ContentPolicyEngine
    from tools.check_content import load_bundle
    content = content or load_bundle(ROOT / "content" / "bundle_v1.json")
    engine = ContentPolicyEngine()
    deps = EngineDeps(content, None)
    meta = GameMeta("tutorial-test", PRESETS[tutorial.PRESET_ID], tutorial.SEED, DataSource.SCRIPTED,
                    tutorial.rule_version(), ENGINE_VERSION, content.content_version, player_pseudonym="p",
                    scenario_id=tutorial.SCENARIO_ID)
    tr = engine.new_game(meta, deps)
    state, truth = tr.state, tr.truth
    trace = hashlib.sha256(dumps(tr.events).encode())
    seen = []
    for i, step in enumerate(tutorial.STEPS):
        assert state.state_version == tutorial.START_VERSION + i
        seen.append((state.day, state.phase))
        t = engine.apply(state, truth, CommandEnvelope(meta.game_id, f"c{i}", state.state_version,
                                                       command_of(step.expected)), deps)
        assert t.accepted, (i, t.reject, t.reject_detail)
        trace.update(dumps(t.events).encode())
        state, truth = t.state, t.truth
    return state, seen, trace.hexdigest()


class Required(unittest.TestCase):
    """튜토리얼 강제·첫 판 안내(11/11 사용자 결정)."""

    def test_routes_only_open_start_before_tutorial(self):
        self.assertTrue(tutorial.routes_to_tutorial(None, None))       # 칸 이전 기록 = 미완료
        self.assertFalse(tutorial.routes_to_tutorial(True, None))
        self.assertFalse(tutorial.routes_to_tutorial(None, "round-1"))  # 회차 판은 가로채지 않음

    def test_first_game_guide_off_after_tutorial(self):
        self.assertTrue(tutorial.first_game_guide(True, None))
        self.assertFalse(tutorial.first_game_guide(True, True))
        self.assertFalse(tutorial.first_game_guide(False, None))

    def test_progress_field_omitted_until_set(self):
        from engine.domain.model import PlayerProgress
        from engine.domain.serde import from_jsonable, to_jsonable
        old = to_jsonable(PlayerProgress("p"))
        self.assertNotIn("tutorial_completed", old)                    # 기존 진척 문서·바이트 그대로
        self.assertIsNone(from_jsonable(PlayerProgress, old).tutorial_completed)
        done = to_jsonable(PlayerProgress("p", tutorial_completed=True))
        self.assertTrue(from_jsonable(PlayerProgress, done).tutorial_completed)


class ScriptPlaythrough(unittest.TestCase):
    def test_script_wins_on_day_two_and_is_deterministic(self):
        state, seen, h = play_script()
        self.assertEqual(seen, [(s.day, s.phase) for s in tutorial.STEPS])
        self.assertIsNotNone(state.result)
        self.assertEqual((state.result.winner.value, state.result.day), ("village", 2))
        self.assertEqual({play_script()[2] for _ in range(2)}, {h})


if __name__ == "__main__":
    unittest.main()
