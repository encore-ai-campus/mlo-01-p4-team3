"""도메인 규칙: 회의 발언 다듬기(E-11-13, `npc_llm_design.md` 1·3절).

NPC가 회의에서 한 공개 발언(의심·변호·모순 지적·해명 4종)을 그 인물 말투로 어미·어순만 바꿔 다시 쓰게 하는
요청·프롬프트·출력 검사·결과 선택 규칙을 둔다. 생성(HTTP)과 저장은 호출하는 쪽(응용 `server/app/npc_lines.py`,
인프라)이 한다. 표준 라이브러리와 같은 `llm` 도메인 모듈만 쓴다.

누출 기준: 요청에는 그 시점에 이미 화면에 공개될 정보만 들어간다. 요청 조립 함수는 발언 원문·말투·종류·허용 이름
같은 문자열만 받고 판 진실·믿음·비밀 객체를 받지 않는다(`tests/llm/test_npc_polish.py`가 정적 검사).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from llm.free_answer import STOP_NOUNS, noun_candidates, split_clauses, split_sentences
from llm.prompt import example_pick, speaker_and_style, style_profile
from llm.validator import (FORBIDDEN_ENTITY, FORMAT_LENGTH, Lexicon, ValidationResult, find_mentions, normalize_output,
                           validate)

PROMPT_VERSION = "meeting-polish-v3"   # v2: 인물 말투 계열의 짧은 앞말을 예시로(11/11 실측: v1은 "허,"만 베낌), v3: 고정 지시를 앞으로(캐시)
VALIDATOR_VERSION = "meeting-polish-check-v1"

POLISH_KINDS = ("suspect", "defend", "point_contradiction", "explain")   # 지켜보기는 "……" 같은 비언어 줄이 있어 제외(D1)
KIND_KO = {"suspect": "의심", "defend": "감싸기", "point_contradiction": "말이 어긋난 곳 짚기", "explain": "자기 해명"}

MAX_ADDED_HANGUL = 12   # 원문 대비 늘어난 한글 글자 수 상한(3절)

# 생성 설정. 짧은 한 줄이라 출력 상한을 작게 둔다. 요청마다 독립 메시지다. 서버 프롬프트 캐시(`cache_prompt`)는 같은
# 앞부분 글자(시스템 지시·예시)의 계산만 다시 쓰고 다른 요청의 내용을 섞지 않아 켠다(11/11 실측: 문장당 약 4초 → 약 1.5초).
GEN_SETTINGS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 64}

# 판단 방향 어휘(3절). 원문보다 많이 나오면 판단이 바뀐 것으로 본다.
_DEFEND_WORDS = (r"믿", r"감싸", r"결백", r"죄\s*없", r"죄가\s*없", r"억울", r"아닐\s*게", r"착한", r"좋은\s*사람")
_SUSPECT_WORDS = (r"수상", r"의심스럽", r"의심하", r"거짓", r"이상하", r"구린", r"범인", r"켕기", r"찜찜")
_DIRECTION_BLOCK = {"suspect": _DEFEND_WORDS, "point_contradiction": _DEFEND_WORDS, "defend": _SUSPECT_WORDS}
_DIRECTION_RE = {k: [re.compile(p) for p in v] for k, v in _DIRECTION_BLOCK.items()}
_HANGUL = re.compile(r"[가-힣]")

# 말투만 바꿀 때 흔히 붙는 일반 명사. 고유명사 후보 검사에서 새로 나와도 허용한다.
COMMON_NOUNS = frozenset(("사람", "자네", "당신", "그대", "이보게", "여보게", "얘기", "이야기", "생각", "마음", "정말",
                          "참말", "진짜", "그쪽", "이쪽", "우리", "모두", "다들", "여러분", "이번", "지금", "아까",
                          "앞뒤", "말씀", "소리", "노릇", "양반"))

DIRECTION = "direction"
ADDED_SENTENCE = "added_sentence"
NEW_NOUN = "new_noun"
REASONS = (FORMAT_LENGTH, FORBIDDEN_ENTITY, "added_assertion", "fact_changed", ADDED_SENTENCE, DIRECTION, NEW_NOUN)


@dataclass(frozen=True)
class PolishRequest:
    """다듬기 요청 1건. 모두 공개 문자열이다."""
    speech_id: str
    kind: str
    text: str                       # 공개 원문(`Speech.line_text`, 이름이 채워진 상태)
    persona: str                    # 화자 말투(`Character.speech_style`, 콘텐츠 공개 설정)
    allowed_names: tuple[str, ...]  # 원문에 나온 인물 이름

    def as_dict(self) -> dict:
        """요청을 작업 문서에 저장할 사전으로 바꾼다."""
        return {"speech_id": self.speech_id, "kind": self.kind, "text": self.text, "persona": self.persona,
                "allowed_names": list(self.allowed_names)}


def eligible(kind: str, text: str) -> bool:
    """다듬기 대상 발언인가: 4종이고 한글 원문이 있다."""
    return kind in POLISH_KINDS and bool(_HANGUL.search(text or ""))


def build_request(speech_id: str, kind: str, text: str, persona: str, lexicon: Lexicon) -> PolishRequest:
    """공개 원문·말투·종류로 요청을 만든다. 허용 이름은 원문에서 찾은 인물 이름(대표 이름)뿐이다."""
    names = tuple(dict.fromkeys(m.key for m in find_mentions(text, lexicon) if m.kind == "name"))
    return PolishRequest(speech_id, kind, text, persona, names)


def short_prefixes(style: str) -> tuple[str, ...]:
    """말투 계열 앞말 중 검사(문장·절 수)를 넘지 않는 짧은 것. 끝 마침표는 쉼표로 바꾼다("에헴." → "에헴,")."""
    out = []
    for p in style_profile(style)[0]:
        p = p[:-1] + "," if p.endswith(".") else p
        if all(len(_HANGUL.findall(c)) <= 2 for c in split_clauses(p)) and len(split_sentences(p)) <= 1:
            out.append(p)
    return tuple(out) or ("허,",)


def build_messages(req: PolishRequest) -> list[dict]:
    """회의 발언 전용 채팅 메시지(system, 예시 한 쌍, user).

    예시는 이 인물 말투 계열의 짧은 앞말 하나(발언 ID로 고름)를 붙이고 나머지는 그대로 둔 문장이다. 예시 이름
    "갑돌이"는 게임에 없어서 모델이 예시를 베끼면 원문 이름이 빠져 검사에 걸린다.
    """
    _, style = speaker_and_style(req.persona)
    prefixes = short_prefixes(style)
    endings = style_profile(style)[1]
    prefix = prefixes[example_pick(req.speech_id) % len(prefixes)]
    # 바뀌지 않는 지시를 앞에 두고 인물 말투·발언 종류는 뒤에 둔다. 서버 프롬프트 캐시가 앞부분을 다시 쓰게 해서
    # 인물·종류가 바뀌어도 지연이 덜 늘어난다(11/11 실측).
    system = (
        "너는 조선 시대 산골 마을 사람이다. 마을 회의에서 한 말을 다시 말한다. "
        "받은 문장의 낱말·사람 이름·판단은 그대로 둔다. 앞에 이 인물다운 짧은 감탄사를 붙이거나, "
        "문장 끝 어미만 이 인물 말투로 바꾼다. 문장 수를 늘리지 않는다. "
        "받은 문장에 없는 사람·장소·시간·사실을 더하지 않는다. 대사만 한 줄로 쓰고 따옴표·괄호·설명은 쓰지 않는다.\n"
        f"말투: {style}. 어미 예: {', '.join(endings[:3])}. 발언 종류: {KIND_KO.get(req.kind, '말하기')}."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": "발언: 갑돌이가 수상하오."},
            {"role": "assistant", "content": f"{prefix} 갑돌이가 수상하오."},
            {"role": "user", "content": f"발언: {req.text}"}]


def request_body(req: PolishRequest) -> dict:
    """OpenAI 호환 요청 본문. 같은 요청이면 같은 바이트가 나오게 키 순서를 고정한다."""
    return {"messages": build_messages(req), **GEN_SETTINGS, "stop": ["\n"], "cache_prompt": True}


def _count(patterns, text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns)


def _clauses(sentences: list[str]) -> int:
    """절 수. 한글 2자 이하 조각("허,", "아이고,")은 감탄사로 보고 세지 않는다."""
    return sum(1 for s in sentences for c in split_clauses(s) if len(_HANGUL.findall(c)) > 2)


def check(raw: str, req: PolishRequest, lexicon: Lexicon) -> tuple[str, ValidationResult]:
    """생성 원문을 다듬어 검사한다. (정리한 문장, 결과). 결과가 통과가 아니면 템플릿을 쓴다.

    기존 `validate`(형식·인물·역할어·부정/단정 추가·원문 어절 보존)에 3절 검사를 더한다: 문장 수·절 수는 원문
    이하, 한글은 +12자 이내, 원문에 이름이 없으면 인물·장소·시간 언급 0, 판단 방향 어휘 추가 금지, 사전에 없는
    새 명사 후보 금지. 이 검사도 뜻이 같음을 보장하지 않는다(D9 표본 승인으로 판단).
    """
    text = normalize_output(raw)
    base = validate(text, {"fact_sentence": req.text, "persona": req.persona,
                           "allowed_names": list(req.allowed_names)}, lexicon)
    reasons, details = list(base.reasons), list(base.details)

    def add(reason: str, detail: str) -> None:
        if reason not in reasons:
            reasons.append(reason)
        details.append(detail)

    src_sents, out_sents = split_sentences(req.text), split_sentences(text)
    if len(out_sents) > max(1, len(src_sents)):
        add(ADDED_SENTENCE, f"문장 {len(src_sents)}→{len(out_sents)}")
    src_clauses = _clauses(src_sents) or 1
    out_clauses = _clauses(out_sents)
    if out_clauses > src_clauses:
        add(ADDED_SENTENCE, f"절 {src_clauses}→{out_clauses}")
    added = len(_HANGUL.findall(text)) - len(_HANGUL.findall(req.text))
    if added > MAX_ADDED_HANGUL:
        add(FORMAT_LENGTH, f"덧붙인 한글 {added}자 > {MAX_ADDED_HANGUL}자")
    src_mentions = find_mentions(req.text, lexicon)
    if not any(m.kind in ("name", "place", "time") for m in src_mentions):
        extra = [m.surface for m in find_mentions(text, lexicon) if m.kind in ("name", "place", "time")]
        if extra:
            add(FORBIDDEN_ENTITY, "이름 없는 원문에 언급 추가 " + ",".join(extra))
    block = _DIRECTION_RE.get(req.kind, ())
    if block and _count(block, text) > _count(block, req.text):
        add(DIRECTION, "판단 방향 어휘 추가")
    known = set(noun_candidates(req.text)) | STOP_NOUNS | COMMON_NOUNS
    for group in (lexicon.names, lexicon.places):
        for surfaces in group.values():
            known.update(surfaces)
    new = [n for n in noun_candidates(text) if n not in known and not any(n.startswith(k) or k.startswith(n)
                                                                          for k in known if len(k) >= 2)]
    if new:
        add(NEW_NOUN, "새 명사 후보 " + ",".join(dict.fromkeys(new)))
    ordered = tuple(r for r in REASONS if r in reasons)
    return text, ValidationResult(not ordered, ordered, tuple(details))


# ---- 결과 선택 규칙(D3) ----
POLISHED, TEMPLATE = "polished", "template"


def shown_text(final: dict | None, ready_text: str | None, template: str) -> tuple[str, str]:
    """화면에 띄울 (변형, 문장). 고정된 값이 있으면 그것, 아니면 준비된 다듬은 문장, 없으면 템플릿."""
    if final:
        return final["variant"], final["text"]
    if ready_text:
        return POLISHED, ready_text
    return TEMPLATE, template


def fix_variant(requested: str, mode_on: bool, ready_text: str | None) -> str:
    """고정할 변형. 모드가 꺼졌거나 다듬은 문장이 없으면 다듬기 요청도 템플릿으로 고정한다(2-2의 5번)."""
    return POLISHED if requested == POLISHED and mode_on and ready_text else TEMPLATE
