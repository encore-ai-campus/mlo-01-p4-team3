"""INT-07 #2 웹 재현용 시드·버튼 순서 찾기: 점괘·고백 전환, 마지막 씐 자 감금, 거짓 점괘 공개.

사람이 판을 진행하면서 반례 상황이 저절로 나오기를 기다리지 않도록, 웹 운영 조립과 같은 엔진 구성
(`PaldoEngine(RuleV0Policy(content))` + `FakeSuspicionModel`, 콘텐츠 `content/bundle_v1.json`, base6)으로
시드와 플레이어 명령 순서를 미리 찾는다. 엔진은 같은 시드·같은 명령이면 같은 판을 만들므로(engine.md),
찾은 순서를 게임 웹 고정 회차(같은 시드)에서 그대로 누르면 같은 상황이 나온다. 진실(씐 자·비밀 보유자)은
찾는 쪽에서만 쓰고, 결과 JSON에는 시드·명령·확인값만 남긴다.

    python -m tools.perf.int07_scenarios --out <결과 JSON>

계층: `check_*`(도메인, 화면 정보·로그로 상황 성립 판정) / `choose`(도메인, 목표별 명령 고르기) /
`play`·`search`(응용, 엔진 진행) / `main`(표현). 표준 라이브러리와 엔진만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from engine.domain import params
from engine.domain.engine import PaldoEngine
from engine.domain.events import EventType
from engine.domain.model import (
    PRESETS, Command, CommandEnvelope, CommandKind, DataSource, DivinationPayload, DivinationResult, EndReason,
    GameMeta, InterrogationKind, Role, Slot,
)
from engine.domain.npc import RuleV0Policy
from engine.domain.serde import to_jsonable
from engine.ports import EngineDeps
from engine.testing.fakes import FakeSuspicionModel
from tools.check_content import load_bundle

ROOT = Path(__file__).resolve().parents[2]
PLAYER = "mudang"
GOALS = ("false_reveal", "last_possessed", "confession_switch")
MAX_STEPS = 200


# ---------------------------------------------------------------- 도메인(성립 판정·명령 고르기)
def check_false_reveal(view, target: str) -> bool:
    """거짓 점괘 공개: 공개 기록의 마지막 점괘 공개가 맑음이고 무당 개인 기록(같은 밤·대상)은 탁함."""
    reveals = [c for c in view.public.claims if c.divination_result is not None]
    if not reveals:
        return False
    c = reveals[-1]
    actual = view.player.divinations.get(c.night, {}).get(c.subject)
    return c.subject == target and c.divination_result is DivinationResult.CLEAR and actual is DivinationResult.MURKY


def check_last_possessed(view) -> bool:
    """마지막 씐 자 감금: 판이 '씐 자 전원 감금'으로 마을 승리로 끝났다."""
    return view.result is not None and view.result.reason is EndReason.ALL_POSSESSED_LOCKED


def check_confession_switch(view, target: str) -> bool:
    """점괘·고백 전환: 같은 대상의 점괘가 고백 전 밤에는 탁함, 고백한 날 이후 밤에는 맑음."""
    conf = [c for c in view.public.confessions if c.npc_id == target]
    if not conf:
        return False
    day = conf[0].day
    divs = {n: d[target] for n, d in view.player.divinations.items() if target in d}
    before = [n for n, r in divs.items() if r is DivinationResult.MURKY and n < day]
    after = [n for n, r in divs.items() if r is DivinationResult.CLEAR and n >= day]
    return bool(before and after)


def choose(goal: str, view, aim: str, rng: random.Random):
    """목표별 플레이어 명령. 없으면 None(그때는 진행용 명령). `aim`은 목표 대상(씐 자 또는 비밀 보유자)."""
    legal, npcs = view.legal_kinds, sorted(n for n in view.active if n != PLAYER)
    cands = sorted(view.runoff_candidates) or npcs
    if goal == "last_possessed":
        if CommandKind.VOTE in legal:
            return Command(CommandKind.VOTE, PLAYER, target=aim if aim in cands else rng.choice(cands))
        if CommandKind.DIVINE in legal:
            return Command(CommandKind.DIVINE, PLAYER, target=rng.choice(npcs))
        if CommandKind.SUSPECT in legal and rng.random() < 0.8:
            return Command(CommandKind.SUSPECT, PLAYER, target=aim)
        return None
    if CommandKind.DIVINE in legal:
        return Command(CommandKind.DIVINE, PLAYER, target=aim)
    if CommandKind.VOTE in legal:
        pick = [c for c in cands if c not in (aim, PLAYER)]
        return Command(CommandKind.VOTE, PLAYER, target=rng.choice(pick or cands))
    if goal == "false_reveal" and CommandKind.REVEAL_DIVINATION in legal and view.player.reveals_left_today > 0:
        murky = [(n, t) for n, d in sorted(view.player.divinations.items()) for t, r in sorted(d.items())
                 if r is DivinationResult.MURKY and t == aim]
        if murky:
            n, t = murky[0]
            return Command(CommandKind.REVEAL_DIVINATION, PLAYER, divination=DivinationPayload(t, n, DivinationResult.CLEAR))
    if goal == "confession_switch" and view.day >= 2:
        if CommandKind.INTERROGATE in legal and view.player.interrogations_left > 0:
            if rng.random() < 0.5:
                return Command(CommandKind.INTERROGATE, PLAYER, addressee=aim,
                               interrogation_kind=InterrogationKind.SUSPICION_REASON)
            return Command(CommandKind.INTERROGATE, PLAYER, addressee=aim, interrogation_kind=InterrogationKind.WHEREABOUTS,
                           night=rng.randrange(view.day), slot=rng.choice(list(Slot)))
    if goal == "confession_switch" and CommandKind.SUSPECT in legal and view.day >= 1 and rng.random() < 0.6:
        return Command(CommandKind.SUSPECT, PLAYER, target=aim)
    return None


def filler(view, rng: random.Random):
    """목표와 무관한 진행 명령: 넘어가기 → 침묵 → 아무나 의심."""
    legal = view.legal_kinds
    if CommandKind.ADVANCE in legal:
        return Command(CommandKind.ADVANCE, PLAYER)
    if CommandKind.SILENCE in legal:
        return Command(CommandKind.SILENCE, PLAYER)
    if CommandKind.SUSPECT in legal:
        return Command(CommandKind.SUSPECT, PLAYER, target=rng.choice(sorted(n for n in view.active if n != PLAYER)))
    return None


# ---------------------------------------------------------------- 응용(엔진 진행)
class Lab:
    """웹 운영 조립과 같은 엔진 구성."""

    def __init__(self, bundle_path: Path = ROOT / "content/bundle_v1.json"):
        self.content = load_bundle(bundle_path)
        self.deps = EngineDeps(self.content, FakeSuspicionModel())
        self.engine = PaldoEngine(RuleV0Policy(self.content))

    def new(self, seed: int):
        """base6 새 판(상태, 진실)."""
        meta = GameMeta(f"int07_scn_{seed}", PRESETS["base6"], seed, DataSource.HUMAN, params.RULE_PARAMS_VERSION,
                        "paldo-engine", self.content.content_version)
        r = self.engine.new_game(meta, self.deps)
        return r.state, r.truth

    def view(self, st):
        """플레이어 화면 정보."""
        return self.engine.player_view(st, self.content)

    def apply(self, st, tr, cmd: Command):
        """명령 하나 적용."""
        return self.engine.apply(st, tr, CommandEnvelope(st.meta.game_id, f"c{st.state_version}", st.state_version, cmd),
                                 self.deps)


def play(lab: Lab, goal: str, seed: int, strategy: int):
    """한 판을 목표대로 진행해 상황이 성립하면 결과 dict, 아니면 None."""
    rng = random.Random(f"{seed}:{strategy}")
    st, tr = lab.new(seed)
    possessed = sorted(n for n, r in tr.roles.items() if r is Role.POSSESSED)
    holders = sorted(n for n in tr.secrets if tr.roles.get(n) is Role.VILLAGER)
    aim = possessed[0] if goal == "last_possessed" else (rng.choice(holders) if holders else None)
    if aim is None:
        return None
    cmds = []
    for _ in range(MAX_STEPS):
        v = lab.view(st)
        if goal != "last_possessed" and aim not in v.active:
            return None
        if goal == "false_reveal" and check_false_reveal(v, aim) or goal == "confession_switch" and \
                check_confession_switch(v, aim) or goal == "last_possessed" and check_last_possessed(v):
            return {"goal": goal, "seed": seed, "strategy": strategy, "target": aim, "commands": cmds}
        if st.result is not None:
            return None
        cmd = choose(goal, v, aim, rng) or filler(v, rng)
        if cmd is None:
            return None
        r = lab.apply(st, tr, cmd)
        if not r.accepted:
            return None
        cmds.append(to_jsonable(cmd))
        st, tr = r.state, r.truth
    return None


def search(lab: Lab, goal: str, seeds=range(1, 400), strategies: int = 40, stop_after: int = 40):
    """가장 짧은 명령 순서. 시드를 차례로 보고 `stop_after`번째 시드까지 찾았으면 멈춘다."""
    best = None
    for seed in seeds:
        for k in range(strategies):
            res = play(lab, goal, seed, k)
            if res and (best is None or len(res["commands"]) < len(best["commands"])):
                best = res
        if best and seed >= stop_after:
            break
    return best


def replay(lab: Lab, found: dict) -> bool:
    """찾은 명령을 처음부터 다시 적용해 같은 상황이 나는지(결정성 확인)."""
    st, tr = lab.new(found["seed"])
    for c in found["commands"]:
        from engine.domain.serde import from_jsonable
        r = lab.apply(st, tr, from_jsonable(Command, c))
        if not r.accepted:
            return False
        st, tr = r.state, r.truth
    v = lab.view(st)
    return {"false_reveal": lambda: check_false_reveal(v, found["target"]),
            "last_possessed": lambda: check_last_possessed(v),
            "confession_switch": lambda: check_confession_switch(v, found["target"])}[found["goal"]]()


# ---------------------------------------------------------------- 표현(CLI)
def main(argv=None) -> int:
    """CLI. 세 상황을 찾아 JSON으로 쓴다. 하나라도 못 찾으면 종료 코드 1."""
    ap = argparse.ArgumentParser(prog="python -m tools.perf.int07_scenarios")
    ap.add_argument("--out", required=True, help="결과 JSON 경로(저장소 밖 또는 out/)")
    a = ap.parse_args(argv)
    lab = Lab()
    out = {"rule_version": params.RULE_PARAMS_VERSION, "content_version": lab.content.content_version, "preset": "base6"}
    for goal in GOALS:
        res = search(lab, goal)
        out[goal] = res
        print(goal, None if res is None else (res["seed"], len(res["commands"]), replay(lab, res)))
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0 if all(out[g] for g in GOALS) else 1


if __name__ == "__main__":
    sys.exit(main())
