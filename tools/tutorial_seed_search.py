"""튜토리얼 시드 검색. 규칙 수치·NPC 정책·콘텐츠가 바뀌어 튜토리얼 대본 시험이 깨졌을 때 시드를 다시 고른다.

사용:
  python tools/tutorial_seed_search.py [--seeds 2000] [--rule-base rule-0.9] [--abstain vote1|runoff1|none]
                                       [--with-fake] [--show 5]

진실을 읽는 검색 전용 대본으로 base6 판을 두고, 튜토리얼 조작을 모두 쓰고 2일째 투표로 무당이 이기는 시드를 찾는다.
1일: 아침 진행 → 회의1 모순 지적 → 캐묻기 행적(씐 자, 0일째 밤 한밤) → 캐묻기 모순(모순 상대) → 진행
     → 회의2 변호 → 투표(--abstain vote1이면 기권) → 결선(있으면, --abstain runoff1이면 기권) → 밤 점치기(씐 자)
2일: 아침 진행 → 회의1 점괘 공개(씐 자) → 캐묻기 진행 → 회의2 의심(씐 자) → 투표(씐 자) → 승리
NPC 의존성은 기본 suspicion=None(튜토리얼 판과 같은 모델 없음). --with-fake는 규칙 의심도 가짜 모델과 비교용이다.
고른 시드는 engine/domain/tutorial.py(SEED·RULE_BASE·STEPS)와 대사(server/api/tutorial_script.py)에 반영한다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.domain import params  # noqa: E402
from engine.domain.engine import PaldoEngine, abstain_margin_of, home_alibi  # noqa: E402
from engine.domain.model import (PLAYER_ID, PRESETS, Command, CommandEnvelope, CommandKind, DataSource,  # noqa: E402
                                 DivinationPayload, EvidenceRef, EvidenceType, GameMeta, InterrogationKind, Phase,
                                 Role, Slot)
from engine.domain.npc import RuleV0Policy  # noqa: E402
from engine.domain.serde import dumps  # noqa: E402
from engine.ports import EngineDeps  # noqa: E402
from engine.sim.bots import _conflicts  # noqa: E402


def make_engine(content, rule_version):
    """서버 `ContentPolicyEngine._for`와 같은 정책 구성(규칙 버전의 기권 문턱·이전 의심 상한). tools는 server를 import하지 않는다."""
    kw = {}
    if not home_alibi(rule_version):
        kw["q_max"] = params.LEGACY_DECISION.q_max
    margin = abstain_margin_of(rule_version)
    if margin is not None:
        kw["abstain_margin"] = margin
    return PaldoEngine(RuleV0Policy(content, **kw))


class Fail(Exception):
    pass


def possessed_of(truth):
    return sorted(n for n, r in truth.roles.items() if r is Role.POSSESSED)[0]


def claim_refs(ref):
    """모순 묶음 → 웹 양식과 같은 claim 근거 두 개."""
    return [EvidenceRef(EvidenceType.CLAIM, x) for x in ref.record_id.split("|")]


def script_step(view, truth, memo, abstain_at):
    """현재 화면에서 대본이 낼 (명령, 표시). 조건이 안 맞으면 Fail."""
    p = possessed_of(truth)
    npcs = sorted(set(view.active) - {PLAYER_ID})
    ph, day, used = view.phase, view.day, memo["used"]
    if ph is Phase.MORNING:
        return Command(CommandKind.ADVANCE, PLAYER_ID), "advance"
    if day == 1 and ph is Phase.ROUND1:
        conflicts = sorted(_conflicts(view), key=lambda x: (x[0] != p, x[0], x[1].record_id))
        if not conflicts:
            raise Fail("d1_no_conflict")
        memo["conflict"] = conflicts[0]
        return Command(CommandKind.POINT_CONTRADICTION, PLAYER_ID, evidence=claim_refs(conflicts[0][1])), "contradiction"
    if day == 1 and ph is Phase.INTERROGATION:
        if "iw" not in used:
            return Command(CommandKind.INTERROGATE, PLAYER_ID, addressee=p, interrogation_kind=InterrogationKind.WHEREABOUTS,
                           night=0, slot=Slot.MIDNIGHT), "iw"
        if "ic" not in used:
            t, ref = memo["conflict"]
            # 웹 양식은 모순 질문의 밤·시간 칸을 보내지 않는다(game.js).
            return Command(CommandKind.INTERROGATE, PLAYER_ID, addressee=t,
                           interrogation_kind=InterrogationKind.CONTRADICTION, evidence=claim_refs(ref)), "ic"
        return Command(CommandKind.ADVANCE, PLAYER_ID), "advance"
    if day == 1 and ph is Phase.ROUND2:
        villagers = [n for n in npcs if n != p]
        confessed = {c.npc_id for c in view.public.confessions}
        x = memo["conflict"][0]
        pick = x if x in confessed and x in villagers else sorted(villagers, key=lambda n: (n not in confessed, n == x, n))[0]
        return Command(CommandKind.DEFEND, PLAYER_ID, target=pick), "defend"
    if ph in (Phase.VOTE, Phase.RUNOFF):
        round_ = "vote" if ph is Phase.VOTE else "runoff"
        if day == 1 and abstain_at == f"{round_}1":
            return Command(CommandKind.ABSTAIN, PLAYER_ID), "abstain"
        cands = view.runoff_candidates or npcs
        if p not in cands:
            raise Fail("possessed_not_candidate")
        return Command(CommandKind.VOTE, PLAYER_ID, target=p), f"{round_}{day}"
    if ph is Phase.NIGHT:
        if p not in npcs:
            raise Fail("possessed_gone_at_night")
        return Command(CommandKind.DIVINE, PLAYER_ID, target=p), "divine"
    if day == 2 and ph is Phase.ROUND1:
        res = view.player.divinations.get(1, {}).get(p)
        if res is None:
            raise Fail("no_divination")
        return Command(CommandKind.REVEAL_DIVINATION, PLAYER_ID, divination=DivinationPayload(p, 1, res)), "reveal"
    if day == 2 and ph is Phase.INTERROGATION:
        return Command(CommandKind.ADVANCE, PLAYER_ID), "advance"
    if day == 2 and ph is Phase.ROUND2:
        return Command(CommandKind.SUSPECT, PLAYER_ID, target=p), "suspect"
    raise Fail(f"unexpected_{day}_{ph.value}")


def run(engine, deps, content, rule_version, seed, abstain_at):
    """대본대로 끝까지 둔다. (마지막 화면, 진실, 표시 목록, 기록, 이벤트 해시, 상태)."""
    meta = GameMeta(f"tutorial-search-{seed}", PRESETS["base6"], seed, DataSource.SCRIPTED, rule_version,
                    "paldo-engine", content.content_version, player_pseudonym="p")
    tr = engine.new_game(meta, deps)
    state, truth = tr.state, tr.truth
    trace = hashlib.sha256(dumps(tr.events).encode())
    memo = {"used": []}
    log = []
    for step in range(80):
        view = engine.player_view(state, content)
        if view.result is not None:
            return view, truth, memo["used"], log, trace.hexdigest(), state
        cmd, tag = script_step(view, truth, memo, abstain_at)
        t = engine.apply(state, truth, CommandEnvelope(meta.game_id, f"c{step:03d}", view.state_version, cmd), deps)
        if not t.accepted:
            raise Fail(f"reject_{tag}_{t.reject}")
        trace.update(dumps(cmd).encode())
        trace.update(dumps(t.events).encode())
        memo["used"].append(tag)
        log.append((view.state_version, view.day, view.phase.value, tag, cmd))
        state, truth = t.state, t.truth
        if state.result is not None and "divine" not in memo["used"]:
            raise Fail("ended_day1")
    raise Fail("too_long")


NEED = {"contradiction", "iw", "ic", "defend", "divine", "reveal", "suspect", "vote2"}


def flags(log, truth, state):
    """순위 기준: 모순 상대 고백, 1일째 씐 자 기색, 변호한 주민 1일째 감금, 1일째 감금 여부."""
    p = possessed_of(truth)
    x = next(c.evidence for _, _, _, tag, c in log if tag == "contradiction")
    claims = {c.claim_id: c for c in state.public.claims}
    speaker = claims[x[0].record_id].speaker if x[0].record_id in claims else None
    target = next(c.addressee for _, _, _, tag, c in log if tag == "ic")
    defended = next(c.target for _, _, _, tag, c in log if tag == "defend")
    conf = {c.npc_id for c in state.public.confessions}
    tell_p1 = any(t.shown and t.day == 1 and t.claim_id in claims and claims[t.claim_id].speaker == p
                  for t in state.public.tells)
    lock1 = next((lk.locked for lk in state.public.locks if lk.day == 1), None)
    npc_abstain1 = sum(1 for v in state.public.votes if v.day == 1 and v.target is None and v.voter != PLAYER_ID)
    return dict(target_confessed=target in conf, tell_p1=tell_p1, defended_locked=lock1 == defended,
                lock1=lock1, npc_abstain1=npc_abstain1, speaker=speaker)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=2000)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--rule-base", default=params.RULE_BASE_VERSION, help="규칙 버전 기본(예: rule-0.8, rule-0.9)")
    ap.add_argument("--abstain", choices=("none", "vote1", "runoff1"), default="none",
                    help="1일째 무당 기권 단계 위치(rule-0.9 이상)")
    ap.add_argument("--with-fake", action="store_true", help="규칙 의심도 가짜 모델 의존성으로 비교")
    ap.add_argument("--show", type=int, default=5)
    ap.add_argument("--content", default=str(ROOT / "content" / "bundle_v1.json"))
    args = ap.parse_args(argv)

    from tools.check_content import load_bundle
    content = load_bundle(Path(args.content))
    if args.with_fake:
        from engine.testing.fakes import FakeSuspicionModel
        deps = EngineDeps(content, FakeSuspicionModel())
    else:
        deps = EngineDeps(content, None)
    rule_version = f"{args.rule_base}{params.VERSION_JOIN}{params.DECISION_PARAMS_VERSION}"
    engine = make_engine(content, rule_version)
    need = NEED | ({"abstain"} if args.abstain != "none" else set())

    fails, found = {}, []
    for seed in range(args.start, args.start + args.seeds):
        try:
            view, truth, used, log, h, state = run(engine, deps, content, rule_version, seed, args.abstain)
        except Fail as e:
            fails[str(e)] = fails.get(str(e), 0) + 1
            continue
        if view.result.winner is None or view.result.winner.value != "village" or not need <= set(used) \
                or view.result.day != 2:
            k = f"result_{view.result.winner.value if view.result.winner else None}_day{view.result.day}"
            fails[k] = fails.get(k, 0) + 1
            continue
        found.append((seed, log, h, truth, state))

    print(f"rule={rule_version} content={content.content_version} deps={'fake' if args.with_fake else 'none'} "
          f"abstain={args.abstain} seeds={args.start}..{args.start + args.seeds - 1} found={len(found)}")
    print("fails:", dict(sorted(fails.items(), key=lambda x: -x[1])))
    rank = []
    for seed, log, h, truth, state in found:
        f = flags(log, truth, state)
        score = 2 * f["target_confessed"] + 2 * f["tell_p1"] - 3 * f["defended_locked"] - (f["lock1"] is not None)
        rank.append((-score, seed, f))
    rank.sort()
    names = {c.npc_id: c.name_ko for c in content.characters}
    print("ranked:")
    for sc, seed, f in rank[:15]:
        print(" ", seed, -sc, f)
    by_seed = {f[0]: f for f in found}
    for _, seed, _ in rank[:args.show]:
        _, log, h, truth, state = by_seed[seed]
        print(f"\n== seed {seed} trace {h[:16]} possessed={possessed_of(truth)}({names.get(possessed_of(truth))})")
        for version, day, phase, tag, cmd in log:
            print(f"   [{version}] {day} {phase} {tag} target={cmd.target} addressee={cmd.addressee} "
                  f"evidence={[e.record_id for e in cmd.evidence]}")
        print("   votes:", [(v.day, v.round, v.voter, v.target) for v in state.public.votes])
        print("   locks:", [(lk.day, lk.locked) for lk in state.public.locks], "missing:", list(state.missing))
    if rank:
        seed = rank[0][1]
        hashes = {run(engine, deps, content, rule_version, seed, args.abstain)[4] for _ in range(3)}
        print(f"\ndeterminism seed {seed}: {len(hashes) == 1 and by_seed[seed][2] in hashes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
