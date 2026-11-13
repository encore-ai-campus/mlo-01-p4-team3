"""도메인 규칙: 복기 해설 LLM 요약의 항목 선정·요청·응답 해석·문장 검사·템플릿 대체(E-10-01).

판 종료 뒤 복기 화면에서 NPC마다 "이 사람은 왜 그렇게 움직였나"를 1~3문장으로 요약한다(설계 D1 A안).
요약할 항목은 서버가 고르고(4.1), LLM은 고른 항목마다 한 문장을 인용 번호와 함께 쓴다(JSON 강제, 4.2).
검사에 걸린 NPC는 그 NPC 전체를 템플릿 요약으로 두고, JSON이 깨지거나 잘리면 판 전체를 템플릿으로 둔다.
의심 근거 풀이(승인 문구, `NarrationItem.basis`)는 LLM에 보내지 않고, 최종 문장(LLM·템플릿) 뒤에 그대로 붙인다(`with_basis`).

입력은 판 종료 뒤 공개되는 정보만이다(3.2): NPC 이름·성향, 진짜 역할(종료 뒤 공개), 복기 항목(날짜·단계·행동·
대상 이름·대표 이유 코드의 한국어 뜻·근거 진술의 공개 문장), 판 결과. 가명·세션·명령 ID, 믿음 수치, 플레이어
자유 입력, 템플릿 해설 문장 자체는 넣지 않는다.

계층: 도메인 성격의 순수 함수. 표준 라이브러리와 `llm`만 쓴다(`tests/contract/test_layers.py`).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from llm.free_answer import noun_candidates
from llm.validator import _BAD_CHARS_RE, _HANJA_KANA_RE, _LABEL_RE, _LATIN_RE, Lexicon, find_mentions

PROMPT_VERSION = "review-v1"
MAX_ITEMS = 3                 # NPC당 요약 항목 상한(4.1)
MAX_GAME_ITEMS = 10           # 판 전체 요약 항목 상한(출력 토큰·시간 제한, `_trim`)
MAX_SENTENCE_CHARS = 70       # 문장당 글자 수 상한(4.3 format_length)
MAX_RESPONSE_BYTES = 4096     # 응답 크기 상한(4.2)
MAX_UNKNOWN_NOUNS = 2         # 문장당 허용 어휘 밖 명사 후보 수 상한
GEN_SETTINGS = {"temperature": 0.3, "top_p": 0.9, "max_tokens": 900}

FORMAT_LENGTH = "format_length"
CITE_MISMATCH = "cite_mismatch"
FORBIDDEN_ENTITY = "forbidden_entity"
ACTION_MISMATCH = "action_mismatch"
TIME_MISMATCH = "time_mismatch"
INVENTED_CAUSE = "invented_cause"
PARSE_FAILED = "parse_failed"       # 판 전체 템플릿(JSON 파싱 실패·잘림·크기 초과)
REASONS = (FORMAT_LENGTH, CITE_MISMATCH, FORBIDDEN_ENTITY, ACTION_MISMATCH, TIME_MISMATCH, INVENTED_CAUSE)

SOURCE_LLM = "llm"
SOURCE_TEMPLATE = "template"

# 이유 코드 → 한 줄 뜻(고정). `ReasonCode`와 1:1(테스트). LLM 입력에 대표 이유의 뜻으로 넣는다.
REASON_DESC = {
    "baseline_silence": "특별히 할 말이 없어 말을 아꼈다",
    "baseline_explain": "캐묻기에 담담하게 해명했다",
    "max_suspicion": "가장 수상해 보이던 사람을 골랐다",
    "tie_seeded": "똑같이 수상해 보이는 사람들 가운데 하나를 골랐다",
    "skip_confessed": "비밀을 털어놓은 사람은 후보에서 뺐다",
    "avoid_partner": "같은 편인 창귀를 피했다",
    "fallback_all_legal": "뺄 사람을 빼니 후보가 없어 모두 가운데서 골랐다",
    "seeded_executor": "그날 밤 움직일 창귀를 정했다",
    "seeded_victim": "그날 밤 노릴 사람을 정했다",
    "scripted": "미리 정해 둔 순서를 따랐다",
    "witness_others_claim": "다른 사람이 봤다는 말을 근거로 판단했다",
    "witness_mutual_vouch": "서로 봤다는 두 사람의 말을 근거로 판단했다",
    "rule_point_contradiction": "상대의 말이 앞뒤가 맞지 않는 것을 짚었다",
    "rule_suspect": "근거를 들어 상대를 의심했다",
    "rule_defend": "상대를 직접 본 적이 있어 감쌌다",
    "rule_explain": "자기를 향한 의심에 해명했다",
    "rule_silence": "할 말이 없어 지켜보기만 했다",
    "exclude_proposition": "씐 자가 아니라고 밝혀진 사람은 후보에서 뺐다",
    "pressure_confess": "캐묻기에 몰려 숨긴 비밀을 털어놓았다",
    "react_explain": "캐묻기에 해명으로 답했다",
    "react_evade": "캐묻기에 말을 돌리며 피했다",
    "react_counter": "캐묻기에 오히려 되물으며 맞섰다",
    "frame_contradicted": "말이 어긋난 사람에게 누명을 씌우려 했다",
    "frame_suspected": "이미 의심받던 사람에게 누명을 씌우려 했다",
    "frame_mudang": "무당에게 의심이 쏠리도록 했다",
    "lie_unrefuted": "아무도 반박할 수 없는 곳에 있었다고 거짓말했다",
    "lie_routine": "평소 다니던 곳에 있었다고 거짓말했다",
    "night_accuser": "자기를 지목한 사람을 밤에 노렸다",
    "night_witness": "거짓말을 들킬 수 있는 사람을 밤에 노렸다",
    "night_trusts_mudang": "무당을 믿고 따르는 사람을 밤에 노렸다",
    "night_any": "마땅한 사람이 없어 아무나 밤에 노렸다",
    "model_estimate": "지금까지 나온 말과 표를 따져 표를 던졌다",
    "vote_conform": "다른 사람들이 찍을 것 같은 사람에게 따라 표를 던졌다",
    "fallback_max_suspicion": "후보가 모두 빠져 가장 수상한 사람에게 표를 던졌다",
    "model_fallback_unavailable": "평소 판단 기준대로 표를 던졌다",
    "model_fallback_version": "평소 판단 기준대로 표를 던졌다",
    "model_fallback_timeout": "평소 판단 기준대로 표를 던졌다",
    "model_fallback_invalid": "평소 판단 기준대로 표를 던졌다",
    "skip_trusted_mudang": "믿을 만한 무당은 후보에서 빼고 표를 던졌다",
    "abstain_uncertain": "누가 더 수상한지 가리기 어려워 표를 던지지 않았다",   # 11/11 투표 기권(rule-0.9)
}

KIND_KO = {"suspect": "의심 제기", "defend": "감싸기", "point_contradiction": "모순 지적", "explain": "해명",
           "silence": "지켜보기", "vote": "투표", "night_plan": "밤 계획", "react": "캐묻기 답"}
PHASE_KO = {"morning": "아침", "round1": "첫 회의", "round2": "둘째 회의", "interrogation": "캐묻기",
            "vote": "투표", "runoff": "결선 투표", "night": "밤"}
REACTION_KIND = {"explain": "react_explain", "evade": "react_evade", "counter": "react_counter",
                 "confess": "pressure_confess"}

# 행동 어휘군(4.3 action_mismatch). 문장에 인용 항목 행동의 어휘가 하나 이상 있어야 하고 반대 어휘군이 없어야 한다.
ACTION_WORDS = {
    "suspect": ("의심", "수상", "지목", "몰아"),
    "vote": ("표를", "표가", "찍", "투표", "지목"),
    "defend": ("감싸", "감쌌", "믿", "편을", "편에", "두둔", "변호"),
    "point_contradiction": ("어긋", "다르", "다른", "앞뒤", "맞지 않", "모순"),
    "night_plan": ("노렸", "노리", "해치", "밤에", "밤중", "데려"),
    "explain": ("해명", "떳떳", "결백", "아니라고", "변명"),
    "silence": ("지켜", "말을 아", "입을 다", "침묵", "조용"),
    "react_explain": ("해명", "답했", "대답", "설명", "떳떳"),
    "react_evade": ("피했", "피하", "돌리", "돌렸", "얼버무", "둘러"),
    "react_counter": ("되물", "맞섰", "맞서", "따졌", "반박"),
    "pressure_confess": ("털어놓", "고백", "밝혔", "실토", "비밀"),
}
OPPOSITE = {"suspect": "defend", "defend": "suspect"}
_NEG_AFTER = re.compile(r"^\s?[가-힣]{0,4}?\s?(?:지\s*않|지\s*못|않|못)")
_NEG_BEFORE = re.compile(r"(?:(?<![가-힣])안|(?<![가-힣])못)\s*$")
_CAUSE_RE = re.compile(r"때문|그래서|탓에|탓으로|기에|니까|므로|(?<![에로])서(?=\s)")
# 이유를 잇는 말에 흔히 쓰는 연결 어휘(인과 검사에서 지어낸 말로 세지 않음)
CAUSE_LINK_WORDS = ("같아", "따라", "보고", "들어", "따져", "근거로", "위해", "피해", "몰려", "믿어", "봤기", "있어",
                    "의심해", "의심받", "수상해", "직접", "본 적", "자기를", "자기")
_DAY_RE = re.compile(r"(\d+)\s*일\s*째|첫날|첫째 날|둘째 날|셋째 날")
_DAY_WORDS = {"첫날": 1, "첫째 날": 1, "둘째 날": 2, "셋째 날": 3}
_SENT_END = re.compile(r"[.?!]")
_META_RE = re.compile(r"이유는|이유에는|이유 없음|근거는|근거의 요지|없음|항목")   # 입력 칸 이름을 문장에 늘어놓음
MAX_COMMAS = 2

# 문장에 늘 쓸 수 있는 일반 어휘(명사 후보 검사에서 모르는 말로 세지 않음)
BASE_WORDS = frozenset((
    "마을", "주민", "사람", "무당", "창귀", "의심", "투표", "표를", "회의", "캐묻기", "비밀", "진술", "근거", "판단",
    "누명", "거짓말", "해명", "고백", "후보", "결백", "편이", "속셈", "정체", "다른", "사람들", "마음", "자기", "자신",
    "상대", "그날", "이날", "다음", "끝내", "결국", "끝까지", "처음", "마지막", "끝에", "모두", "가운데", "쪽으로",
    "평소", "기준", "목격", "증언", "모순", "앞뒤", "의견", "분위기", "다수", "몰래", "몸을", "신뢰", "믿음", "용의자",
))


class ParseFailed(ValueError):
    """응답 JSON을 해석할 수 없음(판 전체 템플릿)."""


@dataclass(frozen=True)
class NarrationItem:
    """요약 대상 항목 하나. 응용이 복기 항목·공개 기록에서 허용 필드만 골라 만든다."""
    item_id: str
    day: int
    phase: str
    actor: str
    kind: str                     # suspect|defend|point_contradiction|explain|silence|vote|night_plan|react
    code: str                     # 대표 이유 코드
    target: Optional[str] = None  # 대상 이름(투표·의심·감싸기·모순)
    victim: Optional[str] = None  # 밤 계획 대상 이름
    executor: Optional[str] = None  # 밤 계획에서 움직일 창귀 이름
    reaction: Optional[str] = None
    evidence: tuple[str, ...] = ()        # 근거 진술의 공개 문장
    template: str = ""                    # 그 항목의 템플릿 해설(대체 요약용, LLM에는 보내지 않음)
    basis: str = ""                       # 의심 근거 풀이(승인 문구). LLM에는 보내지 않고 최종 문장 뒤에 그대로 붙인다


@dataclass
class NpcBlock:
    """NPC 하나의 요약 입력."""
    npc_id: str
    name: str
    role: str                     # "창귀에 씐 사람" | "마을 사람"
    temperament: str = ""
    items: list[NarrationItem] = field(default_factory=list)


@dataclass
class NpcNarration:
    """NPC 하나의 최종 요약."""
    npc_id: str
    source: str                   # llm | template
    sentences: list[tuple[str, str]]   # (cite, text)
    reason: Optional[str] = None  # 템플릿이 된 사유


@dataclass
class NarrationResult:
    """판 하나의 요약 결과와 탈락 사유별 수."""
    npcs: list[NpcNarration]
    rejects: dict[str, int] = field(default_factory=dict)
    parse_failed: bool = False


# ---------------------------------------------------------------- 4.1 항목 선정
def _vote_picks(votes: list[NarrationItem]) -> list[NarrationItem]:
    """투표 중 최대 2개: 대상이 바뀐 지점(바뀐 뒤 투표)과 마지막 투표."""
    if not votes:
        return []
    picks: list[NarrationItem] = []
    for prev, cur in zip(votes, votes[1:]):
        if cur.target != prev.target:
            picks.append(cur)
            break
    last = votes[-1]
    if last not in picks:
        picks.append(last)
    if len(picks) < 2 and len(votes) >= 2 and votes[0] not in picks:
        picks.insert(0, votes[0])
    return sorted(picks[:2], key=_order)


def _order(item: NarrationItem) -> tuple[int, int]:
    return (item.day, int(item.item_id.lstrip("d") or 0))


def select_items(items: list[NarrationItem], possessed: set[str]) -> dict[str, list[NarrationItem]]:
    """NPC마다 요약할 항목(최대 3개)을 서버가 고른다. 고른 항목이 없는 NPC는 뺀다(4.1)."""
    by_actor: dict[str, list[NarrationItem]] = {}
    for item in sorted(items, key=_order):
        by_actor.setdefault(item.actor, []).append(item)
    out: dict[str, list[NarrationItem]] = {}
    for actor, own in by_actor.items():
        picks = _vote_picks([i for i in own if i.kind == "vote"])
        if actor in possessed:
            extra = [i for i in own if i.kind == "night_plan"]
        else:
            extra = [i for i in own if i.kind in ("suspect", "defend", "point_contradiction")]
        if not extra:
            extra = [i for i in own if i.kind == "react"]
        if extra:
            picks.append(extra[-1])
        picks = sorted(picks, key=_order)[:MAX_ITEMS]
        if picks:
            out[actor] = picks
    return _trim(out)


def _trim(out: dict[str, list[NarrationItem]]) -> dict[str, list[NarrationItem]]:
    """판 전체 항목이 `MAX_GAME_ITEMS`를 넘으면 마지막 투표가 아닌 투표부터(이른 것부터) 뺀다.

    3B 모델 출력 속도(약 13 tok/s)로 요청 제한(90 s) 안에 끝내기 위한 상한이다(11/11 실측)."""
    total = sum(len(v) for v in out.values())
    if total <= MAX_GAME_ITEMS:
        return out
    droppable = []
    for actor, picks in out.items():
        votes = [i for i in picks if i.kind == "vote"]
        droppable.extend((i, actor) for i in votes[:-1] if len(picks) > 1)
    for item, actor in sorted(droppable, key=lambda p: _order(p[0])):
        if total <= MAX_GAME_ITEMS:
            break
        out[actor] = [i for i in out[actor] if i is not item]
        total -= 1
    return out


# ---------------------------------------------------------------- 3.3 요청
def topic_josa(name: str) -> str:
    """이름 뒤 주제 조사(은/는). 마지막 글자가 한글이 아니면 '는'."""
    ch = name[-1:] if name else ""
    if not ("가" <= ch <= "힣"):
        return "는"
    return "은" if (ord(ch) - 0xAC00) % 28 else "는"


SYSTEM_PROMPT = "\n".join((
    "당신은 판이 끝난 뒤 마을 사람들의 행동을 풀이해 주는 복기 해설자입니다.",
    "입력의 각 항목(d로 시작하는 번호)마다 그 사람의 행동과 이유를 이어 자연스러운 한 문장으로 씁니다.",
    "규칙:",
    "1. 문장은 그 사람 이름으로 시작하고 '~했습니다.'처럼 '습니다.'로 끝냅니다.",
    "2. 행동에 나온 대상 이름을 꼭 넣고, 이유의 뜻을 바꾸지 않습니다. 근거가 있으면 근거의 요지를 짧게 넣습니다.",
    "3. 항목에 없는 사람·장소·시간·사건을 지어내지 않습니다. 쌍점·괄호·따옴표·목록을 쓰지 않습니다.",
    "4. 한 문장은 70자 이하입니다.",
    "예시(갑순·을동·나루터는 예시용이라 실제 문장에 쓰지 않습니다):",
    "- 행동: 을동에게 표를 던졌다 / 이유: 다른 사람들이 찍을 것 같은 사람에게 따라 표를 던졌다",
    "  ⇒ 갑순은 다른 사람들이 찍을 것 같은 을동에게 따라서 표를 던졌습니다.",
    "- 행동: 을동을 의심했다 / 이유: 근거를 들어 상대를 의심했다 / 근거: 을동의 말: 1일째 밤 한밤에 나루터에 있었다",
    "  ⇒ 갑순은 한밤에 나루터에 있었다는 을동의 말을 근거로 을동을 의심했습니다.",
    "- 행동: 그날 밤 을동을 노렸다 / 이유: 자기를 지목한 사람을 밤에 노렸다",
    "  ⇒ 갑순은 자기를 지목한 을동을 그날 밤 노렸습니다.",
    '출력은 JSON 하나입니다: {"npcs":[{"npc":ID,"sentences":[{"cite":항목번호,"text":문장}]}]}',
))


def object_josa(name: str) -> str:
    """이름 뒤 목적격 조사(을/를)."""
    return "을" if topic_josa(name) == "은" else "를"


REACT_ACTION = {"explain": "캐묻기에 해명했다", "evade": "캐묻기에 말을 돌리며 피했다",
                "counter": "캐묻기에 오히려 되물으며 맞섰다", "confess": "캐묻기에 몰려 숨긴 비밀을 털어놓았다"}


def action_phrase(item: NarrationItem, actor_name: str) -> str:
    """항목의 행동을 짧은 서술구로(대상 이름 포함). 승인된 템플릿 해설 문장과는 별개의 사실 구절이다."""
    t = item.target or "다른 사람"
    if item.kind == "vote":
        return f"{t}에게 표를 던졌다"
    if item.kind == "suspect":
        return f"{t}{object_josa(t)} 의심했다"
    if item.kind == "defend":
        return f"{t}{object_josa(t)} 감쌌다"
    if item.kind == "point_contradiction":
        return f"{t}의 말이 앞뒤가 맞지 않다고 짚었다"
    if item.kind == "explain":
        return "자기를 향한 의심에 해명했다"
    if item.kind == "silence":
        return "말을 아끼고 지켜보기만 했다"
    if item.kind == "react":
        return REACT_ACTION.get(item.reaction or "", REACT_ACTION["explain"])
    if item.kind == "night_plan":
        victim = item.victim or "누군가"
        if item.executor and item.executor != actor_name:
            return f"그날 밤 {item.executor}{object_josa(item.executor)} 움직여 {victim}{object_josa(victim)} 노렸다"
        return f"그날 밤 {victim}{object_josa(victim)} 노렸다"
    return KIND_KO.get(item.kind, item.kind)


def _item_line(item: NarrationItem, actor_name: str = "") -> str:
    when = f"{item.day}일째 {PHASE_KO.get(item.phase, item.phase)}"
    lines = [f"- {item.item_id} ({when})", f"  행동: {action_phrase(item, actor_name)}",
             f"  이유: {REASON_DESC.get(item.code, item.code)}"]
    if item.evidence:
        lines.append("  근거: " + " / ".join(item.evidence))
    return "\n".join(lines)


def build_user_prompt(blocks: list[NpcBlock], result_text: str) -> str:
    """사용자 메시지: 판 결과와 NPC별 항목 블록."""
    lines = [f"판 결과: {result_text}"]
    for b in blocks:
        role = b.role
        lines.append(f"[npc={b.npc_id}] {b.name} ({role})")
        lines.extend(_item_line(i, b.name) for i in b.items)
    return "\n".join(lines) + "\n"


def build_messages(blocks: list[NpcBlock], result_text: str) -> list[dict]:
    """OpenAI 호환 chat 메시지(시스템 + 실제 요청). 대화형 예시는 3B 모델이 사실을 베껴 넣어 쓰지 않는다."""
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(blocks, result_text)}]


def response_schema(blocks: list[NpcBlock]) -> dict:
    """llama-server `response_format`용 JSON 스키마(3.3). NPC 순서·인용 순서를 `const`로 고정해 고른 항목마다
    정확히 한 문장을 쓰게 한다. 응답 모양은 4.2 `{"npcs":[{"npc","sentences":[{"cite","text"}]}]}` 그대로다."""
    def sentence(item: NarrationItem, subject: str) -> dict:
        # 주어로 시작하고 마침표로 끝나는 한 문장(따옴표·줄바꿈·다른 마침 부호 없음)
        # '습니다.'까지 문법으로 강제하면 3B 모델이 끝을 못 찾아 쉼표 나열로 채운다(11/11 실측). 끝맺음은 검사로 본다.
        pattern = "^" + subject + " [^\"\\n.?!:()]{%d,%d}[.]$" % (6, MAX_SENTENCE_CHARS - len(subject) - 2)
        return {"type": "object", "required": ["cite", "text"], "additionalProperties": False,
                "properties": {"cite": {"const": item.item_id}, "text": {"type": "string", "pattern": pattern}}}

    def npc(block: NpcBlock) -> dict:
        n = len(block.items)
        subject = block.name + topic_josa(block.name)
        return {"type": "object", "required": ["npc", "sentences"], "additionalProperties": False,
                "properties": {"npc": {"const": block.npc_id},
                               "sentences": {"type": "array", "prefixItems": [sentence(i, subject) for i in block.items],
                                             "minItems": n, "maxItems": n}}}
    n = len(blocks)
    return {"type": "object", "required": ["npcs"], "additionalProperties": False,
            "properties": {"npcs": {"type": "array", "prefixItems": [npc(b) for b in blocks],
                                    "minItems": n, "maxItems": n}}}


def request_body(blocks: list[NpcBlock], result_text: str, seed: int) -> dict:
    """`POST /v1/chat/completions` 본문."""
    return {"messages": build_messages(blocks, result_text), **GEN_SETTINGS, "seed": seed % (2 ** 31),
            "response_format": {"type": "json_schema", "json_schema": {"name": "review", "schema": response_schema(blocks)}}}


# ---------------------------------------------------------------- 4.2 응답 해석
def parse_response(text: str, finish_reason: Optional[str]) -> dict[str, list[tuple[str, str]]]:
    """응답 본문 → {npc: [(cite, text)]}. 잘림·크기 초과·JSON 오류·모양 오류는 `ParseFailed`(판 전체 템플릿)."""
    if finish_reason == "length":
        raise ParseFailed("truncated")
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ParseFailed("too_large")
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ParseFailed("invalid_json") from None
    if not isinstance(data, dict) or set(data) != {"npcs"} or not isinstance(data["npcs"], list):
        raise ParseFailed("bad_shape")
    out: dict[str, Optional[list[tuple[str, str]]]] = {}
    for row in data["npcs"]:
        if not isinstance(row, dict) or set(row) != {"npc", "sentences"} or not isinstance(row["sentences"], list):
            raise ParseFailed("bad_shape")
        npc = str(row["npc"])
        sentences = []
        for s in row["sentences"]:
            if not isinstance(s, dict) or set(s) != {"cite", "text"}:
                raise ParseFailed("bad_shape")
            sentences.append((str(s["cite"]), str(s["text"])))
        out[npc] = None if npc in out else sentences   # 같은 NPC가 두 번 나오면 그 NPC는 인용 불일치
    return out


def structure_reason(npc_sentences: Optional[list[tuple[str, str]]], block: NpcBlock) -> Optional[str]:
    """NPC 하나의 인용 구조 검사: 고른 항목을 모두 한 번씩 인용, 중복·여분·빈 문장 없음."""
    if not npc_sentences:
        return CITE_MISMATCH
    cites = [c for c, _ in npc_sentences]
    want = [i.item_id for i in block.items]
    if len(cites) != len(set(cites)) or sorted(cites) != sorted(want):
        return CITE_MISMATCH
    if any(not t.strip() for _, t in npc_sentences):
        return CITE_MISMATCH
    return None


# ---------------------------------------------------------------- 4.3 문장 검사
def _format_reason(text: str) -> Optional[str]:
    t = text.strip()
    if len(t) > MAX_SENTENCE_CHARS or len(_SENT_END.findall(t)) != 1 or not t.endswith("습니다."):
        return FORMAT_LENGTH
    if _LATIN_RE.search(t) or _HANJA_KANA_RE.search(t) or _BAD_CHARS_RE.search(t) or _LABEL_RE.match(t):
        return FORMAT_LENGTH
    words = re.findall(r"[가-힣]{2,}", t)
    if t.count(",") > MAX_COMMAS or _META_RE.search(t) or any(words.count(w) >= 3 for w in set(words)):
        return FORMAT_LENGTH
    return None


def _has_word(text: str, words: tuple[str, ...]) -> list[int]:
    return [m.end() for w in words for m in re.finditer(re.escape(w), text)]


def _negated(text: str, ends: list[int], words: tuple[str, ...]) -> bool:
    for w in words:
        for m in re.finditer(re.escape(w), text):
            if _NEG_AFTER.match(text[m.end():]) or _NEG_BEFORE.search(text[:m.start()]):
                return True
    return False


def _action_key(item: NarrationItem) -> str:
    if item.kind == "react":
        return REACTION_KIND.get(item.reaction or "", "react_explain")
    return item.kind


def _action_reason(text: str, item: NarrationItem, block: NpcBlock) -> Optional[str]:
    key = _action_key(item)
    words = ACTION_WORDS.get(key, ())
    hits = _has_word(text, words)
    if words and not hits:
        return ACTION_MISMATCH
    if words and _negated(text, hits, words):
        return ACTION_MISMATCH
    opposite = OPPOSITE.get(key)
    if opposite and _has_word(text, ACTION_WORDS[opposite]) and key != "vote":
        return ACTION_MISMATCH
    if block.name not in text and not text.startswith(("그는", "그녀는")):
        return ACTION_MISMATCH
    who = item.victim if item.kind == "night_plan" else item.target
    if who and item.kind in ("suspect", "defend", "vote", "night_plan", "point_contradiction") and who not in text:
        return ACTION_MISMATCH
    if key == "vote":
        # 투표는 "대상에게 표를 던짐"이 문장에 있어야 하고 감싸기 어휘가 없어야 한다("말과 표를 따져"만으로는 부족)
        if _has_word(text, ACTION_WORDS["defend"]):
            return ACTION_MISMATCH
        if who and not re.search(re.escape(who) + r"(?:에게|에|을|를)\s?(?:[가-힣]+\s){0,2}(?:표를|표가|찍|투표)", text):
            return ACTION_MISMATCH
    return None


def _days(text: str) -> set[int]:
    out = set()
    for m in _DAY_RE.finditer(text):
        day = int(m.group(1)) if m.group(1) else _DAY_WORDS.get(m.group(0))
        if day is not None:
            out.add(day)
    return out


def _time_reason(text: str, item: NarrationItem) -> Optional[str]:
    """문장의 날짜는 그 항목의 날이거나 근거 진술에 나온 날(예: "1일째 밤 한밤에")이어야 한다."""
    allowed = {item.day} | _days(" ".join(item.evidence))
    return TIME_MISMATCH if _days(text) - allowed else None


def _allowed_names(block: NpcBlock, item: NarrationItem) -> set[str]:
    names = {block.name, "무당"}
    for v in (item.target, item.victim):
        if v:
            names.add(v)
    return names


def _vocab(block: NpcBlock, item: NarrationItem) -> set[str]:
    words = set(BASE_WORDS)
    sources = [REASON_DESC.get(item.code, ""), KIND_KO.get(item.kind, ""), *item.evidence, block.role, block.temperament]
    for s in sources:
        words.update(noun_candidates(s))
        words.update(re.findall(r"[가-힣]{2,}", s))
    for n in _allowed_names(block, item):
        words.add(n)
    return words


def _entity_reason(text: str, item: NarrationItem, block: NpcBlock, lexicon: Lexicon) -> Optional[str]:
    allowed_names = _allowed_names(block, item)
    evidence_text = " ".join(item.evidence)
    for m in find_mentions(text, lexicon):       # 역할어(창귀·씐 자)는 판 종료 뒤 공개 정보라 따지지 않는다
        if m.kind == "name" and m.key not in allowed_names and m.key not in evidence_text:
            return FORBIDDEN_ENTITY
        if m.kind == "place" and m.key not in evidence_text:
            return FORBIDDEN_ENTITY
    vocab = _vocab(block, item)
    unknown = [n for n in noun_candidates(text) if n not in vocab and not any(n.startswith(v) for v in vocab)]
    if len(unknown) > MAX_UNKNOWN_NOUNS:
        return FORBIDDEN_ENTITY
    return None


def _cause_reason(text: str, item: NarrationItem, block: NpcBlock) -> Optional[str]:
    m = _CAUSE_RE.search(text)
    if not m:
        return None
    vocab = _vocab(block, item)
    head = text[:m.start()]
    unknown = [n for n in noun_candidates(head) if n not in vocab and not any(n.startswith(v) for v in vocab)]
    if unknown:
        return INVENTED_CAUSE
    # 이유 구절의 낱말(2글자 이상)은 앞 두 글자가 항목의 이유·근거·행동·이름에 나와야 한다("돈을 훔쳐서" 같은 새 사건 거부)
    known = " ".join((REASON_DESC.get(item.code, ""), action_phrase(item, block.name), *item.evidence,
                      block.name, " ".join(BASE_WORDS), " ".join(CAUSE_LINK_WORDS)))
    for tok in re.findall(r"[가-힣]{2,}", head)[1:]:              # 첫 낱말은 주어
        if tok[:2] not in known:
            return INVENTED_CAUSE
    return None


def normalize_ending(text: str) -> str:
    """과거형 평서 끝맺음 '~ㅆ다.'만 해설체 '~ㅆ습니다.'로 바꾼다(뜻은 그대로인 기계적 문체 맞춤).

    3B 모델이 '습니다.'를 자주 빠뜨린다(11/11 실측 형식 탈락의 대부분). 그 밖의 끝맺음은 그대로 두고 검사가 본다."""
    t = text.strip()
    if t.endswith("다.") and not t.endswith("니다.") and len(t) >= 3:
        ch = t[-3]
        if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 == 20:      # 받침 ㅆ(했·었·았·렸·있)
            return t[:-2] + "습니다."
    return t


def check_sentence(text: str, item: NarrationItem, block: NpcBlock, lexicon: Lexicon) -> Optional[str]:
    """문장 하나의 탈락 사유(통과면 None). 순서: 형식 → 인물·장소 → 행동 → 시간 → 인과."""
    return (_format_reason(text) or _entity_reason(text, item, block, lexicon) or _action_reason(text, item, block)
            or _time_reason(text, item) or _cause_reason(text, item, block))


# ---------------------------------------------------------------- 결과 조립
def with_basis(item: NarrationItem, text: str) -> str:
    """최종 문장 뒤에 의심 근거 풀이를 고치지 않고 붙인다(11/11). 문장 글자 수 상한은 LLM 문장에만 적용된다."""
    if not item.basis:
        return text
    return f"{text} {item.basis}" if text else item.basis


def template_summary(block: NpcBlock) -> NpcNarration:
    """고른 항목들의 템플릿 해설(풀이를 붙인)을 이어 붙인 대체 요약."""
    return NpcNarration(block.npc_id, SOURCE_TEMPLATE,
                        [(i.item_id, with_basis(i, i.template)) for i in block.items if i.template or i.basis])


def all_template(blocks: list[NpcBlock], parse_failed: bool = False) -> NarrationResult:
    """판 전체 템플릿(JSON 오류·서버 정지·마감 초과)."""
    result = NarrationResult([template_summary(b) for b in blocks], parse_failed=parse_failed)
    if parse_failed:
        result.rejects[PARSE_FAILED] = 1
    return result


def narrate(blocks: list[NpcBlock], text: str, finish_reason: Optional[str], lexicon: Lexicon) -> NarrationResult:
    """LLM 응답을 해석·검사해 NPC별 최종 요약을 만든다. 걸린 NPC는 그 NPC만 템플릿으로 둔다."""
    try:
        parsed = parse_response(text, finish_reason)
    except ParseFailed:
        return all_template(blocks, parse_failed=True)
    if set(parsed) - {b.npc_id for b in blocks}:
        return all_template(blocks, parse_failed=True)    # 여분 NPC: 스키마 위반 응답은 믿지 않는다
    rejects: dict[str, int] = {}
    out: list[NpcNarration] = []
    for block in blocks:
        sentences = parsed.get(block.npc_id)
        if sentences:
            sentences = [(c, normalize_ending(t)) for c, t in sentences]
        reason = structure_reason(sentences, block)
        if reason is None:
            items = {i.item_id: i for i in block.items}
            for cite, sentence in sentences:
                reason = check_sentence(sentence, items[cite], block, lexicon)
                if reason:
                    break
        if reason:
            rejects[reason] = rejects.get(reason, 0) + 1
            fallback = template_summary(block)
            fallback.reason = reason
            out.append(fallback)
        else:
            order = {i.item_id: n for n, i in enumerate(block.items)}
            items = {i.item_id: i for i in block.items}
            out.append(NpcNarration(block.npc_id, SOURCE_LLM,
                                    sorted(((c, with_basis(items[c], t.strip())) for c, t in sentences),
                                           key=lambda p: order[p[0]])))
    return NarrationResult(out, rejects)


def merge(results: list[NarrationResult]) -> NarrationResult:
    """NPC별 요청 결과를 판 하나의 결과로 합친다(작업 하나 = NPC마다 요청 1회)."""
    out = NarrationResult([])
    for r in results:
        out.npcs.extend(r.npcs)
        out.parse_failed = out.parse_failed or r.parse_failed
        for k, v in r.rejects.items():
            out.rejects[k] = out.rejects.get(k, 0) + v
    return out
