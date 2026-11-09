"""E-10-09 프리셋 반례 테스트(짧은 판 5인·기본+ 7인·큰 판 8인).

각 프리셋에서 종료 판정 경계 세 가지를 실제 엔진(`PaldoEngine.apply`)으로 끝까지 진행해 확인한다.

- 마지막 씐 자 감금: 1일째 감금과, 창귀 동수 직전 날(마지막 기회) 감금 두 시점. 마을 승리, 밤 없음.
- 무당 감금: 1일째와 마지막 기회 날 두 시점. 창귀 승리, 밤 없음.
- G ≥ V + M 도달: 날마다 마을 주민을 1명씩 가두고 밤마다 1명씩 실종시켜, 마을 주민 NPC가 0이 되는
  순간에만 끝나는지(한 걸음 전에는 계속되는지) 본다. 마을 주민 수가 홀수인 5·7인 판은 감금 직후에,
  짝수인 8인 판은 밤 처리 직후에 끝나서 두 판정 시점을 모두 지난다.

NPC 표와 밤 실종은 `PlanPolicy`로 정해 주고, 단계·인원·종료 판정은 엔진이 한다. 콘텐츠는 주민 11명이 있는
`fixtures/content_min`을 쓴다(실제 번들 `content/bundle_v1.json`은 주민 6명이라 7·8인 판 배정이 안 된다).
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Optional

from engine.domain.engine import BaselinePolicy, PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import PLAYER_ID, PRESETS, CommandKind, NightPlan, Phase, Role
from engine.domain.observation import Decision, ReasonCode
from tests.engine.test_scenarios_engine import DEPS, drive_to, end_payload, env, meta, step

PRESET_IDS = ("short5", "base7", "large8")
SEEDS = (0, 1, 2)


class PlanPolicy(BaselinePolicy):
    """날짜별 감금 대상과 밤 실종자를 정해 주는 NPC 결정.

    locks[day] = 그날 모든 NPC가 표를 줄 대상(대상 본인은 다른 후보에게 준다).
    kills[day] = 그날 밤 씐 자가 실종시킬 주민. 정하지 않은 날은 기본 규칙을 쓴다.
    """

    def __init__(self, locks: dict[int, str], kills: dict[int, str]):
        self.locks = locks
        self.kills = kills

    def vote(self, obs, candidates, rng):
        target = self.locks.get(obs.day)
        if target is None:
            return super().vote(obs, candidates, rng)
        if obs.observer == target:
            target = sorted(c for c in candidates if c != obs.observer)[0]
        return Decision(target, [ReasonCode.SCRIPTED])

    def night_plan(self, obs, executors, victims, rng):
        victim = self.kills.get(obs.day)
        if victim is None:
            return super().night_plan(obs, executors, victims, rng)
        if victim not in victims:
            raise AssertionError(f"{obs.day}일째 밤 실종 대상 {victim}이 후보 {victims}에 없음")
        return Decision(NightPlan(sorted(executors)[0], victim), [ReasonCode.SCRIPTED])


@dataclass
class Game:
    """새 판의 1일째 아침 상태와 역할 구분(씐 자 1명, 마을 주민 NPC 목록)."""

    state: object
    truth: object
    possessed: str
    villagers: list[str]


def new_game(preset_id: str, seed: int) -> Game:
    """프리셋·시드로 판을 만든다(배정은 엔진이 시드로 뽑는다)."""
    r = PaldoEngine().new_game(meta(preset_id, seed, f"g_{preset_id}_{seed}"), DEPS)
    st, tr = r.state, r.truth
    poss = [n for n in st.active if tr.roles[n] is Role.POSSESSED]
    vill = sorted(n for n in st.active if tr.roles[n] is Role.VILLAGER)
    return Game(st, tr, poss[0], vill)


def race_plan(villagers: list[str]) -> tuple[dict[int, str], dict[int, str]]:
    """마을 주민을 앞에서부터 낮에 1명 감금, 밤에 1명 실종으로 번갈아 줄이는 계획."""
    locks: dict[int, str] = {}
    kills: dict[int, str] = {}
    for k, npc in enumerate(villagers):
        day = k // 2 + 1
        (locks if k % 2 == 0 else kills)[day] = npc
    return locks, kills


def last_chance_day(n_villagers: int) -> int:
    """창귀 동수 도달 전 마지막 감금 날(동수 경주에서 판이 끝나는 날)."""
    return (n_villagers + 1) // 2


@dataclass
class Trace:
    """판 진행 기록: 날마다 감금 직후 판정(end 값)과 밤 뒤 활동 인원 수(G, V, 무당 활동)."""

    lock_ends: list[tuple[int, str, Optional[str]]]
    after_night: list[tuple[int, int, int, bool]]
    state: object
    truth: object
    night_events: list


def play(t: unittest.TestCase, g: Game, locks: dict[int, str], kills: dict[int, str], max_days: int = 12) -> Trace:
    """판이 끝날 때까지 날마다 정한 대상을 가두고 밤에는 아직 점치지 않은 NPC를 점친다.

    플레이어는 아침 advance, 회의 침묵, 캐묻기 advance만 한다. 무당이 감금 대상이면 다른 NPC에게 표를 낸다.
    """
    engine = PaldoEngine(PlanPolicy(locks, kills))
    st, tr = g.state, g.truth
    lock_ends, after_night, night_events = [], [], []
    while st.phase is not Phase.ENDED:
        t.assertLessEqual(st.day, max_days, "판이 끝나지 않음")
        day = st.day
        st, tr = drive_to(t, engine, st, tr, Phase.VOTE)
        target = locks[day]
        own = target if target != PLAYER_ID else sorted(n for n in st.active if n != PLAYER_ID)[0]
        st, tr, r = step(t, engine, st, tr, CommandKind.VOTE, target=own)
        t.assertIsNot(st.phase, Phase.RUNOFF, f"{day}일째 결선으로 감")
        (locked, _, end), = end_payload(r.events)
        t.assertEqual(locked, target)
        lock_ends.append((day, locked, end))
        if st.phase is Phase.ENDED:
            break
        t.assertIs(st.phase, Phase.NIGHT)
        st, tr, r = divine_someone(t, engine, st, tr)
        night_events.append((day, r.events))
        gg = sum(1 for n in st.active if tr.roles[n] is Role.POSSESSED)
        vv = sum(1 for n in st.active if tr.roles[n] is Role.VILLAGER)
        after_night.append((day, gg, vv, PLAYER_ID in st.active))
    return Trace(lock_ends, after_night, st, tr, night_events)


def divine_someone(t, engine, st, tr):
    """밤에 아직 점치지 않은 활동 NPC 중 ID가 가장 앞선 사람을 점친다."""
    done = {target for per_night in st.player.divinations.values() for target in per_night}
    pool = sorted(n for n in st.active if n != PLAYER_ID)
    target = next((n for n in pool if n not in done), pool[0])
    r = engine.apply(st, tr, env(st, CommandKind.DIVINE, target=target), DEPS)
    t.assertTrue(r.accepted, f"점 거부: {r.reject} {r.reject_detail}")
    return r.state, r.truth, r


def assert_ended(t, trace: Trace, reason: str, winner: str):
    t.assertIs(trace.state.phase, Phase.ENDED)
    t.assertEqual(trace.state.result.reason.value, reason)
    t.assertEqual(trace.state.result.winner.value, winner)


def night_resolved(events) -> list[tuple[Optional[str], Optional[str]]]:
    """밤 처리 이벤트의 (실종자, 종료 판정) 목록."""
    return [(e.payload["victim"], e.payload["end"]) for e in events if e.event_type is EventType.NIGHT_RESOLVED]


class PresetShape(unittest.TestCase):
    """프리셋 구성이 기획서 3.1 표와 같고, 시드마다 판이 그 인원으로 시작한다."""

    WANT = {"short5": (5, 1, 1), "base7": (7, 1, 2), "large8": (8, 1, 3)}

    def test_shape_and_start(self):
        for pid in PRESET_IDS:
            p = PRESETS[pid]
            self.assertEqual((p.n_active, p.n_possessed, p.n_secret), self.WANT[pid])
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    g = new_game(pid, seed)
                    self.assertEqual(len(g.state.active), p.n_active)
                    self.assertIn(PLAYER_ID, g.state.active)
                    self.assertEqual(len(g.villagers), p.n_active - 1 - p.n_possessed)
                    self.assertEqual(len(g.state.missing), 1)
                    self.assertEqual(len(g.truth.secrets), p.n_secret)


class LastPossessedLocked(unittest.TestCase):
    """마지막 씐 자 감금: 감금 직후 마을 승리로 끝나고 그날 밤은 오지 않는다."""

    def _check(self, pid: str, seed: int, day: int):
        g = new_game(pid, seed)
        locks, kills = race_plan(g.villagers)
        locks = {d: x for d, x in locks.items() if d < day}
        kills = {d: x for d, x in kills.items() if d < day}
        locks[day] = g.possessed
        tr = play(self, g, locks, kills)
        assert_ended(self, tr, "all_possessed_locked", "village")
        self.assertEqual(tr.lock_ends[-1], (day, g.possessed, "all_possessed_locked"))
        self.assertTrue(all(end is None for _, _, end in tr.lock_ends[:-1]))
        self.assertEqual(len(tr.after_night), day - 1)   # 마지막 날 밤은 실행하지 않음
        self.assertEqual(tr.state.day, day)

    def test_day1(self):
        for pid in PRESET_IDS:
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    self._check(pid, seed, 1)

    def test_last_chance_day(self):
        """창귀 동수가 되기 직전 날(마지막 기회)에 씐 자를 가두면 동수보다 먼저 마을 승리로 끝난다."""
        for pid in PRESET_IDS:
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    g = new_game(pid, seed)
                    self._check(pid, seed, last_chance_day(len(g.villagers)))


class MudangLocked(unittest.TestCase):
    """무당 감금: 감금 직후 창귀 승리로 끝나고 그날 밤은 오지 않는다."""

    def _check(self, pid: str, seed: int, day: int):
        g = new_game(pid, seed)
        locks, kills = race_plan(g.villagers)
        locks = {d: x for d, x in locks.items() if d < day}
        kills = {d: x for d, x in kills.items() if d < day}
        locks[day] = PLAYER_ID
        tr = play(self, g, locks, kills)
        assert_ended(self, tr, "mudang_locked", "changgwi")
        self.assertEqual(tr.lock_ends[-1], (day, PLAYER_ID, "mudang_locked"))
        self.assertEqual(len(tr.after_night), day - 1)
        self.assertIn(PLAYER_ID, tr.state.locked)
        self.assertIn(g.possessed, tr.state.active)

    def test_day1(self):
        for pid in PRESET_IDS:
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    self._check(pid, seed, 1)

    def test_last_chance_day(self):
        for pid in PRESET_IDS:
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    g = new_game(pid, seed)
                    self._check(pid, seed, last_chance_day(len(g.villagers)))


class PossessedParity(unittest.TestCase):
    """G ≥ V + M 도달: 마을 주민 NPC가 0이 되는 순간(G=1, V=0, M=1)에만 끝나고 그 전에는 계속된다.

    마을 주민 수가 홀수(5·7인 판)면 감금 직후, 짝수(8인 판)면 밤 처리 직후에 끝난다.
    """

    def test_race_to_parity(self):
        for pid in PRESET_IDS:
            for seed in SEEDS:
                with self.subTest(preset=pid, seed=seed):
                    g = new_game(pid, seed)
                    n = len(g.villagers)
                    locks, kills = race_plan(g.villagers)
                    tr = play(self, g, locks, kills)
                    assert_ended(self, tr, "possessed_parity", "changgwi")
                    end_day = last_chance_day(n)
                    self.assertEqual(tr.state.day, end_day)
                    self.assertEqual(sorted(tr.state.active), sorted([PLAYER_ID, g.possessed]))
                    if n % 2:   # 감금 직후 종료, 그날 밤 없음
                        self.assertEqual(tr.lock_ends[-1][2], "possessed_parity")
                        self.assertEqual(len(tr.after_night), end_day - 1)
                    else:       # 밤 처리 직후 종료, 감금 판정은 모두 계속
                        self.assertTrue(all(end is None for _, _, end in tr.lock_ends))
                        self.assertEqual(len(tr.after_night), end_day)
                        self.assertEqual(tr.after_night[-1], (end_day, 1, 0, True))
                        self.assertEqual(night_resolved(tr.night_events[-1][1]), [(kills[end_day], "possessed_parity")])
                    # 한 걸음 전(V=1)까지는 판이 계속됨: 앞선 밤 처리 판정은 모두 None
                    for d, events in tr.night_events[:len(tr.night_events) - (0 if n % 2 else 1)]:
                        self.assertEqual(night_resolved(events), [(kills[d], None)])
                    for _, gg, vv, m in tr.after_night[:-1] if not n % 2 else tr.after_night:
                        self.assertEqual((gg, m), (1, True))
                        self.assertGreaterEqual(vv, 1)
                    self.assertTrue(all(end is None for _, _, end in tr.lock_ends[:-1]))

    def test_expected_end_point_per_preset(self):
        """프리셋별 종료 시점: 5인 2일째 감금, 7인 3일째 감금, 8인 3일째 밤."""
        want = {"short5": (2, "lock"), "base7": (3, "lock"), "large8": (3, "night")}
        for pid, (day, when) in want.items():
            n = PRESETS[pid].n_active - 2
            self.assertEqual((last_chance_day(n), "lock" if n % 2 else "night"), (day, when))


if __name__ == "__main__":
    unittest.main()
