"""계약 픽스처 생성기. 계약 타입으로 만들고 serde로 직렬화하므로 픽스처가 항상 스키마를 지킨다.

실행: paldo/ 에서 `python -m tools.build_fixtures`
결과는 결정적이다(시드·시각 고정). 생성물은 저장소에 커밋하고, 계약을 바꾸면 다시 생성한다.
모든 픽스처의 source는 "scripted"다. 사람·봇 데이터로 쓰지 않는다.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from contracts.datasets import (
    IntentExample, LabelSource, Scenario, ScenarioCheck, Split, SplitKey, TrainingRow,
)
from engine.domain import serde
from engine.domain.content import (
    Assignment, Character, ContentBundle, GrudgeClue, PlaceDef, Secret, Temperament, TemplateLine,
)
from engine.domain.events import DomainEvent, EventEnvelope, EventType, topic_of
from engine.domain.model import (
    CONTRACT_VERSION, PLAYER_ID, PRESETS, Claim, ClaimType, Command, CommandKind, ConfessionRecord,
    DataSource, DisappearanceRecord, DivinationPayload, DivinationResult, EvidenceGroup, EvidenceRef,
    EvidenceType, GameResult, EndReason, Faction, InterrogationKind, LockRecord, NightPlan, Phase,
    Place, PlayerState, Polarity, PublicRecord, ReactionIntent, Role, Slot, Speech, VoteRecord,
)
from engine.ports import PlayerView, RenderRequest

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "fixtures"
T0 = "2026-11-05T00:00:00Z"
CONTENT_VERSION = "content-min-0.1"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serde.to_jsonable(obj), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(serde.to_jsonable(i), ensure_ascii=False) + "\n" for i in items),
                    encoding="utf-8")


# ======================================================================== 콘텐츠
def routine(evening: dict, midnight: dict) -> dict:
    return {Slot.EVENING: evening, Slot.MIDNIGHT: midnight}


EV = EvidenceGroup
TEMPERAMENTS = [
    Temperament("scholar", "신중형", {EV.CONTRADICTION: 1.3, EV.DIVINATION: 0.3, EV.STATEMENT: 1.2}, 0.3, 0.8, 0.3, 0.3),
    Temperament("keen", "눈치형", {EV.WITNESS: 1.4, EV.SOCIAL: 0.7}, 0.4, 0.4, 0.4, 0.5),
    Temperament("hothead", "다혈질", {EV.SOCIAL: 1.3, EV.TELL: 1.3, EV.CONTRADICTION: 0.7}, 0.8, 0.3, 0.8, 0.4),
    Temperament("timid", "겁쟁이", {EV.DIVINATION: 1.5, EV.CONTRADICTION: 0.6}, 0.2, 0.5, 0.7, 0.8),
]
H, T, W, S, G = Place.HOME, Place.TAVERN, Place.WELL, Place.SHRINE, Place.VILLAGE_GATE
CHARACTERS = [
    Character("hunjang", "훈장", ["훈장", "훈장님", "훈장 어른", "김 훈장"], "scholar",
              routine({T: 0.4, H: 0.6}, {H: 0.9, S: 0.1}), "점잖은 하오체, 문자를 섞음"),
    Character("jumo", "주모", ["주모", "주막댁", "주모 아지매"], "keen",
              routine({T: 0.9, W: 0.1}, {T: 0.5, H: 0.3, S: 0.2}), "시원시원한 반말 섞인 하게체"),
    Character("blacksmith", "대장장이", ["대장장이", "대장간 최씨", "최씨"], "hothead",
              routine({T: 0.5, H: 0.5}, {H: 0.8, W: 0.2}), "거친 말투, 짧은 문장"),
    Character("meosum", "머슴", ["머슴", "돌쇠", "머슴 돌쇠"], "timid",
              routine({T: 0.3, W: 0.4, H: 0.3}, {H: 0.9, W: 0.1}), "더듬는 말투, 존댓말"),
    Character("yakcho", "약초꾼", ["약초꾼", "약초꾼 영감", "박 영감"], "keen",
              routine({G: 0.5, H: 0.5}, {H: 0.7, T: 0.3}), "느릿한 노인 말투"),
    Character("gwabu", "과부", ["과부", "윗마을 과부", "이씨 부인"], "scholar",
              routine({W: 0.5, H: 0.5}, {H: 0.7, W: 0.3}), "조용하고 단정한 말투"),
    # 확장 인원 프리셋(7~11인) 검증용 추가 주민. 콘텐츠는 E가 실제 인물로 교체한다.
    Character("ferryman", "뱃사공", ["뱃사공", "사공"], "hothead", routine({W: 0.5, H: 0.5}, {H: 1.0}), "투박한 사투리"),
    Character("gisaeng", "퇴기", ["퇴기", "월향"], "keen", routine({T: 0.7, H: 0.3}, {T: 0.4, H: 0.6}), "능청스러운 말투"),
    Character("hunter", "포수", ["포수", "사냥꾼"], "hothead", routine({G: 0.7, H: 0.3}, {G: 0.3, H: 0.7}), "말수 적음"),
    Character("monk", "탁발승", ["탁발승", "스님"], "scholar", routine({S: 0.7, H: 0.3}, {S: 0.6, H: 0.4}), "합장하며 느린 말투"),
    Character("boy", "아이", ["아이", "막동이"], "timid", routine({W: 0.6, H: 0.4}, {H: 1.0}), "어린아이 말투"),
]
SECRETS = [
    Secret("gambling", "몰래 노름", ["yakcho", "blacksmith", "ferryman", "meosum"], Slot.MIDNIGHT, T, H,
           "…주막 뒷방에서 투전판에 끼었소. 식구들한테만은 말하지 마시오.", 60.0, [], "clue_debt"),
    Secret("tryst", "몰래 만남", ["jumo", "gwabu", "gisaeng"], Slot.MIDNIGHT, S, W,
           "서낭당 뒤에서… 사람을 만났소. 누군지는 묻지 마시오.", 70.0, [], None),
    Secret("grain", "곡식 빼돌리기", ["meosum", "boy", "ferryman"], Slot.EVENING, G, T,
           "빚을 갚으려고 곳간 곡식을 어귀로 옮겼습니다요…", 50.0, [], "clue_famine"),
    Secret("taboo_herb", "금기 약초", ["yakcho", "hunter", "monk"], Slot.EVENING, G, S,
           "산신께 금한 골짜기에서 약초를 캤소.", 55.0, [], "clue_valley"),
    Secret("letter", "숨긴 편지", ["hunjang", "monk"], Slot.EVENING, W, H,
           "우물가에서 관아로 보내는 편지를 몰래 전했소.", 65.0, ["tryst"], None),
    Secret("drinking", "금주 어김", ["hunter", "blacksmith", "hunjang"], Slot.EVENING, T, H,
           "제사 전날인데 주막에서 술을 마셨네.", 40.0, [], "clue_tiger"),
]
CLUES = [
    GrudgeClue("clue_debt", "창귀는 생전에 노름빚 때문에 밤길을 나섰다.", "secret:gambling 고백", "death"),
    GrudgeClue("clue_famine", "흉년에 곳간을 지키던 머슴이 산에서 사라졌다.", "secret:grain 고백", "identity"),
    GrudgeClue("clue_valley", "금한 골짜기에 이름 없는 무덤이 있다.", "secret:taboo_herb 고백", "identity"),
    GrudgeClue("clue_tiger", "제삿날 밤 호랑이 울음을 들은 이가 있다.", "secret:drinking 고백", "target"),
]
K = CommandKind
R = ReactionIntent
TEMPLATES = [
    TemplateLine("t_suspect", K.SUSPECT, None, None, ["{target}이(가) 수상하오.", "{target}, 자네 말이 영 미덥지 않네."]),
    TemplateLine("t_defend", K.DEFEND, None, None, ["{target}은(는) 그럴 사람이 아니오."]),
    TemplateLine("t_contra", K.POINT_CONTRADICTION, None, None, ["두 사람 말이 서로 맞지 않소."]),
    TemplateLine("t_explain", K.EXPLAIN, None, None, ["나는 귀신과 상관없소."]),
    TemplateLine("t_silence", K.SILENCE, None, None, ["……"]),
    TemplateLine("t_reveal", K.REVEAL_DIVINATION, None, None, ["간밤 {target}의 점괘는 {result}였소."]),
    TemplateLine("t_whereabouts", K.EXPLAIN, None, None, ["{night}일째 밤 {slot}엔 {place}에 있었소."]),
    TemplateLine("t_ans_explain", K.INTERROGATE, None, R.EXPLAIN, ["{fact}"]),
    TemplateLine("t_ans_evade", K.INTERROGATE, None, R.EVADE, ["{fact} …더 묻지 마시오."]),
    TemplateLine("t_ans_counter", K.INTERROGATE, "hothead", R.COUNTER, ["{fact} 그러는 무당 양반은 어디 있었소?"]),
    TemplateLine("t_ans_confess", K.INTERROGATE, None, R.CONFESS, ["{fact}"]),
]
BUNDLE = ContentBundle(
    CONTENT_VERSION,
    [PlaceDef(Place.WELL, "우물가", True), PlaceDef(Place.SHRINE, "서낭당", True),
     PlaceDef(Place.TAVERN, "주막", True), PlaceDef(Place.VILLAGE_GATE, "마을 어귀", True),
     PlaceDef(Place.HOME, "집", False)],
    TEMPERAMENTS, CHARACTERS, SECRETS, CLUES, TEMPLATES,
)

# ======================================================================== 기본 6인 판 배정
BASE6_RESIDENTS = ["hunjang", "jumo", "blacksmith", "meosum", "yakcho", "gwabu"]
BASE6_ROLES = {n: Role.VILLAGER for n in BASE6_RESIDENTS} | {"blacksmith": Role.POSSESSED}
BASE6_SECRETS = {"yakcho": "gambling", "jumo": "tryst"}
BASE6_N0 = {
    "hunjang": {Slot.EVENING: T, Slot.MIDNIGHT: H},
    "jumo": {Slot.EVENING: T, Slot.MIDNIGHT: S},
    "blacksmith": {Slot.EVENING: H, Slot.MIDNIGHT: W},
    "meosum": {Slot.EVENING: T, Slot.MIDNIGHT: H},
    "yakcho": {Slot.EVENING: H, Slot.MIDNIGHT: T},
    "gwabu": {Slot.EVENING: H, Slot.MIDNIGHT: W},
}
BASE6_ASSIGN = Assignment("g_base6_001", "base6", BASE6_RESIDENTS, "gwabu", BASE6_ROLES, BASE6_SECRETS,
                          BASE6_N0, NightPlan("blacksmith", "gwabu"))

DUO9_RESIDENTS = ["hunjang", "jumo", "blacksmith", "meosum", "yakcho", "gwabu", "ferryman", "gisaeng", "hunter"]
DUO9_ROLES = {n: Role.VILLAGER for n in DUO9_RESIDENTS} | {"blacksmith": Role.POSSESSED, "gisaeng": Role.POSSESSED}
DUO9_N0 = {n: {Slot.EVENING: H, Slot.MIDNIGHT: H} for n in DUO9_RESIDENTS}
DUO9_N0.update({
    "blacksmith": {Slot.EVENING: H, Slot.MIDNIGHT: W},      # 실행자: 피해자 위치
    "gwabu": {Slot.EVENING: H, Slot.MIDNIGHT: W},           # 0일째 피해자
    "gisaeng": {Slot.EVENING: T, Slot.MIDNIGHT: T},         # 비실행 공범: 주막에서 목격됨
    "jumo": {Slot.EVENING: T, Slot.MIDNIGHT: S},
    "yakcho": {Slot.EVENING: H, Slot.MIDNIGHT: T},
    "meosum": {Slot.EVENING: G, Slot.MIDNIGHT: H},
})
DUO9_ASSIGN = Assignment("g_duo9_001", "duo9", DUO9_RESIDENTS, "gwabu", DUO9_ROLES,
                         {"yakcho": "gambling", "jumo": "tryst", "meosum": "grain"}, DUO9_N0,
                         NightPlan("blacksmith", "gwabu", {"blacksmith": "tavern", "gisaeng": "tavern"}))


def conflict_cases() -> list[dict]:
    cases = []

    def case(cid, preset, mutate, codes):
        a = copy.deepcopy(BASE6_ASSIGN if preset == "base6" else DUO9_ASSIGN)
        mutate(a)
        cases.append({"case_id": cid, "preset_id": preset, "assignment": serde.to_jsonable(a),
                      "expect_codes": codes})

    case("secret_on_possessed", "base6", lambda a: a.secrets.update({"blacksmith": "gambling"}) or a.secrets.pop("yakcho"),
         ["secret_on_non_villager", "secret_whereabouts_mismatch"])
    case("secret_not_eligible", "base6", lambda a: a.secrets.update({"jumo": "grain"}),
         ["secret_not_eligible", "secret_whereabouts_mismatch"])
    case("executor_not_at_victim", "base6", lambda a: a.night0_whereabouts["blacksmith"].update({Slot.MIDNIGHT: H}),
         ["executor_not_at_victim"])
    case("victim_is_possessed", "base6", lambda a: setattr(a, "night0_victim", "blacksmith"),
         ["night0_victim", "plan_victim_mismatch"])
    case("secret_whereabouts_mismatch", "base6", lambda a: a.night0_whereabouts["yakcho"].update({Slot.MIDNIGHT: H}),
         ["secret_whereabouts_mismatch"])
    case("possessed_count", "duo9", lambda a: a.roles.update({"gisaeng": Role.VILLAGER}),
         ["possessed_count", "alibi_by_non_possessed"])
    case("witness_at_crime", "base6", lambda a: a.night0_whereabouts["meosum"].update({Slot.MIDNIGHT: W}),
         ["witness_at_crime"])
    return cases


# ======================================================================== 녹화 판(기본 6인)
NAMES = {c.npc_id: c.name_ko for c in CHARACTERS if c.npc_id in BASE6_RESIDENTS}


def claim(cid, speaker, ctype, night, slot=None, place=None, pol=Polarity.AFFIRM, seen=None, subject=None,
          day=1, div=None, corrects=None):
    return Claim(cid, speaker, ctype, subject or speaker, night, slot, place, pol, seen or [], corrects, div, day)


def recorded_game() -> dict:
    pub = PublicRecord()
    player = PlayerState()
    active = [PLAYER_ID, "hunjang", "jumo", "blacksmith", "meosum", "yakcho"]
    locked: list[str] = []
    missing = ["gwabu"]
    pub.disappearances.append(DisappearanceRecord(0, "gwabu"))
    steps: list[dict] = []
    ver = 0
    day = 1

    def view(phase, legal, awaiting=PLAYER_ID, result=None, runoff=None, jobs=None):
        return PlayerView("g_base6_001", ver, day, phase, list(active), list(locked), list(missing),
                          copy.deepcopy(pub), copy.deepcopy(player), legal, awaiting, runoff or [], result,
                          dict(NAMES), jobs or [])

    def step(req_cmd, v, events):
        steps.append({
            "request": None if req_cmd is None else serde.to_jsonable(
                {"command_id": f"c{ver:03d}", "expected_state_version": ver - 1, "command": req_cmd}),
            "view": serde.to_jsonable(v),
            "events": [serde.to_jsonable(e) for e in events],
        })

    talk = [K.SUSPECT, K.DEFEND, K.POINT_CONTRADICTION, K.EXPLAIN, K.SILENCE, K.REVEAL_DIVINATION]
    # 0: 1일째 아침 진술
    pub.claims += [
        claim("cl01", "hunjang", ClaimType.WHEREABOUTS, 0, Slot.EVENING, T),
        claim("cl02", "hunjang", ClaimType.WHEREABOUTS, 0, Slot.MIDNIGHT, H),
        claim("cl03", "jumo", ClaimType.WHEREABOUTS, 0, Slot.MIDNIGHT, W),          # 거짓(비밀: 실제 서낭당)
        claim("cl04", "blacksmith", ClaimType.WHEREABOUTS, 0, Slot.MIDNIGHT, H),    # 거짓(실행자: 실제 우물가)
        claim("cl05", "meosum", ClaimType.SIGHTING, 0, Slot.EVENING, T, seen=["hunjang", "jumo"]),
        claim("cl06", "yakcho", ClaimType.WHEREABOUTS, 0, Slot.MIDNIGHT, H),        # 거짓(비밀: 실제 주막)
    ]
    step(None, view(Phase.MORNING, [K.ADVANCE]),
         [DomainEvent(EventType.GAME_STARTED, 1, Phase.MORNING, {"preset_id": "base6"}),
          DomainEvent(EventType.MORNING_ANNOUNCED, 1, Phase.MORNING, {"missing": ["gwabu"], "claims": [f"cl0{i}" for i in range(1, 7)]})])
    # 1: 진행 → 1라운드(훈장이 약초꾼 의심)
    ver += 1
    pub.speeches.append(Speech("sp01", 1, Phase.ROUND1, "hunjang", K.SUSPECT, target="yakcho",
                               line_text="약초꾼이 수상하오."))
    step(Command(K.ADVANCE, PLAYER_ID), view(Phase.ROUND1, talk),
         [DomainEvent(EventType.SPEECH_MADE, 1, Phase.ROUND1, {"speech_id": "sp01"})])
    # 2: 플레이어 의심(주모, 근거 cl03) → 나머지 NPC 발언 → 추궁 단계
    ver += 1
    pub.speeches += [
        Speech("sp02", 1, Phase.ROUND1, PLAYER_ID, K.SUSPECT, target="jumo",
               evidence=[EvidenceRef(EvidenceType.CLAIM, "cl03")], line_text="주모의 한밤 행적이 미덥지 않소."),
        Speech("sp03", 1, Phase.ROUND1, "jumo", K.EXPLAIN, line_text="나는 귀신과 상관없소."),
        Speech("sp04", 1, Phase.ROUND1, "blacksmith", K.SUSPECT, target="yakcho", line_text="약초꾼이 수상하오."),
    ]
    step(Command(K.SUSPECT, PLAYER_ID, target="jumo", evidence=[EvidenceRef(EvidenceType.CLAIM, "cl03")]),
         view(Phase.INTERROGATION, [K.INTERROGATE, K.ADVANCE]),
         [DomainEvent(EventType.SPEECH_MADE, 1, Phase.ROUND1, {"speech_id": s}) for s in ("sp02", "sp03", "sp04")])
    # 3: 캐묻기(주모, 행적) → 회피 답변 + LLM 작업
    ver += 1
    player.interrogations_left = 1
    pub.speeches.append(Speech("sp05", 1, Phase.INTERROGATION, "jumo", K.INTERROGATE, addressee=PLAYER_ID,
                               reaction=R.EVADE, fact_sentence="그날 한밤엔 우물가에 있었소.",
                               line_text="그날 한밤엔 우물가에 있었소. …더 묻지 마시오.", render_job_id="job_001"))
    step(Command(K.INTERROGATE, PLAYER_ID, addressee="jumo", interrogation_kind=InterrogationKind.WHEREABOUTS,
                 night=0, slot=Slot.MIDNIGHT),
         view(Phase.INTERROGATION, [K.INTERROGATE, K.ADVANCE], jobs=["job_001"]),
         [DomainEvent(EventType.INTERROGATION_ANSWERED, 1, Phase.INTERROGATION,
                      {"speech_id": "sp05", "reaction": "evade", "render_job_id": "job_001"})])
    # 4: 진행 → 2라운드
    ver += 1
    pub.speeches.append(Speech("sp06", 1, Phase.ROUND2, "meosum", K.SUSPECT, target="yakcho",
                               line_text="야, 약초꾼 영감님이… 수상합니다요."))
    step(Command(K.ADVANCE, PLAYER_ID), view(Phase.ROUND2, talk),
         [DomainEvent(EventType.SPEECH_MADE, 1, Phase.ROUND2, {"speech_id": "sp06"})])
    # 5: 침묵 → 투표 단계
    ver += 1
    pub.speeches.append(Speech("sp07", 1, Phase.ROUND2, PLAYER_ID, K.SILENCE, line_text="……"))
    step(Command(K.SILENCE, PLAYER_ID), view(Phase.VOTE, [K.VOTE]),
         [DomainEvent(EventType.SPEECH_MADE, 1, Phase.ROUND2, {"speech_id": "sp07"})])
    # 6: 투표(약초꾼) → 약초꾼 감금(씐 자 아님) → 밤
    ver += 1
    votes = {PLAYER_ID: "yakcho", "hunjang": "yakcho", "jumo": "yakcho", "blacksmith": "yakcho", "meosum": "yakcho",
             "yakcho": "jumo"}
    pub.votes += [VoteRecord(f"v1_{i}", 1, "main", a, b) for i, (a, b) in enumerate(votes.items())]
    pub.locks.append(LockRecord(1, "yakcho", revealed_possessed=False))
    active.remove("yakcho"); locked.append("yakcho")
    step(Command(K.VOTE, PLAYER_ID, target="yakcho"), view(Phase.NIGHT, [K.DIVINE]),
         [DomainEvent(EventType.VOTES_REVEALED, 1, Phase.VOTE, {"round": "main"}),
          DomainEvent(EventType.LOCK_RESOLVED, 1, Phase.VOTE, {"locked": "yakcho", "revealed_possessed": False, "end": None})])
    # 7: 밤 점(대장장이) → 2일째 아침: 훈장 실종, 점괘 탁함
    ver += 1
    day = 2
    player.divinations[1] = {"blacksmith": DivinationResult.MURKY}
    player.interrogations_left = 2
    pub.disappearances.append(DisappearanceRecord(1, "hunjang"))
    active.remove("hunjang"); missing.append("hunjang")
    pub.claims += [
        claim("cl07", "jumo", ClaimType.WHEREABOUTS, 1, Slot.MIDNIGHT, T, day=2),
        claim("cl08", "blacksmith", ClaimType.WHEREABOUTS, 1, Slot.MIDNIGHT, H, day=2),   # 거짓
        claim("cl09", "meosum", ClaimType.NON_SIGHTING, 1, Slot.MIDNIGHT, W, pol=Polarity.DENY, day=2),
    ]
    step(Command(K.DIVINE, PLAYER_ID, target="blacksmith"), view(Phase.MORNING, [K.ADVANCE]),
         [DomainEvent(EventType.DIVINATION_DONE, 1, Phase.NIGHT, {"target": "blacksmith", "result": "murky"}),
          DomainEvent(EventType.NIGHT_RESOLVED, 1, Phase.NIGHT, {"victim": "hunjang", "end": None}),
          DomainEvent(EventType.MORNING_ANNOUNCED, 2, Phase.MORNING, {"missing": ["hunjang"], "claims": ["cl07", "cl08", "cl09"]})])
    # 8: 진행 → 1라운드
    ver += 1
    step(Command(K.ADVANCE, PLAYER_ID), view(Phase.ROUND1, talk), [])
    # 9: 점괘 공개(대장장이 탁함, 사실대로) → 추궁 단계
    ver += 1
    player.reveals_left_today = 0
    pub.claims.append(claim("cl10", PLAYER_ID, ClaimType.DIVINATION_REVEAL, 1, subject="blacksmith", day=2,
                            div=DivinationResult.MURKY))
    pub.speeches += [
        Speech("sp08", 2, Phase.ROUND1, PLAYER_ID, K.REVEAL_DIVINATION, target="blacksmith", claim_ids=["cl10"],
               line_text="간밤 대장장이의 점괘는 탁함이었소."),
        Speech("sp09", 2, Phase.ROUND1, "blacksmith", K.SUSPECT, target=PLAYER_ID,
               line_text="저 무당이 오고 나서부터 사람이 사라지오!"),
    ]
    step(Command(K.REVEAL_DIVINATION, PLAYER_ID,
                 divination=DivinationPayload("blacksmith", 1, DivinationResult.MURKY)),
         view(Phase.INTERROGATION, [K.INTERROGATE, K.ADVANCE]),
         [DomainEvent(EventType.CLAIM_MADE, 2, Phase.ROUND1, {"claim_id": "cl10"}),
          DomainEvent(EventType.SPEECH_MADE, 2, Phase.ROUND1, {"speech_id": "sp08"}),
          DomainEvent(EventType.SPEECH_MADE, 2, Phase.ROUND1, {"speech_id": "sp09"})])
    # 10: 진행 → 2라운드
    ver += 1
    player.talk_used_this_round = False
    step(Command(K.ADVANCE, PLAYER_ID), view(Phase.ROUND2, talk[:-1]), [])
    # 11: 대장장이 의심 → 투표
    ver += 1
    pub.speeches.append(Speech("sp10", 2, Phase.ROUND2, PLAYER_ID, K.SUSPECT, target="blacksmith",
                               evidence=[EvidenceRef(EvidenceType.DIVINATION, "cl10")],
                               line_text="대장장이, 자네 점괘가 탁하네."))
    step(Command(K.SUSPECT, PLAYER_ID, target="blacksmith", evidence=[EvidenceRef(EvidenceType.DIVINATION, "cl10")]),
         view(Phase.VOTE, [K.VOTE]),
         [DomainEvent(EventType.SPEECH_MADE, 2, Phase.ROUND2, {"speech_id": "sp10"})])
    # 12: 투표(대장장이) → 감금(씐 자) → 종료(마을 승리)
    ver += 1
    votes2 = {PLAYER_ID: "blacksmith", "jumo": "blacksmith", "meosum": "blacksmith", "blacksmith": PLAYER_ID}
    pub.votes += [VoteRecord(f"v2_{i}", 2, "main", a, b) for i, (a, b) in enumerate(votes2.items())]
    pub.locks.append(LockRecord(2, "blacksmith", revealed_possessed=True))
    active.remove("blacksmith"); locked.append("blacksmith")
    res = GameResult(Faction.VILLAGE, EndReason.ALL_POSSESSED_LOCKED, 2)
    step(Command(K.VOTE, PLAYER_ID, target="blacksmith"), view(Phase.ENDED, [], awaiting=None, result=res),
         [DomainEvent(EventType.VOTES_REVEALED, 2, Phase.VOTE, {"round": "main"}),
          DomainEvent(EventType.LOCK_RESOLVED, 2, Phase.VOTE, {"locked": "blacksmith", "revealed_possessed": True,
                                                               "end": "all_possessed_locked"}),
          DomainEvent(EventType.GAME_ENDED, 2, Phase.ENDED, {"winner": "village", "reason": "all_possessed_locked"})])
    return {"game_id": "g_base6_001", "preset_id": "base6", "source": "scripted",
            "contract_version": CONTRACT_VERSION, "steps": steps}


def envelopes_from(rec: dict) -> list[EventEnvelope]:
    out = []
    seq = 0
    for i, s in enumerate(rec["steps"]):
        cmd_id = s["request"]["command_id"] if s["request"] else None
        for e in s["events"]:
            out.append(_env(rec["game_id"], seq, i, e["event_type"], e["day"], e["phase"], e["payload"], cmd_id))
            seq += 1
    # 행동 결정과 관측 스냅샷 예시, 판 종료 후 진실
    out.append(_env(rec["game_id"], seq, 6, "observation", 1, "vote",
                    {"snapshot_id": "snap_v1_meosum", "observer": "meosum", "ref": "fixtures/ml/observations_sample.jsonl"}, None)); seq += 1
    out.append(_env(rec["game_id"], seq, 6, "decision", 1, "vote",
                    {"actor": "meosum", "snapshot_id": "snap_v1_meosum", "kind": "vote", "target": "yakcho",
                     "reason_codes": ["contradiction_majority"], "evidence": [], "policy_version": "rule-v0"}, None)); seq += 1
    out.append(_env(rec["game_id"], seq, 12, "truth_revealed", 2, "ended",
                    {"roles": {n: r.value for n, r in BASE6_ROLES.items()}, "secrets": BASE6_SECRETS,
                     "lies": ["cl03", "cl04", "cl06", "cl08"]}, None))
    return out


def _env(game_id, seq, ver, etype, day, phase, payload, cmd_id) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"{game_id}:{seq:05d}", game_id=game_id, seq=seq, state_version=ver, event_type=etype,
        topic=topic_of(EventType(etype)), occurred_at=T0, day=day, phase=phase, source=DataSource.SCRIPTED,
        contract_version=CONTRACT_VERSION, preset_id="base6", seed=42, rule_version="rule-0.1",
        engine_version="scripted", content_version=CONTENT_VERSION,
        npc_policy_versions={n: "rule-v0" for n in BASE6_RESIDENTS}, model_versions={"suspicion": "fake-suspicion-1"},
        payload=payload, command_id=cmd_id)


# ======================================================================== ML 학습 행 예시
def training_rows() -> list[TrainingRow]:
    rows = []
    cands_by_obs = {"jumo": ["blacksmith", "hunjang", "meosum", "yakcho"],
                    "meosum": ["blacksmith", "hunjang", "jumo", "yakcho"],
                    "hunjang": ["blacksmith", "jumo", "meosum", "yakcho"]}
    feats = {
        "blacksmith": {"contradiction_count": 0, "tell_count": 0, "unverifiable_alibi": 1, "suspected_by_victim": 0},
        "hunjang": {"contradiction_count": 0, "tell_count": 0, "unverifiable_alibi": 1, "suspected_by_victim": 0},
        "jumo": {"contradiction_count": 1, "tell_count": 1, "unverifiable_alibi": 0, "suspected_by_victim": 0},
        "meosum": {"contradiction_count": 0, "tell_count": 0, "unverifiable_alibi": 1, "suspected_by_victim": 0},
        "yakcho": {"contradiction_count": 0, "tell_count": 1, "unverifiable_alibi": 1, "suspected_by_victim": 0},
    }
    for obs, cands in cands_by_obs.items():
        for c in cands:
            f = {k: float(v) for k, v in feats[c].items()} | {"day": 1.0, "n_candidates": float(len(cands))}
            rows.append(TrainingRow(
                f"row_{obs}_{c}", "g_base6_001", f"snap_v1_{obs}", obs, c, 1, "vote", "base6", 6, 1, 1, len(cands),
                "feat-0.1", f, "scripted", SplitKey(None, "g_base6_001", "2026-11-05"),
                label=1 if c == "blacksmith" else 0, label_joined_at=T0))
    return rows


# ======================================================================== LLM 요청 예시
def llm_requests() -> list[RenderRequest]:
    persona = {c.npc_id: f"{c.name_ko}. 말투: {c.speech_style}" for c in CHARACTERS}
    return [
        RenderRequest("job_001", "g_base6_001", 3, "sp05", "jumo", persona["jumo"], R.EVADE,
                      "그날 한밤엔 우물가에 있었소.", ["주모", "무당"], "그날 한밤엔 우물가에 있었소. …더 묻지 마시오."),
        RenderRequest("job_002", "g_base6_001", 3, "sp_x1", "yakcho", persona["yakcho"], R.CONFESS,
                      "그날 한밤엔 주막 뒷방에 있었소. 투전판에 끼었소.", ["약초꾼", "무당"],
                      "그날 한밤엔 주막 뒷방에 있었소. 투전판에 끼었소."),
        RenderRequest("job_003", "g_base6_001", 9, "sp_x2", "blacksmith", persona["blacksmith"], R.COUNTER,
                      "그날 한밤엔 집에 있었소.", ["대장장이", "무당"],
                      "그날 한밤엔 집에 있었소. 그러는 무당 양반은 어디 있었소?"),
    ]


# ======================================================================== 의도 예시(intent.md와 같은 내용)
INTENT_SEED = [
    # (label, form, text, slots, contrast_group)
    ("suspect", "assertion", "<P1>이 수상하다", {"target": "<P1>"}, "cg_suspect_1"),
    ("suspect", "assertion", "<P1> 말은 못 믿겠소", {"target": "<P1>"}, None),
    ("suspect", "assertion", "아무래도 <P1>이 씐 것 같소", {"target": "<P1>"}, None),
    ("suspect", "assertion", "<P1>, 자네 행적이 영 이상하네", {"target": "<P1>"}, None),
    ("suspect", "assertion", "나는 <P1>을 의심하오", {"target": "<P1>"}, None),
    ("defend", "assertion", "<P1>이 수상하지 않다", {"target": "<P1>"}, "cg_suspect_1"),
    ("defend", "assertion", "<P1>은 그럴 사람이 아니오", {"target": "<P1>"}, None),
    ("defend", "assertion", "<P1>은 결백하오", {"target": "<P1>"}, None),
    ("defend", "assertion", "<P1>은 아니야", {"target": "<P1>"}, None),
    ("defend", "assertion", "<P1>을 몰아세우지 마시오", {"target": "<P1>"}, None),
    ("point_contradiction", "assertion", "<P1>과 <P2> 말이 서로 안 맞는다", {"claims": 2}, "cg_contra_1"),
    ("point_contradiction", "assertion", "둘 중 하나는 거짓말이오", {"claims": 2}, None),
    ("point_contradiction", "assertion", "<P1>은 서낭당에 있었다 하고 <P2>는 아무도 없었다 하오", {"claims": 2}, None),
    ("point_contradiction", "assertion", "그 진술들은 앞뒤가 맞지 않소", {"claims": 2}, None),
    ("point_contradiction", "assertion", "<P1>의 말이 <P2>의 목격과 어긋나오", {"claims": 2}, None),
    ("explain", "assertion", "나는 귀신과 상관없소", {}, None),
    ("explain", "assertion", "나를 의심하지 마시오, 나는 무당이오", {}, None),
    ("explain", "assertion", "나는 마을을 도우러 왔소", {}, None),
    ("explain", "assertion", "내가 왜 귀신을 데려오겠소", {}, None),
    ("explain", "assertion", "억울하오, 나는 아무 짓도 안 했소", {}, None),
    ("silence", "assertion", "이번엔 지켜보겠다", {}, None),
    ("silence", "assertion", "할 말 없소", {}, None),
    ("silence", "assertion", "잠자코 듣겠소", {}, None),
    ("silence", "assertion", "패스", {}, None),
    ("silence", "assertion", "이번 차례는 넘기겠소", {}, None),
    ("reveal_divination", "assertion", "<P1> 점괘는 맑았다", {"target": "<P1>", "result": "clear"}, "cg_reveal_1"),
    ("reveal_divination", "assertion", "간밤 <P1>을 점쳤더니 탁하게 나왔소", {"target": "<P1>", "result": "murky"}, None),
    ("reveal_divination", "assertion", "<P1>의 기운이 탁하오", {"target": "<P1>", "result": "murky"}, None),
    ("reveal_divination", "assertion", "점을 쳐 보니 <P1>은 맑소", {"target": "<P1>", "result": "clear"}, None),
    ("reveal_divination", "assertion", "어젯밤 점괘를 말하겠소, <P1>은 탁했소", {"target": "<P1>", "result": "murky"}, None),
    ("interrogate_whereabouts", "question", "어젯밤 한밤에 어디 있었소?", {"addressee": "<P1>", "night": -1, "slot": "midnight"}, None),
    ("interrogate_whereabouts", "question", "<P1>, 초저녁엔 뭐 했소?", {"addressee": "<P1>", "slot": "evening"}, None),
    ("interrogate_whereabouts", "question", "그날 밤 집에 있었다는 게 사실이오?", {"addressee": "<P1>"}, None),
    ("interrogate_whereabouts", "question", "<P1>, 한밤에 누구와 있었소?", {"addressee": "<P1>", "slot": "midnight"}, None),
    ("interrogate_whereabouts", "request", "<P1>은 어젯밤 행적을 다시 말해 보시오", {"addressee": "<P1>"}, None),
    ("interrogate_contradiction", "question", "<P1>님, <P2> 말과 왜 다르오?", {"addressee": "<P1>", "claims": 2}, "cg_contra_1"),
    ("interrogate_contradiction", "question", "서낭당에 있었다며? <P2>는 아무도 없었다던데?", {"addressee": "<P1>", "claims": 2}, None),
    ("interrogate_contradiction", "question", "<P1>, 자네 말이 <P2> 목격과 어긋나는 걸 어찌 설명하겠소?", {"addressee": "<P1>", "claims": 2}, None),
    ("interrogate_contradiction", "request", "<P1>은 <P2>의 말과 다른 이유를 대 보시오", {"addressee": "<P1>", "claims": 2}, None),
    ("interrogate_contradiction", "question", "<P1>, 아까 한 말과 지금 말이 왜 다르오?", {"addressee": "<P1>", "claims": 2}, None),
    ("interrogate_suspicion_reason", "question", "왜 <P2>를 의심하오?", {"addressee": "<P1>", "mentioned": "<P2>"}, None),
    ("interrogate_suspicion_reason", "question", "<P1>님, <P2>를 왜 의심하세요?", {"addressee": "<P1>", "mentioned": "<P2>"}, None),
    ("interrogate_suspicion_reason", "question", "<P1>, 어째서 <P2>에게 표를 던졌소?", {"addressee": "<P1>", "mentioned": "<P2>"}, None),
    ("interrogate_suspicion_reason", "quote", "<P1>이 수상하다고?", {"addressee": "<P2>", "mentioned": "<P1>"}, "cg_suspect_1"),
    ("interrogate_suspicion_reason", "request", "<P1>은 <P2>를 의심하는 까닭을 말해 보시오", {"addressee": "<P1>", "mentioned": "<P2>"}, None),
    ("unsupported", "question", "오늘 날씨가 어떻소?", {}, None),
    ("unsupported", "question", "호랑이는 어디 사오?", {}, None),
    ("unsupported", "question", "<P1>의 점괘는 어땠소?", {}, "cg_reveal_1"),
    ("unsupported", "assertion", "ㅋㅋㅋ", {}, None),
    ("unsupported", "question", "<P1>, 자네 비밀이 뭐요?", {}, None),
]


def intent_examples() -> list[IntentExample]:
    out = []
    for i, (label, form, text, slots, cg) in enumerate(INTENT_SEED):
        eid = f"seed_{i:03d}"
        out.append(IntentExample(eid, text, label, form, slots, LabelSource.AUTHORED, Split.TRAIN,
                                 SplitKey(None, None, "2026-11-04", lineage_root=eid), cg))
    return out


# ======================================================================== 계약 시나리오
def scenarios() -> list[Scenario]:
    C = ScenarioCheck
    sc = []
    sc.append(Scenario(
        "normal_game", "기본 6인 판 정상 진행(녹화 판과 같은 판). 1일째 오답 감금, 2일째 씐 자 감금으로 마을 승리.",
        "base6", "scripted", {"assignment": "fixtures/assignment/valid_base6.json"},
        [{"recording": "fixtures/api/recorded_game_base6.json"}],
        [C("vote", {"main": {"mudang": "yakcho", "hunjang": "yakcho", "jumo": "yakcho", "blacksmith": "yakcho",
                             "meosum": "yakcho", "yakcho": "jumo"}}, {"locked": "yakcho", "runoff_candidates": []}),
         C("end_check", {"g": 1, "v": 3, "mudang_active": True, "mudang_locked": False}, {"reason": None, "night_runs": True},
           "1일째 감금 후: 씐 자 1, 마을 주민 3(훈장·주모·머슴)"),
         C("end_check", {"g": 0, "v": 2, "mudang_active": True, "mudang_locked": False},
           {"reason": "all_possessed_locked", "winner": "village", "night_runs": False}),
         C("assignment", {"preset_id": "base6", "assignment": "fixtures/assignment/valid_base6.json"}, {"violations": []}),
         C("phase_legal", {"phase": "interrogation", "kind": "suspect", "is_player": True}, {"legal": False}),
         C("phase_legal", {"phase": "night", "kind": "night_plan", "is_player": False}, {"legal": True})]))
    sc.append(Scenario(
        "secret_confession", "비밀 있는 약초꾼이 모순 추궁 끝에 고백. 다음 점부터 맑음, 원한 단서 획득(중복 무시).",
        "base6", "scripted", {"assignment": "fixtures/assignment/valid_base6.json"},
        [{"command": {"kind": "interrogate", "actor": "mudang", "addressee": "yakcho", "interrogation_kind": "contradiction",
                      "evidence": [{"type": "contradiction", "record_id": "cl06|cl05"}]}},
         {"engine_reaction": "confess", "secret_id": "gambling"}],
        [C("divination", {"is_possessed": False, "has_unconfessed_secret": True}, {"result": "murky"}, "고백 전"),
         C("divination", {"is_possessed": False, "has_unconfessed_secret": False}, {"result": "clear"}, "고백 후"),
         C("divination", {"is_possessed": True, "has_unconfessed_secret": False}, {"result": "murky"}),
         C("missing_slots", {"command": {"kind": "interrogate", "actor": "mudang", "addressee": "yakcho",
                                         "interrogation_kind": "contradiction"}}, {"missing": ["evidence"]}),
         C("progress_clue", {"clue_id": "clue_debt", "times": 2}, {"added": [True, False]})]))
    sc.append(Scenario(
        "false_divination_reveal", "무당이 탁함을 맑음으로 거짓 공개. 합법이며, 공개 기록과 실제 결과가 따로 저장된다.",
        "base6", "scripted", {"actual": {"night": 1, "target": "blacksmith", "result": "murky"}},
        [{"command": {"kind": "reveal_divination", "actor": "mudang",
                      "divination": {"target": "blacksmith", "night": 1, "result": "clear"}}}],
        [C("reveal_record", {"actual": "murky", "revealed": "clear"},
           {"legal": True, "public_result": "clear", "private_result": "murky"}),
         C("phase_legal", {"phase": "round1", "kind": "reveal_divination", "is_player": True}, {"legal": True}),
         C("limit", {"player": {"reveals_left_today": 0}, "kind": "reveal_divination"}, {"reject": "limit_exceeded"},
           "하루 1회 초과"),
         C("phase_legal", {"phase": "round1", "kind": "reveal_divination", "is_player": False}, {"legal": False})]))
    sc.append(Scenario(
        "duo_accomplice_alibi", "창귀 2명(9인). 실행자 대장장이, 비실행 공범 퇴기는 주막에서 목격됨 → '실행자 아님'까지만.",
        "duo9", "scripted", {"assignment": "fixtures/assignment/valid_duo9.json"},
        [],
        [C("alibi", {"n_possessed": 2}, {"proposition_kind": "not_executor"}),
         C("alibi", {"n_possessed": 1}, {"proposition_kind": "not_possessed"}),
         C("assignment", {"preset_id": "duo9", "assignment": "fixtures/assignment/valid_duo9.json"}, {"violations": []}),
         C("end_check", {"g": 2, "v": 2, "mudang_active": True, "mudang_locked": False}, {"reason": None, "night_runs": True},
           "창귀2 + 주민2 + 무당: 아직 창귀 승리 아님"),
         C("end_check", {"g": 2, "v": 1, "mudang_active": True, "mudang_locked": False},
           {"reason": "possessed_parity", "winner": "changgwi", "night_runs": False})]))
    sc.append(Scenario(
        "runoff_tie_no_lock", "본투표 동점 → 결선도 동점 → 무감금. 밤은 진행된다.", "base6", "scripted", {}, [],
        [C("vote", {"main": {"mudang": "jumo", "hunjang": "jumo", "jumo": "yakcho", "blacksmith": "yakcho",
                             "meosum": "hunjang", "yakcho": "hunjang"}},
           {"locked": None, "runoff_candidates": ["hunjang", "jumo", "yakcho"]}),
         C("vote", {"main": {"mudang": "jumo", "hunjang": "jumo", "jumo": "yakcho", "blacksmith": "yakcho",
                             "meosum": "hunjang", "yakcho": "hunjang"},
                    "runoff": {"mudang": "jumo", "hunjang": "jumo", "jumo": "yakcho", "blacksmith": "yakcho",
                               "meosum": "hunjang", "yakcho": "hunjang"}},
           {"locked": None, "runoff_candidates": []}),
         C("end_check", {"g": 1, "v": 4, "mudang_active": True, "mudang_locked": False}, {"reason": None, "night_runs": True})]))
    sc.append(Scenario(
        "mudang_locked", "NPC 다수가 무당에게 투표 → 무당 감금 → 인원과 무관하게 창귀 승리.", "base6", "scripted", {}, [],
        [C("vote", {"main": {"hunjang": "mudang", "jumo": "mudang", "blacksmith": "mudang", "meosum": "jumo",
                             "yakcho": "mudang", "mudang": "blacksmith"}}, {"locked": "mudang", "runoff_candidates": []}),
         C("end_check", {"g": 1, "v": 4, "mudang_active": False, "mudang_locked": True},
           {"reason": "mudang_locked", "winner": "changgwi", "night_runs": False})]))
    sc.append(Scenario(
        "last_possessed_locked", "창귀 2명 판에서 남은 마지막 씐 자를 가두면 즉시 마을 승리, 밤 생략.", "duo9", "scripted", {},
        [],
        [C("end_check", {"g": 1, "v": 4, "mudang_active": True, "mudang_locked": False}, {"reason": None, "night_runs": True},
           "첫 씐 자 감금 후(감금 공개로 남은 수 1)"),
         C("end_check", {"g": 0, "v": 3, "mudang_active": True, "mudang_locked": False},
           {"reason": "all_possessed_locked", "winner": "village", "night_runs": False})]))
    sc.append(Scenario(
        "intent_failure_and_version_conflict", "자유 입력 실패 상태와 오래된 상태 버전 명령 거부. 둘 다 행동을 소비하지 않는다.",
        "base6", "scripted", {}, [],
        [C("parse", {"text": "", "phase": "round1"}, {"failure": "empty_input", "kinds": []}),
         C("parse", {"text": "오늘 날씨가 어떻소?", "phase": "round1"}, {"failure": "unsupported_intent", "kinds": []}),
         C("parse", {"text": "수상하다", "phase": "round1"}, {"failure": "missing_target", "kinds": ["suspect"]}),
         C("parse", {"text": "훈장은 수상하지만 머슴은 결백해", "phase": "round1"},
           {"failure": None, "kinds": ["defend", "suspect"]}, "다중 의도: 카드 2장, 플레이어가 하나 선택"),
         C("repo_commit", {"stored_version": 5, "expected_version": 4}, {"error": "state_version_conflict"}),
         C("repo_commit", {"stored_version": 5, "expected_version": 5}, {"error": None}),
         C("idempotent", {"command_id": "c005", "repeat": 2}, {"commits": 1, "same_response": True}),
         C("limit", {"player": {"interrogations_left": 0}, "kind": "interrogate"}, {"reject": "limit_exceeded"})]))
    return sc


def main() -> None:
    write_json(FX / "content_min" / "bundle.json", BUNDLE)
    write_json(FX / "assignment" / "valid_base6.json", BASE6_ASSIGN)
    write_json(FX / "assignment" / "valid_duo9.json", DUO9_ASSIGN)
    (FX / "assignment" / "conflicts.json").write_text(
        json.dumps(conflict_cases(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rec = recorded_game()
    (FX / "api" / "recorded_game_base6.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                                                          encoding="utf-8")
    write_jsonl(FX / "events" / "sample_events.jsonl", envelopes_from(rec))
    write_jsonl(FX / "ml" / "observations_sample.jsonl", training_rows())
    write_json(FX / "llm" / "interrogation_requests.json", llm_requests())
    write_jsonl(FX / "intent" / "seed_examples.jsonl", intent_examples())
    for s in scenarios():
        write_json(FX / "scenarios" / f"{s.scenario_id}.json", s)
    from tools import build_ads_fixtures
    build_ads_fixtures.main(FX)
    print("fixtures written to", FX)


if __name__ == "__main__":
    main()
