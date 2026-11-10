"""E-09-08: 원한 단서 획득·한풀이 해금·선택형 도전·진척 장애 복구.

- 규칙: `engine/domain/progress.py`(단서 고르기, 가설 완성, 결말 선택 가능 여부)
- 반영: `server/app/progress.py`(멱등 반영), `GameService.command`(명령 저장 직후 반영)
- 장애 복구: 진척 저장 실패를 주입하고 판 아웃박스의 `confession`·`game_ended`를 재처리해 복구, 중복 0
- 선택형 도전: 프리셋 옵션 3종의 규칙 차이(점 1회 제한, 비밀 3명, 목격 축소)

저장소 구현은 `PROGRESS_FACTORY`·`GAME_FACTORY`로 바꿔 끼운다(실제 Mongo는 tests/infra/test_mongo_repo.py).
"""
from __future__ import annotations

import random
import unittest
from dataclasses import replace
from pathlib import Path

from engine.domain import params
from engine.domain.content import GrudgeClue
from engine.domain.engine import PaldoEngine, divination_available, draw_sightings, make_setup
from engine.domain.events import EventEnvelope, EventType
from engine.domain.model import (
    CHALLENGE_PRESET_IDS, PRESETS, CommandEnvelope, CommandKind, ConfessionRecord, DataSource, GameMeta, Place, PlayerProgress, Preset, Slot,
)
from engine.domain.npc import RuleV0Policy
from engine.domain.progress import (
    EXORCISM, ClueGain, clue_gains, clue_gains_from_confessions, completed_hypotheses, confession_source_id, ending_available, ending_unlocks,
    haewon_ending, progress_events,
)
from engine.ports import EngineDeps
from engine.sim.bots import LogicBot
from engine.testing.fakes import FakeSuspicionModel, InMemoryGameRepository, InMemoryProgressRepository
from server.app.dto import StartGameInput, SubmitCommandInput
from server.app.progress import apply_progress
from server.app.usecases import GameService, ReviewNotReady
from tools.check_content import load_bundle

ROOT = Path(__file__).resolve().parents[2]
CONTENT = load_bundle(ROOT / "content/bundle_v1.json")
CLUES = CONTENT.grudge_clues
PLAYER = "player-e0908"

# 실제 콘텐츠·규칙 엔진·LogicBot으로 고백이 나오는 base6 시드(이 커밋에서 확인, rule-0.7 꾀어내기 규칙으로 다시 찾음).
# 시드 655: 곡식(grain) 고백 → clue_famine(identity), 노름(gambling) 고백 → clue_debt(death)
# 시드 15: 술(drinking) 고백 → clue_gate(target). 두 판을 이어 하면 가설 h1의 세 부분이 모인다.
SEED_TWO_CLUES = 655
SEED_TARGET_CLUE = 15

PROGRESS_FACTORY = InMemoryProgressRepository
GAME_FACTORY = InMemoryGameRepository


def clue(cid, part, hyp="h1"):
    return GrudgeClue(cid, "본문", f"secret:{cid}", part, hyp)


def event(event_type, payload, game_id="g1", day=1, seq=0):
    return EventEnvelope(
        event_id=f"{game_id}:{seq:05d}", game_id=game_id, seq=seq, state_version=seq, event_type=event_type.value,
        topic="paldo.game", occurred_at="2026-11-09T00:00:00+00:00", day=day, phase="interrogation",
        source=DataSource.HUMAN, contract_version="c", preset_id="base6", seed=1, rule_version="r",
        engine_version="e", content_version=CONTENT.content_version, npc_policy_versions={},
        model_versions={}, payload=payload)


def confession(npc, clue_id, game_id="g1", day=1, seq=0):
    return event(EventType.CONFESSION, {"npc_id": npc, "secret_id": "s", "grudge_clue_id": clue_id,
                                        "corrected_claims": [], "claim_ids": []}, game_id, day, seq)


# ---------------------------------------------------------------- 규칙(도메인)
class ClueRules(unittest.TestCase):
    def test_confession_gives_clue_and_skips_unknown_or_missing(self):
        events = [confession("jumo", "clue_famine", seq=1), confession("meosum", None, seq=2),
                  confession("gwabu", "clue_unknown", seq=3), event(EventType.SPEECH_MADE, {"speech_id": "s1"}, seq=4)]
        self.assertEqual(clue_gains(events, CLUES),
                         [ClueGain("clue_famine", "g1", 1, confession_source_id("g1", "jumo"))])

    def test_same_confession_twice_in_input_counts_once(self):
        e = confession("jumo", "clue_famine", seq=1)
        self.assertEqual(len(clue_gains([e, replace(e, event_id="g1:00009", seq=9)], CLUES)), 1)

    def test_public_confession_record_gives_same_key_as_event(self):
        recs = [ConfessionRecord(2, "jumo", "grain"), ConfessionRecord(2, "hunjang", "letter"),
                ConfessionRecord(3, "jumo", "grain")]
        got = clue_gains_from_confessions("g1", recs, CONTENT.secrets, CLUES)
        self.assertEqual(got, [ClueGain("clue_famine", "g1", 2, confession_source_id("g1", "jumo"))])
        self.assertEqual(got[0].source_event_id, clue_gains([confession("jumo", "clue_famine", day=2)], CLUES)[0].source_event_id)

    def test_hypothesis_needs_all_three_parts_of_same_hypothesis(self):
        clues = [clue("a", "identity"), clue("b", "death"), clue("c", "target"),
                 clue("x", "identity", "h2"), clue("y", "death", "h2"), clue("z2", "target", "h2")]
        self.assertEqual(completed_hypotheses({"a", "b"}, clues), [])
        self.assertEqual(completed_hypotheses({"a", "b", "c"}, clues), ["h1"])
        # 다른 가설의 부분은 합치지 않는다
        self.assertEqual(completed_hypotheses({"a", "b", "z2"}, clues), [])
        self.assertEqual(completed_hypotheses({"a", "b", "c", "x", "y", "z2"}, clues), ["h1", "h2"])

    def test_two_identity_clues_fill_one_part(self):
        self.assertEqual(completed_hypotheses({"clue_famine", "clue_valley", "clue_debt"}, CLUES), [])
        self.assertEqual(completed_hypotheses({"clue_valley", "clue_debt", "clue_gate"}, CLUES), ["h1"])

    def test_unlock_only_new_endings_and_exorcism_always_available(self):
        p = PlayerProgress(PLAYER)
        for cid in ("clue_famine", "clue_debt", "clue_gate"):
            p.add_clue(cid, "g", 1)
        self.assertEqual([u.ending for u in ending_unlocks(p, CLUES)], [haewon_ending("h1")])
        p.endings_unlocked.append("haewon:h1")
        self.assertEqual(ending_unlocks(p, CLUES), [])
        empty = replace(PlayerProgress(PLAYER), endings_unlocked=[])
        self.assertTrue(ending_available(empty, EXORCISM))
        self.assertFalse(ending_available(empty, "haewon:h1"))
        self.assertTrue(ending_available(p, "haewon:h1"))

    def test_progress_keeps_only_out_of_game_fields(self):
        """판 밖 상태는 단서·결말·판 ID뿐이다. NPC 믿음·기억·공개 기록 필드가 없다."""
        self.assertEqual(set(PlayerProgress.__dataclass_fields__),
                         {"player_pseudonym", "game_ids", "clues", "confessed_secrets_seen",
                          "endings_unlocked", "content_version"})

    def test_progress_events_filter(self):
        es = [confession("jumo", "clue_famine", seq=1), event(EventType.SPEECH_MADE, {}, seq=2),
              event(EventType.GAME_ENDED, {}, seq=3)]
        self.assertEqual([e.seq for e in progress_events(es)], [1, 3])


# ---------------------------------------------------------------- 반영(응용, 가짜 저장소)
class ApplyProgress(unittest.TestCase):
    def test_apply_is_idempotent_and_unlocks_after_third_part(self):
        repo = PROGRESS_FACTORY()
        first = apply_progress(repo, PLAYER, [confession("jumo", "clue_famine", "g1"),
                                              confession("meosum", "clue_debt", "g1", day=2, seq=1)], CLUES)
        self.assertEqual((first.clues, first.endings), (["clue_famine", "clue_debt"], []))
        again = apply_progress(repo, PLAYER, [confession("jumo", "clue_famine", "g1")], CLUES)
        self.assertEqual((again.clues, again.endings), ([], []))
        third = apply_progress(repo, PLAYER, [confession("hunjang", "clue_gate", "g2")], CLUES)
        self.assertEqual((third.clues, third.endings), (["clue_gate"], ["haewon:h1"]))
        p = repo.load(PLAYER)
        self.assertEqual(sorted(p.clues), ["clue_debt", "clue_famine", "clue_gate"])
        self.assertEqual(p.clues["clue_famine"].first_game_id, "g1")
        self.assertEqual(p.endings_unlocked, ["exorcism", "haewon:h1"])

    def test_same_clue_from_later_game_is_ignored(self):
        repo = PROGRESS_FACTORY()
        apply_progress(repo, PLAYER, [confession("jumo", "clue_famine", "g1", day=1)], CLUES)
        later = apply_progress(repo, PLAYER, [confession("gwabu", "clue_famine", "g2", day=3)], CLUES)
        self.assertEqual(later.clues, [])
        rec = repo.load(PLAYER).clues["clue_famine"]
        self.assertEqual((rec.first_game_id, rec.first_day), ("g1", 1))


# ---------------------------------------------------------------- 게임 흐름(실제 엔진 + GameService)
class FailingProgress:
    """진척 저장소 장애 주입: 지정한 연산이 `fail_times`번 실패한다(판 저장 뒤 진척 반영 전 프로세스 중단 대역)."""

    def __init__(self, inner, op: str, fail_times: int = 10 ** 6):
        self.inner, self.op, self.left = inner, op, fail_times

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if name != self.op:
            return attr

        def failing(*a, **k):
            if self.left > 0:
                self.left -= 1
                raise ConnectionError(f"injected {name} failure")
            return attr(*a, **k)
        return failing


def make_service(seeds, games=None, progress=None):
    """웹 운영 조립과 같은 엔진(규칙 정책·실제 콘텐츠). 판마다 `seeds`에서 시드를 하나씩 꺼낸다."""
    queue = list(seeds)
    engine = PaldoEngine(RuleV0Policy(CONTENT))

    def prepare(data, player):
        seed = queue.pop(0)
        return GameMeta(f"e0908_{seed}", PRESETS[data.preset_id], seed, DataSource.HUMAN, params.RULE_PARAMS_VERSION,
                        "paldo-engine", CONTENT.content_version, player_pseudonym=player)
    return GameService(engine, games or GAME_FACTORY(), progress or PROGRESS_FACTORY(),
                       lambda meta: EngineDeps(CONTENT, FakeSuspicionModel()), prepare)


def play(service, seed, ending=EXORCISM):
    """LogicBot이 판 하나를 끝까지 둔다(봇 난수는 판 시드). 반환: 판 ID."""
    out = service.start(StartGameInput(PLAYER, "base6", None, "f", ending_choice=ending), PLAYER, True)
    view, rng, bot = out.view, random.Random(seed), LogicBot()
    for i in range(500):
        if view.result is not None:
            return view.game_id
        cmd = bot.choose(view, rng)
        res = service.command(SubmitCommandInput(view.game_id, f"c{i:04d}", view.state_version, cmd), PLAYER)
        assert res.accepted, (seed, i, cmd.kind, res.reject)
        view = res.view
    raise AssertionError("판이 끝나지 않음")


def outbox(games, game_id):
    """판 문서 아웃박스 전체(발행 여부 무관, 재처리 입력). 가짜는 docs, Mongo 구현은 판 문서 `outbox`를 읽는다.
    재처리 도구(C-09-04 `tools/replay_progress.py`)가 생기면 그 읽기 경로로 바꾼다."""
    if hasattr(games, "docs"):
        return list(games.docs[game_id]["outbox"])
    from engine.domain.serde import from_jsonable
    document = games.collection.find_one({"_id": game_id}, {"outbox": 1})
    return [from_jsonable(EventEnvelope, row["envelope"]) for row in document["outbox"]]


class GameFlow(unittest.TestCase):
    def test_confession_in_play_adds_clue_and_unlock_opens_haewon_choice(self):
        progress = PROGRESS_FACTORY()
        service = make_service([SEED_TWO_CLUES, SEED_TARGET_CLUE, 1], progress=progress)
        g1 = play(service, SEED_TWO_CLUES)
        p = progress.load(PLAYER)
        self.assertEqual(sorted(p.clues), ["clue_debt", "clue_famine"])
        self.assertEqual(p.endings_unlocked, ["exorcism"])
        # 아직 열리지 않은 한풀이는 판 시작 때 고를 수 없다
        with self.assertRaisesRegex(ValueError, "ending_locked"):
            service.start(StartGameInput(PLAYER, "base6", None, "f", ending_choice="haewon:h1"), PLAYER, True)
        play(service, SEED_TARGET_CLUE)
        p = progress.load(PLAYER)
        self.assertEqual(sorted(p.clues), ["clue_debt", "clue_famine", "clue_gate"])
        self.assertEqual(p.endings_unlocked, ["exorcism", "haewon:h1"])
        self.assertEqual(service.review(g1, PLAYER).clues_gained, ["clue_debt", "clue_famine"])
        g3 = play(service, 1, ending="haewon:h1")
        state, _ = service.repository.load(g3)
        self.assertEqual(state.meta.ending_choice, "haewon:h1")

    def test_progress_does_not_change_next_game(self):
        """판 밖 진척이 있어도 다음 판의 NPC 상태·배정은 진척 없는 판과 같다(판 안 기억은 버린다)."""
        with_progress = make_service([SEED_TWO_CLUES, 1])
        play(with_progress, SEED_TWO_CLUES)
        a = with_progress.start(StartGameInput(PLAYER, "base6", None, "f"), PLAYER, True)
        fresh = make_service([1])
        b = fresh.start(StartGameInput(PLAYER, "base6", None, "f"), PLAYER, True)
        sa, _ = with_progress.repository.load(a.view.game_id)
        sb, _ = fresh.repository.load(b.view.game_id)
        self.assertEqual(sa.npcs, sb.npcs)
        self.assertEqual(sa.public, sb.public)


class FailureRecovery(unittest.TestCase):
    """완료 조건 4: 고백 이벤트 저장 후 진척 반영 전 장애 → 아웃박스 재처리로 복구, 중복 획득 0."""

    def replay(self, games, game_ids, progress):
        events = [e for g in game_ids for e in progress_events(outbox(games, g))]
        return apply_progress(progress, PLAYER, events, CLUES)

    def test_clue_write_failure_recovered_by_replay(self):
        games, inner = GAME_FACTORY(), PROGRESS_FACTORY()
        service = make_service([SEED_TWO_CLUES, SEED_TARGET_CLUE], games, FailingProgress(inner, "add_clue"))
        with self.assertLogs("paldo.progress", "ERROR"):
            ids = [play(service, SEED_TWO_CLUES), play(service, SEED_TARGET_CLUE)]
        self.assertEqual(inner.load(PLAYER).clues, {})        # 판은 끝났지만 진척은 비어 있다
        first = self.replay(games, ids, inner)
        self.assertEqual(sorted(first.clues), ["clue_debt", "clue_famine", "clue_gate"])
        self.assertEqual(first.endings, ["haewon:h1"])
        second = self.replay(games, ids, inner)                # 두 번 돌려도 같다
        self.assertEqual((second.clues, second.endings), ([], []))
        p = inner.load(PLAYER)
        self.assertEqual(len(p.clues), 3)
        self.assertEqual(p.endings_unlocked, ["exorcism", "haewon:h1"])
        self.assertEqual(p.clues["clue_famine"].first_game_id, ids[0])

    def test_unlock_failure_after_clue_saved_recovered_by_replay(self):
        games, inner = GAME_FACTORY(), PROGRESS_FACTORY()
        service = make_service([SEED_TWO_CLUES, SEED_TARGET_CLUE], games, FailingProgress(inner, "unlock_ending"))
        with self.assertLogs("paldo.progress", "ERROR"):
            ids = [play(service, SEED_TWO_CLUES), play(service, SEED_TARGET_CLUE)]
        p = inner.load(PLAYER)
        self.assertEqual(len(p.clues), 3)
        self.assertEqual(p.endings_unlocked, ["exorcism"])     # 단서는 들어갔고 해금 전에 멈춤
        rec = self.replay(games, ids, inner)
        self.assertEqual((rec.clues, rec.endings), ([], ["haewon:h1"]))
        self.assertEqual(inner.load(PLAYER).endings_unlocked, ["exorcism", "haewon:h1"])

    def test_transient_failure_caught_up_at_game_end_without_duplicates(self):
        """첫 고백 반영만 실패: 판 종료 명령의 따라잡기(`sweep_game`)가 복구하고, 재처리를 더 돌려도 중복 0."""
        games, inner = GAME_FACTORY(), PROGRESS_FACTORY()
        service = make_service([SEED_TWO_CLUES], games, FailingProgress(inner, "add_clue", fail_times=1))
        with self.assertLogs("paldo.progress", "ERROR"):
            gid = play(service, SEED_TWO_CLUES)
        self.assertEqual(sorted(inner.load(PLAYER).clues), ["clue_debt", "clue_famine"])
        rec = self.replay(games, [gid], inner)
        self.assertEqual((rec.clues, rec.endings), ([], []))
        self.assertEqual(len(inner.load(PLAYER).clues), 2)

    def test_command_retry_and_review_catch_up(self):
        """판 도중 반영이 실패한 뒤 같은 명령 ID 재요청, 또는 종료 뒤 복기 조회가 진척을 따라잡는다."""
        games, inner = GAME_FACTORY(), PROGRESS_FACTORY()
        failing = FailingProgress(inner, "add_clue")
        service = make_service([SEED_TWO_CLUES], games, failing)
        out = service.start(StartGameInput(PLAYER, "base6", None, "f"), PLAYER, True)
        view, rng, bot = out.view, random.Random(SEED_TWO_CLUES), LogicBot()
        with self.assertLogs("paldo.progress", "ERROR"):
            for i in range(500):
                cmd = bot.choose(view, rng)
                data = SubmitCommandInput(view.game_id, f"c{i:04d}", view.state_version, cmd)
                res = service.command(data, PLAYER)
                view = res.view
                if res.view.public.confessions:
                    break
        self.assertEqual(inner.load(PLAYER).clues, {})
        failing.left = 0                                       # 장애가 풀린 뒤 같은 명령 재요청
        again = service.command(data, PLAYER)
        self.assertTrue(again.replayed)
        self.assertEqual(sorted(inner.load(PLAYER).clues), ["clue_famine"])
        with self.assertRaises(ReviewNotReady):
            service.review(view.game_id, PLAYER)               # 종료 전에는 복기를 열 수 없다
        while view.result is None:
            i += 1
            res = service.command(SubmitCommandInput(view.game_id, f"c{i:04d}", view.state_version,
                                                     bot.choose(view, rng)), PLAYER)
            view = res.view
        # 복기 조회도 같은 따라잡기를 한다: 진척이 비어 있는 저장소로 바꿔도 복기 때 이 판의 단서가 채워진다
        service.progress = PROGRESS_FACTORY()
        review = service.review(view.game_id, PLAYER)
        self.assertEqual(review.clues_gained, ["clue_debt", "clue_famine"])


class RecoveryNeverBlocks(unittest.TestCase):
    def test_content_lookup_failure_does_not_block_replay_or_review(self):
        """따라잡기 중 콘텐츠 조회가 실패해도 같은 명령 재요청·복기는 저장된 결과를 돌려준다."""
        service = make_service([SEED_TWO_CLUES])
        gid = play(service, SEED_TWO_CLUES)
        good = service.dependencies

        def broken(meta):
            raise KeyError("content version unavailable")
        service.dependencies = broken
        with self.assertLogs("paldo.progress", "ERROR"):
            review = service.review(gid, PLAYER)
        self.assertEqual(review.clues_gained, ["clue_debt", "clue_famine"])
        saved = next(iter(service.repository.docs[gid]["commands"].values()))
        with self.assertLogs("paldo.progress", "ERROR"):
            out = service.replay_command(gid, saved, PLAYER)
        self.assertTrue(out.replayed)
        service.dependencies = good


# ---------------------------------------------------------------- 선택형 도전(프리셋 옵션)
def new_state(preset_id, seed):
    engine = PaldoEngine(RuleV0Policy(CONTENT))
    meta = GameMeta(f"ch_{preset_id}_{seed}", PRESETS[preset_id], seed, DataSource.BOT, params.RULE_PARAMS_VERSION,
                    "paldo-engine", CONTENT.content_version)
    r = engine.new_game(meta, EngineDeps(CONTENT, FakeSuspicionModel()))
    return engine, r.state, r.truth


class ChallengePresets(unittest.TestCase):
    def test_three_challenges_registered_on_base6(self):
        self.assertEqual(sorted(PRESETS[p].challenge for p in CHALLENGE_PRESET_IDS),
                         ["divination_once", "fewer_sightings", "secret3"])
        for pid in CHALLENGE_PRESET_IDS:
            p = PRESETS[pid]
            self.assertEqual((p.n_active, p.n_possessed), (6, 1), pid)
            self.assertEqual(p.status, "candidate", pid)

    def test_secret3_assigns_three_secrets(self):
        for seed in range(1, 30):
            _, _, tr = new_state("base6_secret3", seed)
            self.assertEqual(len(tr.secrets), 3, seed)

    def test_divination_once_gives_one_result_then_nights_pass(self):
        engine, st, tr = new_state("base6_div1", 3)
        deps = EngineDeps(CONTENT, FakeSuspicionModel())
        rng, bot, nights = random.Random(3), LogicBot(), 0
        for i in range(500):
            if st.result is not None:
                break
            view = engine.player_view(st, CONTENT)
            cmd = bot.choose(view, rng)
            if cmd.kind is CommandKind.DIVINE:
                nights += 1
                self.assertEqual(divination_available(st), nights == 1)
            r = engine.apply(st, tr, CommandEnvelope(st.meta.game_id, f"c{i}", st.state_version, cmd), deps)
            self.assertTrue(r.accepted)
            if cmd.kind is CommandKind.DIVINE and nights > 1:
                self.assertNotIn(EventType.DIVINATION_DONE, [e.event_type for e in r.events])
            st, tr = r.state, r.truth
        self.assertLessEqual(sum(len(v) for v in st.player.divinations.values()), 1)

    def test_witness_scale_reduces_sightings_and_default_is_unchanged(self):
        people = [f"p{i}" for i in range(6)]
        where = {p: {s: Place.TAVERN for s in Slot} for p in people}
        full = draw_sightings(random.Random(1), 0, people, where)
        same = draw_sightings(random.Random(1), 0, people, where, 1.0)
        half = draw_sightings(random.Random(1), 0, people, where, 0.5)
        count = lambda s: sum(len(x.seen) for v in s.values() for x in v)
        self.assertEqual(full, same)
        self.assertLess(count(half), count(full))

    def test_default_presets_unchanged_by_new_fields(self):
        """기본 프리셋의 배정·목격은 도전 필드가 생기기 전과 같다(배율 1.0, 점 제한 없음)."""
        p = PRESETS["base6"]
        self.assertIsNone(p.divination_limit)
        self.assertEqual(p.witness_scale, 1.0)
        a = make_setup(p, CONTENT, 7, "g")
        b = make_setup(replace(p, witness_scale=1.0), CONTENT, 7, "g")
        self.assertEqual(a, b)

    def test_invalid_challenge_values_rejected(self):
        with self.assertRaises(ValueError):
            Preset("x", 6, 1, 2, divination_limit=-1)
        with self.assertRaises(ValueError):
            Preset("x", 6, 1, 2, witness_scale=1.5)


if __name__ == "__main__":
    unittest.main()
