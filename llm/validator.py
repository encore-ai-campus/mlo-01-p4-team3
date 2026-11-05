"""도메인 규칙: 추궁 답변 검증기와 폴백 문장(E-07-07, 기획서 7.2 4~5단계).

LLM은 엔진이 완성한 `fact_sentence`에 말투만 입힌다. 이 모듈은 생성문이 그 약속을 지켰는지 본다.

위반 사유 코드 4종(검사 순서이자 대표 사유 우선순위):

- `format_length`: 비었거나 너무 길다(전체 글자 수, 사실 문장보다 늘어난 한글 수), 문장이 너무 많다, 따옴표·괄호·줄바꿈·마크다운·화자 표시(`이름:`)·
  로마자·한자·사실 문장에 없는 숫자가 들어 있다.
- `forbidden_entity`: 허용 목록 밖의 인물, 사실 문장에 없는 장소, 역할어(창귀·씐 자 등)나 사건 단정 동사
  (죽였·홀렸 등)를 말했다. 어휘에 없는 새 이름은 찾지 못한다(덧붙임 길이 상한으로만 제한).
- `added_assertion`: 사실 문장에 없던 부정("가지 않았소")·단정("틀림없이 무고하오") 표현을 덧붙였다.
- `fact_changed`: 사실 문장의 장소·시간·인물을 빼거나 바꿨다(다른 시간대를 말함, 부정을 지움 포함). 그 밖의 어절
  (행동·대상)도 같은 뜻 표현을 대표 이름으로 맞춘 뒤 그대로 남아 있어야 한다.

인물·장소·시간은 `Lexicon`(콘텐츠 묶음에서 만든 어휘)으로 찾는다. 어휘는 호출하는 쪽이 넘긴다
(평가 세트 JSON의 `lexicon`, 서비스는 콘텐츠 묶음). 표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FORMAT_LENGTH = "format_length"
FORBIDDEN_ENTITY = "forbidden_entity"
ADDED_ASSERTION = "added_assertion"
FACT_CHANGED = "fact_changed"
REASONS = (FORMAT_LENGTH, FORBIDDEN_ENTITY, ADDED_ASSERTION, FACT_CHANGED)

MAX_CHARS = 120          # 생성문 최대 글자 수(공백 포함). 폴백 문장은 60자 안팎
MAX_SENTENCES = 4        # 문장 끝(. ? !) 개수 상한
MAX_ADDED_HANGUL = 25    # 사실 문장보다 늘어난 한글 글자 수 상한. 11/5 시험에서 통과한 답의 덧붙임은 최대 11자.
                         # 어휘 목록으로 못 잡는 새 주장·새 인물을 길이로 제한한다(짧은 지어낸 말은 못 막는다)
ADDRESSEE_WORDS = ("무당",)   # 플레이어 호칭. 인물 목록과 무관하게 늘 허용

# 장소 동의어(콘텐츠 이름 → 같은 장소로 보는 표현). 콘텐츠 묶음에 없는 장소 이름은 무시된다.
PLACE_SYNONYMS = {
    "우물가": ("우물",),
    "서낭당": ("성황당",),
    "주막": ("주막집",),
    "마을 어귀": ("어귀", "동구 밖", "동구"),
    "집": ("제 집", "우리 집", "내 집"),
}
# 콘텐츠 장소가 아니지만 새로 말하면 사실을 더하는 장소. 사실 문장에 있으면 그 요청에서는 허용된다.
EXTRA_PLACES = ("관아", "장터", "나루터", "나루", "강가", "냇가", "개울", "뒷산", "산길", "고갯길", "대장간",
                "방앗간", "곳간", "절간", "골짜기", "뒷방")
# 역할어: 게임의 정체·판정을 드러내는 말. 생성문에 새로 나오면 거부한다.
ROLE_WORDS = ("창귀", "씐 자", "씐자", "씌었", "씐 놈", "호랑이", "범인", "점괘", "살인",
              # 사건을 단정하는 동사(새 주장). 사실 문장에 있으면 그 요청에서는 허용된다
              "죽였", "죽인", "죽이", "홀렸", "홀린", "해쳤", "해친", "훔쳤", "훔친", "끌고 갔", "데려갔")
# 시간 표현: 대표 이름 → 같은 시간으로 보는 표현. 사실 문장의 시간은 보존해야 하고, 다른 시간을 말하면 바꾼 것이다.
TIME_GROUPS = {
    "한밤": ("한밤", "한밤중", "야밤", "깊은 밤", "자정"),
    "초저녁": ("초저녁", "해 질 녘", "해질녘", "땅거미"),
    "새벽": ("새벽",),
    "아침": ("아침",),
    "낮": ("대낮", "한낮", "낮"),
    "어제": ("어제", "어젯밤", "간밤"),
    "오늘": ("오늘",),
    "1일째": ("1일째", "첫날", "첫째 날"),
    "2일째": ("2일째", "이튿날", "둘째 날"),
    "3일째": ("3일째", "사흗날", "셋째 날"),
}
# 사람 이름 별칭과 겹치는 감탄사. 인물을 찾기 전에 지운다(예: 별칭 "아이" ↔ "아이고").
INTERJECTIONS = ("아이고", "아이구", "아이참", "아이쿠")

# 부정·단정 표현. 사실 문장보다 많이 나오면 덧붙인 것(added_assertion), 적게 나오면 지운 것(fact_changed).
_NEGATION = (
    r"않(?:았|는|고|소|네|습|다|으|을|지)",
    r"(?<![가-힣])안\s*(?:갔|했|봤|왔|나갔|있었|만났|보았)",
    r"(?<![가-힣])못\s*(?:봤|보았|했|갔|만났|봐)",
    r"(?<!수\s)(?<!수)없(?:었|소|네|다|습|어|지|음)",
    r"아니(?:오|요|었|라|다|네|지|야|올|외|니|에요)|아닙|아닐|아냐|아녀",
)
_ASSERTION = (
    r"틀림없", r"분명", r"결코", r"절대", r"맹세", r"확실", r"무고", r"결백", r"죄가\s*없", r"참말", r"정말로",
)
_NEGATION_RE = [re.compile(p) for p in _NEGATION]
_ASSERTION_RE = [re.compile(p) for p in _ASSERTION]
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_QUOTES = "\"'“”‘’「」『』«»"
_BAD_CHARS_RE = re.compile(r"[\"'“”‘’「」『』«»()\[\]{}<>*#`|\\/_=~^@$%&+]")
_LABEL_RE = re.compile(r"^\s*[가-힣 ]{1,8}\s*:")
_LATIN_RE = re.compile(r"[A-Za-z]")
_HANJA_KANA_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
_DIGITS_RE = re.compile(r"\d+")
_SENT_END_RE = re.compile(r"[.?!]+")
_HANGUL = re.compile(r"[가-힣]")


@dataclass(frozen=True)
class Lexicon:
    """검증에 쓰는 어휘. 각 dict는 대표 이름 → 같은 것으로 보는 표현들."""
    places: dict[str, tuple[str, ...]]
    names: dict[str, tuple[str, ...]]
    times: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(TIME_GROUPS))
    roles: tuple[str, ...] = ROLE_WORDS

    def to_json(self) -> dict:
        """평가 세트 JSON에 넣을 형태(목록은 정렬)."""
        return {"places": {k: list(v) for k, v in sorted(self.places.items())},
                "names": {k: list(v) for k, v in sorted(self.names.items())},
                "times": {k: list(v) for k, v in sorted(self.times.items())},
                "roles": list(self.roles)}

    @classmethod
    def from_json(cls, data: dict) -> "Lexicon":
        """`to_json`의 역."""
        return cls(places={k: tuple(v) for k, v in data["places"].items()},
                   names={k: tuple(v) for k, v in data["names"].items()},
                   times={k: tuple(v) for k, v in data["times"].items()},
                   roles=tuple(data["roles"]))


def lexicon_from_bundle(bundle: dict) -> Lexicon:
    """콘텐츠 묶음(`fixtures/content_min/bundle.json` 형식의 dict)에서 어휘를 만든다.

    장소 = 묶음의 `name_ko` + `PLACE_SYNONYMS` + `EXTRA_PLACES`, 인물 = 인물 `name_ko` + `aliases`.
    """
    places: dict[str, tuple[str, ...]] = {}
    for p in bundle["places"]:
        name = p["name_ko"]
        places[name] = tuple(dict.fromkeys((name, *PLACE_SYNONYMS.get(name, ()))))
    for extra in EXTRA_PLACES:
        places.setdefault(extra, (extra,))
    names = {c["name_ko"]: tuple(dict.fromkeys((c["name_ko"], *c.get("aliases", ())))) for c in bundle["characters"]}
    return Lexicon(places=places, names=names)


@dataclass(frozen=True)
class Mention:
    """본문에서 찾은 어휘 하나. kind는 place|name|time|role, key는 대표 이름."""
    kind: str
    key: str
    surface: str
    start: int


def find_mentions(text: str, lexicon: Lexicon) -> list[Mention]:
    """본문에서 장소·인물·시간·역할어를 찾는다.

    긴 표현부터 맞추고 이미 잡힌 글자는 다시 쓰지 않는다("대장간 최씨"는 인물, 그 안의 "대장간"은 장소로
    세지 않음). 표현 앞 글자가 한글이면 단어 중간으로 보고 건너뛴다("주막집"의 "집"). 숫자로 시작하는
    표현("1일째")은 앞 글자가 숫자일 때 건너뛴다. 감탄사(`INTERJECTIONS`)는 먼저 지운다.
    """
    for word in INTERJECTIONS:
        text = text.replace(word, " " * len(word))
    terms: list[tuple[str, str, str]] = []
    for kind, groups in (("place", lexicon.places), ("name", lexicon.names), ("time", lexicon.times)):
        for key, surfaces in groups.items():
            terms.extend((s, kind, key) for s in surfaces)
    terms.extend((r, "role", r) for r in lexicon.roles)
    terms.sort(key=lambda t: (-len(t[0]), t[0], t[1], t[2]))
    taken = [False] * len(text)
    found: list[Mention] = []
    for surface, kind, key in terms:
        start = text.find(surface)
        while start != -1:
            end = start + len(surface)
            prev = text[start - 1] if start else ""
            boundary_ok = not (_HANGUL.match(prev) if prev else False) and not (surface[0].isdigit() and prev.isdigit())
            if boundary_ok and not any(taken[start:end]):
                for i in range(start, end):
                    taken[i] = True
                found.append(Mention(kind, key, surface, start))
            start = text.find(surface, start + 1)
    found.sort(key=lambda m: m.start)
    return found


@dataclass(frozen=True)
class FactElements:
    """사실 문장의 보존 대상: 장소·시간·인물의 대표 이름."""
    places: frozenset[str]
    times: frozenset[str]
    names: frozenset[str]


def fact_elements(fact_sentence: str, lexicon: Lexicon) -> FactElements:
    """사실 문장에서 장소·시간·인물을 뽑는다."""
    ms = find_mentions(fact_sentence, lexicon)
    pick = lambda kind: frozenset(m.key for m in ms if m.kind == kind)   # noqa: E731
    return FactElements(pick("place"), pick("time"), pick("name"))


def normalize_output(raw: str) -> str:
    """생성 원문을 검증 대상 문장으로 다듬는다: 사고 블록 제거, 앞뒤 공백 제거, 전체를 감싼 따옴표 한 겹 제거.

    그 밖의 형식 문제(중간 따옴표, 화자 표시 등)는 고치지 않고 검증기가 거부한다.
    """
    text = _THINK_RE.sub("", raw or "").strip()
    if len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
        inner = text[1:-1].strip()
        if not any(q in inner for q in _QUOTES):
            text = inner
    return text


_WORD_STRIP = " .,?!…~·\"'“”‘’"


def canonicalize(text: str, lexicon: Lexicon) -> str:
    """찾은 어휘를 대표 이름으로 바꾼 문장("이튿날"→"2일째", "한밤중"→"한밤", "박 영감"→"약초꾼")."""
    out, last = [], 0
    for m in find_mentions(text, lexicon):
        out += [text[last:m.start], m.key]
        last = m.start + len(m.surface)
    return "".join(out) + text[last:]


def missing_fact_words(text: str, fact_sentence: str, lexicon: Lexicon) -> list[str]:
    """사실 문장의 어절(띄어쓰기 단위, 앞뒤 문장부호 제외) 중 생성문에 없는 것. 둘 다 `canonicalize`한 뒤 비교한다.

    장소·시간·인물 밖의 내용(행동·대상, 예: "팔았소"→"샀소")이 바뀌거나 빠진 것을 잡는다. 어미를 바꾼 것도 바꾼 것으로 본다
    (프롬프트가 사실 문장을 그대로 넣으라고 요구한다).
    """
    out = canonicalize(text, lexicon)
    words = [w.strip(_WORD_STRIP) for w in canonicalize(fact_sentence, lexicon).split()]
    return [w for w in words if w and w not in out]


def _count(patterns: list[re.Pattern], text: str) -> int:
    return sum(len(p.findall(text)) for p in patterns)


@dataclass(frozen=True)
class ValidationResult:
    """검증 결과. reasons는 걸린 사유 코드를 `REASONS` 순서로, details는 사람이 읽을 설명."""
    ok: bool
    reasons: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    @property
    def reason(self) -> str | None:
        """대표 사유(첫 사유). 통과면 None."""
        return self.reasons[0] if self.reasons else None


def check_format(text: str, fact_sentence: str) -> list[str]:
    """형식·길이 위반 설명 목록(비면 통과)."""
    out = []
    if not text.strip():
        return ["빈 문장"]
    if len(text) > MAX_CHARS:
        out.append(f"{len(text)}자 > {MAX_CHARS}자")
    if len(_SENT_END_RE.findall(text)) > MAX_SENTENCES:
        out.append(f"문장 {len(_SENT_END_RE.findall(text))}개 > {MAX_SENTENCES}개")
    added = len(_HANGUL.findall(text)) - len(_HANGUL.findall(fact_sentence))
    if added > MAX_ADDED_HANGUL:
        out.append(f"덧붙인 한글 {added}자 > {MAX_ADDED_HANGUL}자")
    if "\n" in text or "\r" in text:
        out.append("줄바꿈")
    bad = sorted(set(_BAD_CHARS_RE.findall(text)) - set(_BAD_CHARS_RE.findall(fact_sentence)))
    if bad:
        out.append("허용되지 않은 기호 " + "".join(bad))
    if _LABEL_RE.match(text) and not _LABEL_RE.match(fact_sentence):
        out.append("화자 표시")
    if _LATIN_RE.search(text):
        out.append("로마자")
    if _HANJA_KANA_RE.search(text):
        out.append("한자·가나")
    extra_digits = sorted(set(_DIGITS_RE.findall(text)) - set(_DIGITS_RE.findall(fact_sentence)))
    if extra_digits:
        out.append("사실 문장에 없는 숫자 " + ",".join(extra_digits))
    if not _HANGUL.search(text):
        out.append("한글 없음")
    return out


def validate(text: str, req: dict, lexicon: Lexicon, allowed_places: list[str] | None = None) -> ValidationResult:
    """생성문 text(`normalize_output`을 거친 것)를 추궁 요청 req(`RenderRequest` 필드 dict)에 대해 검증한다.

    allowed_places가 None이면 사실 문장에 나온 장소만 허용한다. 허용 인물은 `req["allowed_names"]`
    (별칭 포함)와 `ADDRESSEE_WORDS`다.
    """
    fact = req["fact_sentence"]
    details: list[str] = []
    reasons: list[str] = []

    fmt = check_format(text, fact)
    if fmt:
        reasons.append(FORMAT_LENGTH)
        details.extend(fmt)

    facts = fact_elements(fact, lexicon)
    places_ok = set(allowed_places) if allowed_places is not None else set(facts.places)
    places_ok |= facts.places
    names_ok = set(req.get("allowed_names", ())) | set(facts.names)
    fact_mentions = find_mentions(fact, lexicon)
    fact_roles = {m.key for m in fact_mentions if m.kind == "role"}
    mentions = find_mentions(text, lexicon)
    forbidden = []
    for m in mentions:
        if m.kind == "place" and m.key not in places_ok:
            forbidden.append(f"장소 {m.surface}")
        elif m.kind == "name" and m.key not in names_ok:
            forbidden.append(f"인물 {m.surface}")
        elif m.kind == "role" and m.key not in fact_roles:
            forbidden.append(f"역할어 {m.surface}")
    if forbidden:
        reasons.append(FORBIDDEN_ENTITY)
        details.extend(forbidden)

    neg_out, neg_fact = _count(_NEGATION_RE, text), _count(_NEGATION_RE, fact)
    ast_out, ast_fact = _count(_ASSERTION_RE, text), _count(_ASSERTION_RE, fact)
    if neg_out > neg_fact or ast_out > ast_fact:
        reasons.append(ADDED_ASSERTION)
        details.append(f"부정 {neg_fact}→{neg_out}, 단정 {ast_fact}→{ast_out}")

    seen = {(m.kind, m.key) for m in mentions}
    changed = [f"{label} {key} 빠짐" for label, kind, keys in
               (("장소", "place", facts.places), ("시간", "time", facts.times), ("인물", "name", facts.names))
               for key in sorted(keys) if (kind, key) not in seen]
    changed += [f"시간 {m.surface} 추가" for m in mentions if m.kind == "time" and m.key not in facts.times]
    if neg_out < neg_fact:
        changed.append(f"부정 표현 빠짐 {neg_fact}→{neg_out}")
    missing = missing_fact_words(text, fact, lexicon)
    if missing:
        changed.append("사실 문장 어절 빠짐·바뀜 " + ",".join(missing))
    if changed:
        reasons.append(FACT_CHANGED)
        details.extend(changed)

    return ValidationResult(not reasons, tuple(reasons), tuple(details))


def fallback_text(req: dict) -> str:
    """폴백 문장. 계약상 `fallback_text`는 `fact_sentence`를 그대로 포함한다. 어긋난 요청이면 사실 문장만 쓴다
    (폴백 동등성: 플레이어가 얻는 정보는 생성 성공 여부와 같다)."""
    fb = req.get("fallback_text") or ""
    return fb if req["fact_sentence"] in fb else req["fact_sentence"]
