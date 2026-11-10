"""대사창에 흘릴 행동 결과·아침 진술 줄을 공개 기록만으로 조립한다(viewmodel 계층).

문장 틀은 이 파일 맨 위 상수에만 둔다. 사실 요소(밤·시간대·장소·이름·결과)는 viewmodel 값을 그대로 넣고,
거짓 진술도 같은 틀을 써서 줄만 보고 진위를 알 수 없게 한다(docs/assets/spec.md 7절).
"""
import re
import zlib
from dataclasses import dataclass

from engine.domain.model import PLAYER_ID, ClaimType, CommandKind, Phase, Polarity
from engine.ports import PlayerView
from server.app.trust_cue import trust_cue
from .viewmodels import PLACE_KO, SLOT_KO

# 자기 행적 진술 문장 틀(11/10 사용자 승인).
STATEMENT_AFFIRM = "{night}일째 밤 {slot}에는 {place}에 있었소."
STATEMENT_DENY = "{night}일째 밤 {slot}에는 {place}에 없었소."
# 목격·못 봄 진술 문장 틀(11/10 사용자 승인). 점괘 공개는 무당 발언 문장을 그대로 쓴다(11/10 사용자 결정). 괄호 조사는 앞 글자 받침으로 골라 괄호 없이 낸다.
STATEMENT_SEEN = "{night}일째 밤 {slot}에 {place}에서 {names}을(를) 보았소."
STATEMENT_NOT_SEEN = "{night}일째 밤 {slot}에 {place}에서 {names}은(는) 보지 못했소."
STATEMENT_NOBODY = "{night}일째 밤 {slot}에 {place}에서는 아무도 보지 못했소."
NAME_JOIN = ", "            # 셋 이상일 때 앞 이름 사이
NAME_LAST = "와(과) "       # 마지막 이름 앞
SOMEONE = "누군가"          # 목격 대상이 비었을 때(진술표 카드 문구와 같음)
# 아래는 기존 기록 패널(공개된 투표·감금 결과·내 점괘)의 문구를 그대로 옮긴 것이다.
VOTE_ROUND_KO = {"main": "본투표", "runoff": "결선"}
VOTE_LINE = "{day}일째 {round} · {votes}"
LOCK_NONE = "{day}일째 · 무감금"
LOCK_LINE = "{day}일째 · {name} 감금"
LOCK_REVEAL = {True: "금줄 반응으로 씐 자임이 밝혀졌습니다.", False: "금줄 반응으로 씐 자가 아님이 밝혀졌습니다."}
DIVINATION_LINE = "{night}일째 밤 · {name} · {result}"
SPEAKER_VOTE = "투표 결과"
SPEAKER_LOCK = "감금 결과"
SPEAKER_DIVINATION = "무당"
# 투표 결과 발표 장면의 새 문장(11/10 사용자 지시, 승인 대기). 득표는 많은 순, 같으면 먼저 표를 받은 순.
VOTE_SUMMARY = {"main": "투표 결과 — {tally}", "runoff": "결선 결과 — {tally}"}
VOTE_TALLY_ITEM = "{name} {count}표"
RUNOFF_NOTICE = "{names}의 표가 같아 결선 투표로 넘어갑니다."
# 아침 실종 발표는 까치가 말한다(11/10 사용자 승인). 발표 줄은 밤 화면이 아니면 단계와 상관없이 까치 그림을 세운다.
SPEAKER_KKACHI = "까치"
KKACHI_MASCOT = "mascot:kkachi"
ANNOUNCE_MISSING = "깍깍! 간밤 {slot}에 {name}이(가) 사라졌어요. {name}의 짚신이 {place}에서 발견됐대요!"
ANNOUNCE_NONE = "깍깍! 간밤엔 아무도 사라지지 않았어요."
HOME_PLACE = "{name}의 집"   # 피해 장소가 집이면 기존 기록 문구처럼 누구의 집인지 밝힌다
# 플레이어가 주민을 의심하거나 모순을 지적하면 지목된 주민이 바로 한 줄 반응한다(11/10 사용자 승인).
# 엔진 규칙과 무관한 연출 줄이다: 믿음·투표·진술에 들어가지 않고, 결백·사실을 주장하지 않는 감정 표현만 둔다.
# 성향(콘텐츠 temperament_id)별 2개, 성향이 없거나 모르는 성향이면 None 줄. 고르기는 발언 ID로 결정적이다.
REACTION_SUSPECTED = {
    None: ("내가 뭘 어쨌다고 그러오!", "날 의심하다니, 억울하오."),
    "scholar": ("근거도 없이 나를 의심하는 것이오?", "의심은 자유이나, 섣부르구려."),
    "keen": ("아이고, 나를 잡겠다고? 헛짚었네.", "허허, 내가 수상해 보이나?"),
    "hothead": ("뭐? 나라고?", "누굴 의심하는 거냐!"),
    "timid": ("제, 제가요? 아, 아닙니다요!", "그, 그런 눈으로 보지 마셔요…"),
}
REACTION_CONTRADICTED = {
    None: ("내 말이 어디가 어긋났단 말이오?", "말이 다르다니, 무슨 소리요?"),
    "scholar": ("어느 대목이 어긋난다는 것인지 짚어 보시오.", "내 말의 앞뒤를 다시 들어 보시오."),
    "keen": ("말이 다르다니, 어디가 말인가?", "에이, 내 말을 잘못 들은 게지."),
    "hothead": ("다르긴 뭐가 달라!", "트집 잡지 마라!"),
    "timid": ("제, 제 말이 다르다고요…?", "어, 어디가 다르다는 건지… 모르겠습니다요."),
}
REACTIONS = {CommandKind.SUSPECT: REACTION_SUSPECTED, CommandKind.POINT_CONTRADICTION: REACTION_CONTRADICTED}


@dataclass(frozen=True)
class VNLine:
    """대사창 한 줄. sprite_id가 비면 인물을 세우지 않는다."""

    line_id: str
    day: int
    phase: str
    speaker: str
    text: str
    speaker_id: str = ""
    sprite_id: str = ""
    full: bool = False      # 전신 컷(첫 등장·추방 결과·엔딩·밤 습격 결과만)
    result: bool = False    # 투표 결과 발표 줄. 다 읽기 전에는 그 단계 행동을 잠근다
    guide: bool = False     # 첫 판 안내(족제비) 줄
    mascot: bool = False    # 마스코트(까치·족제비) 줄: 인물 자리 대신 무대 왼쪽 마스코트 자리에 세운다(밤 화면은 글만)
    actor_name: str = ""    # 전신 컷 인물 이름(화자와 다를 때, 그림 대체 글)
    beat: bool = False      # 그림만 보여 주는 한 박자 컷(앞 줄 문장 반복): 늘 전신 컷으로만 보이고 닫으면 다음 줄로, 밤 화면에서는 뺀다


def _name(view: PlayerView, npc_id: str) -> str:
    name = view.names.get(npc_id, npc_id)
    return "무당" if name == "mudang" else name


# 받침 유무로 고르는 조사: 표기 → (받침 있음, 받침 없음)
_PARTICLES = {"이(가)": ("이", "가"), "을(를)": ("을", "를"), "와(과)": ("과", "와"), "은(는)": ("은", "는"), "(이)라": ("이라", "라")}
_PARTICLE_RE = re.compile("|".join(re.escape(k) for k in _PARTICLES))
_DIGIT_BATCHIM = set("013678")   # 영·일·삼·육·칠·팔은 받침이 있다


def has_batchim(word: str) -> bool:
    """마지막 글자의 받침 여부. 한글 음절은 받침으로, 숫자는 읽는 소리로, 그 밖(영문 등)은 받침 없음으로 본다."""
    last = word.rstrip()[-1:] or " "
    if "가" <= last <= "힣":
        return (ord(last) - ord("가")) % 28 != 0
    return last in _DIGIT_BATCHIM


def attach_particles(text: str) -> str:
    """문장의 괄호 조사 표기를 바로 앞 낱말 받침에 맞는 조사 하나로 바꾼다."""
    out, at = [], 0
    for match in _PARTICLE_RE.finditer(text):
        before = text[at:match.start()]
        out.append(before)
        written = "".join(out)
        with_final, without = _PARTICLES[match.group()]
        out.append(with_final if has_batchim(written) else without)
        at = match.end()
    out.append(text[at:])
    return "".join(out)


def join_names(names: list[str]) -> str:
    """A / A와 B / A, B와 C처럼 마지막 이름 앞에만 와(과)를 붙인다."""
    if len(names) <= 1:
        return "".join(names)
    return attach_particles(NAME_JOIN.join(names[:-1]) + NAME_LAST + names[-1])


def statement_text(claim, names: list[str]) -> str:
    """진술 한 개를 화자의 말로 바꾼다. 진위와 무관하게 공개 값만 쓴다(거짓도 같은 틀)."""
    common = {"night": claim.night, "slot": SLOT_KO.get(claim.slot.value, "") if claim.slot else "",
              "place": PLACE_KO.get(claim.place.value, "") if claim.place else ""}
    if claim.claim_type is ClaimType.WHEREABOUTS:
        template = STATEMENT_DENY if claim.polarity is Polarity.DENY else STATEMENT_AFFIRM
        return template.format(**common)
    if claim.claim_type is ClaimType.SIGHTING:
        # 목격 대상이 비면 진술표 카드와 같이 "누군가"로 읽는다.
        return attach_particles(STATEMENT_SEEN.format(**common, names=join_names(names or [SOMEONE])))
    if not names:
        return STATEMENT_NOBODY.format(**common)
    return attach_particles(STATEMENT_NOT_SEEN.format(**common, names=join_names(names)))


def claim_card_text(view: PlayerView, claim) -> str:
    """진술표 카드 문장. 대사창 진술 줄과 같은 승인 문장 틀을 쓴다(11/10 사용자 승인)."""
    return statement_text(claim, [_name(view, n) for n in claim.seen])


def announcement_lines(view: PlayerView) -> list[VNLine]:
    """그날 아침 실종 발표를 까치 줄로 만든다. 공개 실종 기록이 없는 아침은 '아무도 사라지지 않았다'로 알린다.

    실종이 있으면 발표 줄 바로 다음에 피해자 전신 컷 줄을 한 박자 둔다(밤 습격 결과 전신 컷, spec 7절).
    전신 컷 동안 대사창은 숨으므로 새 문장 없이 발표 문장을 그대로 단다.
    2일째 아침부터는 그 뒤에 주민 신뢰 단서 해설 줄(화자 없음)을 하나 더 단다(`server.app.trust_cue`).
    """
    by_night = {record.night: record for record in view.public.disappearances}
    lines = []
    for day in range(1, view.day + 1):
        lines.extend(_announcement(view, day, by_night.get(day - 1)))
        cue = trust_cue(day, view.public.votes, view.public.speeches)
        if cue:
            lines.append(VNLine(f"trust-cue-{day}", day, Phase.MORNING.value, "", cue))
    return lines


def _announcement(view: PlayerView, day: int, record) -> list[VNLine]:
    """day일째 아침 까치 발표 줄과 (실종이 있으면) 피해자 전신 컷 줄."""
    if record is None:
        return [VNLine(f"announcement-none-{day}", day, Phase.MORNING.value, SPEAKER_KKACHI, ANNOUNCE_NONE,
                       sprite_id=KKACHI_MASCOT, mascot=True)]
    name = _name(view, record.victim)
    place = (HOME_PLACE.format(name=name) if record.place.value == "home"
             else PLACE_KO.get(record.place.value, record.place.value))
    text = attach_particles(ANNOUNCE_MISSING.format(slot=SLOT_KO.get(record.slot.value, ""), name=name, place=place))
    lines = [VNLine(f"announcement-{record.night}", day, Phase.MORNING.value, SPEAKER_KKACHI, text,
                    sprite_id=KKACHI_MASCOT, mascot=True)]
    if record.victim != "mudang":
        lines.append(VNLine(f"announcement-{record.night}-full", day, Phase.MORNING.value, SPEAKER_KKACHI, text,
                            record.victim, f"sprite:{record.victim}", full=True, actor_name=name, beat=True))
    return lines


def reaction_text(kind, speech_id: str, temperament_id) -> str:
    """지목된 주민의 반응 문장. (발언 종류, 발언 ID, 성향)이 같으면 늘 같은 문장이고, 모르는 성향은 None 줄을 쓴다."""
    table = REACTIONS[kind]
    variants = table.get(temperament_id, table[None])
    return variants[zlib.crc32(speech_id.encode("utf-8")) % len(variants)]


def _pointed_resident(view: PlayerView, speech):
    """반응할 주민. 의심은 대상, 모순 지적은 대상이 없으면 근거 두 진술에서 고른다.

    화면의 모순 지적 양식은 대상 칸 없이 진술 두 개(또는 모순 기록 1개)만 보낸다. 두 진술의 화자가 같으면 그 사람,
    다르면 한쪽 화자가 다른 쪽 진술의 목격 대상일 때 그 사람(남의 목격과 어긋난 당사자), 그 밖에는 고르지 않는다.
    """
    if speech.target:
        return speech.target
    if speech.kind is not CommandKind.POINT_CONTRADICTION:
        return None
    ids = []
    for ref in speech.evidence:
        if ref.type.value == "contradiction":
            ids = ref.record_id.split("|")
            break
        if ref.type.value == "claim":
            ids.append(ref.record_id)
    claims = {c.claim_id: c for c in view.public.claims}
    pair = [claims[i] for i in ids[:2] if i in claims]
    if len(pair) != 2:
        return None
    first, second = pair
    if first.speaker == second.speaker:
        return first.speaker
    pointed = [c.speaker for c, other in ((first, second), (second, first)) if c.speaker in (other.seen or [])]
    return pointed[0] if len(pointed) == 1 else None


def reaction_lines(view: PlayerView, temperaments=None) -> dict[str, list[VNLine]]:
    """플레이어의 의심 제기·모순 지적 발언 ID → 그 바로 뒤 지목된 주민의 반응 줄.

    공개 발언·진술 기록만으로 만들므로 새로 고침·기록 보기에서도 같은 줄이 나온다. 주민끼리의 발언, 지목된 주민을
    고를 수 없거나(`_pointed_resident`) 무당인 발언에는 붙이지 않는다. 대상의 지금 상태(감금·실종)와 무관하게 발언 당시 기록 그대로 보인다.
    """
    temperaments = temperaments or {}
    out: dict[str, list[VNLine]] = {}
    for speech in view.public.speeches:
        if speech.speaker != PLAYER_ID or speech.kind not in REACTIONS:
            continue
        target = _pointed_resident(view, speech)
        if not target or target == PLAYER_ID:
            continue
        text = reaction_text(speech.kind, speech.speech_id, temperaments.get(target))
        out[speech.speech_id] = [VNLine(f"reaction-{speech.speech_id}", speech.day, speech.phase.value,
                                        _name(view, target), text, target, f"sprite:{target}")]
    return out


def _tally(view: PlayerView, votes) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for vote in votes:
        counts[vote.target] = counts.get(vote.target, 0) + 1
    order = list(counts)
    return sorted(counts.items(), key=lambda item: (-item[1], order.index(item[0])))


def _result_lines(view: PlayerView) -> list[VNLine]:
    """투표 결과 발표 장면: 득표 요약 → 개별 표 → (동점이면 결선 안내) → 감금 전신 컷 → 금줄 반응.

    본투표 결과는 결선이 있거나 지금 결선 중인 날은 결선 화면에서, 아니면 그날 밤 화면에서 처음 보인다.
    """
    rounds: dict[tuple[int, str], list] = {}
    for vote in view.public.votes:
        rounds.setdefault((vote.day, vote.round), []).append(vote)
    has_runoff = {day for day, rnd in rounds if rnd == "runoff"}
    if view.phase is Phase.RUNOFF:
        has_runoff.add(view.day)
    locks = {lock.day: lock for lock in view.public.locks}
    lines = []
    for day in sorted({day for day, _ in rounds} | set(locks)):
        for rnd in ("main", "runoff"):
            votes = rounds.get((day, rnd))
            if not votes:
                continue
            phase = (Phase.RUNOFF if rnd == "main" and day in has_runoff else Phase.NIGHT).value
            tally = _tally(view, votes)
            summary = ", ".join(VOTE_TALLY_ITEM.format(name=_name(view, npc), count=n) for npc, n in tally)
            lines.append(VNLine(f"vote-summary-{day}-{rnd}", day, phase, SPEAKER_VOTE,
                                VOTE_SUMMARY.get(rnd, VOTE_SUMMARY["main"]).format(tally=summary), result=True))
            text = VOTE_LINE.format(day=day, round=VOTE_ROUND_KO.get(rnd, rnd),
                                    votes=", ".join(f"{_name(view, v.voter)} → {_name(view, v.target)}" for v in votes))
            lines.append(VNLine(f"vote-{day}-{rnd}", day, phase, SPEAKER_VOTE, text, result=True))
            if rnd == "main" and day in has_runoff:
                top = tally[0][1]
                tied = [_name(view, npc) for npc, n in tally if n == top]
                lines.append(VNLine(f"runoff-notice-{day}", day, phase, SPEAKER_VOTE,
                                    attach_particles(RUNOFF_NOTICE.format(names=join_names(tied))), result=True))
        lock = locks.get(day)
        if lock is None:
            continue
        night = Phase.NIGHT.value
        if lock.locked is None:
            lines.append(VNLine(f"lock-{day}", day, night, SPEAKER_LOCK, LOCK_NONE.format(day=day), result=True))
            continue
        # 추방 결과는 전신 컷 허용 조건이다(spec 7절). 밤 화면이어도 이 줄만 당사자를 세운다.
        sprite = f"sprite:{lock.locked}" if lock.locked != "mudang" else ""
        lines.append(VNLine(f"lock-{day}", day, night, SPEAKER_LOCK, LOCK_LINE.format(day=day, name=_name(view, lock.locked)),
                            lock.locked, sprite, full=bool(sprite), result=True))
        if lock.revealed_possessed is not None:
            lines.append(VNLine(f"lock-reveal-{day}", day, night, SPEAKER_LOCK, LOCK_REVEAL[lock.revealed_possessed],
                                result=True))
    return lines


def _divination_lines(view: PlayerView, result_ko: dict) -> list[VNLine]:
    """밤 N의 점괘는 N+1일째 아침에 무당 혼잣말로 보인다(인물 그림 없음)."""
    return [VNLine(f"divination-{night}-{target}", night + 1, Phase.MORNING.value, SPEAKER_DIVINATION,
                   DIVINATION_LINE.format(night=night, name=_name(view, target), result=result_ko[result]))
            for night, items in view.player.divinations.items() for target, result in items.items()]


def _statement_lines(view: PlayerView) -> list[VNLine]:
    """발언이 만들지 않은 주장(=아침 진술)을 화자가 서서 말하는 줄로 만든다."""
    spoken = {cid for speech in view.public.speeches for cid in speech.claim_ids}
    lines = []
    for claim in view.public.claims:
        if claim.claim_id in spoken or claim.claim_type is ClaimType.DIVINATION_REVEAL:
            continue
        text = statement_text(claim, [_name(view, n) for n in claim.seen])
        lines.append(VNLine(f"statement-{claim.claim_id}", claim.day, Phase.MORNING.value, _name(view, claim.speaker),
                            text, claim.speaker, f"sprite:{claim.speaker}"))
    return lines


def build_vn_lines(view: PlayerView, result_ko: dict) -> list[VNLine]:
    """같은 단계 안의 순서: 점괘 혼잣말 → 아침 진술 → 투표 결과 발표(득표·표·결선 안내·감금·금줄).

    실종 발표는 템플릿에서 이 줄들 앞에, 회의 발언은 뒤에 놓인다.
    """
    return _divination_lines(view, result_ko) + _statement_lines(view) + _result_lines(view)
