"""도메인 규칙: 자유 질문 답변의 프롬프트·출력 파싱·검사·폴백(E-08-13, E-11-12 (가)).

LLM은 그 NPC에 허용된 사실 목록 중 무엇을 말할지 고르고 말투를 입힌다. 거짓말을 지어내지 않는다.
출력 형식은 첫 줄 `근거: f1,f3`(없으면 `근거: 없음`), 둘째 줄부터 답 1~2문장이다(`AnswerStream`이 스트리밍
조각을 줄·문장 단위로 끊는다).

검사 순서와 사유 코드(`REASONS` 순서가 대표 사유 우선순위):

1. `format_length`: 근거 줄 형식, 답 문장 수(1~2), 글자 수, 로마자·한자·마크다운 기호·화자 표시, 인용 사실에 없는 숫자.
2. `evidence_range`: 인용 ID가 허용 사실 목록 밖.
3. `pair_binding`: 절마다 (인물, 장소)·(인물, 시간)·(장소, 시간) 쌍이 인용한 사실의 한 절 안에 함께 있어야 한다.
4. `polarity`: 쌍마다 부정 유무가 그 쌍이 있는 인용 사실 절과 같아야 한다. 판별 불가(부정 2개 이상, 전언과 부정이 함께)도 거부.
5. `leak`: 역할어(창귀 등), 항목의 비공개 사실(`forbidden_facts`)에만 있는 쌍·내용어.
6. `ungrounded`(쌍이 없는 주장 대비): 근거가 있으면 문장마다 인용 사실의 인물·장소·시간을 하나 이상 담고, 인용 사실 밖의
   인물·장소·시간(질문에 나온 인물은 허용)을 말하지 않는다. 사건 단정 어휘(`EVENT_WORDS`)는 인용 사실에 없으면 거부, 인용
   사실·질문·말투 목록에 없는 명사 후보가 문장당 `MAX_UNKNOWN_NOUNS`개를 넘으면 거부. 근거가 없으면 인물·장소·시간 언급 0.
   예외(E-11-12): 자기소개 사실(ID가 `s`로 시작)을 인용하고 문장에 말하는 NPC 본인 말고 인물·장소·시간이 없으면
   "인용 사실의 인물·장소·시간을 하나 이상" 조건을 면한다. 다른 인물·장소·시간을 말하면 그대로 거부한다.

판별 방법(형태소 분석이 아니라 어휘 목록과 띄어쓰기·조사 규칙이다. 한계는 docs/modules/llm.md "한계"):

- 문장 경계: `.`·`?`·`!` 뒤(`split_sentences`). 말줄임표 `…`는 경계로 보지 않는다.
- 절 경계: 쉼표, 그리고 어절 끝 연결 어미 `고`·`며`·`는데`·`지만`·`다가`·`면서` 뒤(`split_clauses`). 인물이 없는 절은 앞 절의
  인물을 이어받고, 첫 절에 인물이 없으면 말하는 NPC(1인칭 표지가 있으면 함께 더함)로 본다.
- 사실 문장은 NPC 본인 시점이라 모든 절에 말하는 NPC를 인물로 더한다("주막에서 을을 봤소" → 본인·을이 주막에).
- 부정: 기존 검증기 부정 표현(`llm.validator`)을 절 안에서 센다. 0개 = 긍정, 1개 = 부정, 2개 이상이거나 전언 표지
  (`다는`·`라는`·`다고` 등)와 함께 나오면 판별 불가로 거부한다("갑이 우물에 없었다는 말은 사실이 아니다" → 거부).
- 명사 후보: 어절 끝 조사(`JOSA`)를 떼고 남은 2글자 이상 어간. 동사 어간처럼 끝나는 것(`_VERBISH_END`)은 뺀다. 조사 없이
  쓴 명사는 세지 못한다.

인물·장소·시간 어휘는 `llm.validator.Lexicon`·`find_mentions`를 쓴다. 표준 라이브러리와 `llm`만 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product

from llm.prompt import speaker_and_style, style_profile
from llm.validator import (_BAD_CHARS_RE, _DIGITS_RE, _HANJA_KANA_RE, _LABEL_RE, _LATIN_RE, _NEGATION, MAX_CHARS,
                           Lexicon, ValidationResult, fact_elements, find_mentions)

PROMPT_VERSION = "free-answer-v2"   # v2(E-11-12 자유 대화 확장): 자기소개 사실(`s` ID)·인사·잡담 응대 안내
SELF_FACT_PREFIX = "s"              # 자기소개 사실 ID 접두어(s1 이름, s2 성향). 사건 사실 ID는 f로 시작한다
FORMAT_LENGTH = "format_length"
EVIDENCE_RANGE = "evidence_range"
PAIR_BINDING = "pair_binding"
POLARITY = "polarity"
LEAK = "leak"
UNGROUNDED = "ungrounded"
REASONS = (FORMAT_LENGTH, EVIDENCE_RANGE, PAIR_BINDING, POLARITY, LEAK, UNGROUNDED)

MAX_SENTENCES = 2            # 답 문장 수 상한(E-08-13 1번)
MAX_ANSWER_CHARS = MAX_CHARS  # 답 전체 글자 수 상한(기존 검증기와 같은 120자)
MAX_HEADER_CHARS = 80        # 근거 줄이 이보다 길도록 줄바꿈이 없으면 형식 위반
MAX_UNKNOWN_NOUNS = 2        # 문장당 모르는 명사 후보 수 상한(넘으면 ungrounded)
NO_FACT_TEXT = "잘 모르겠소."   # 허용 사실이 하나도 없을 때의 폴백
EVIDENCE_NONE = "없음"

# 사건 단정 어휘: 인용 사실에 없으면 거부. 앞 글자가 한글이면 단어 중간으로 보고 건너뛴다("소독"의 "독"은 아님).
# 기존 검증기 역할어(ROLE_WORDS: 창귀·죽였 등)는 누설로 따로 본다.
EVENT_WORDS = ("독살", "독약", "독을", "독이", "독에", "죽었", "죽은", "죽어", "죽음", "죽게", "목숨", "시체", "송장",
               "피투성이", "핏자국", "피를", "칼로", "칼을", "흉기", "살해", "범행", "해코지", "피해자", "사라졌", "사라진",
               "실종", "잡아먹", "물어갔", "물려 갔", "습격", "변고")
FIRST_PERSON = ("나는", "내가", "저는", "제가", "난", "전", "나도", "저도", "소인", "쇤네", "소승", "이 몸")
REPORTED = re.compile(r"(?:다는|라는|다던|라던|다고|라고|단|란)(?![가-힣])")
_CONNECTIVE = re.compile(r"(?<=[가-힣])(?:고|며|는데|은데|지만|다가|면서)(?=\s)|,|，")
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")
_SENT_END = re.compile(r"[.?!]")
_NEG_RE = [re.compile(p) for p in _NEGATION]
_HEADER_RE = re.compile(r"^\s*근거\s*[:：]\s*(.*?)\s*$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
# 명사 후보를 가르는 조사(긴 것부터). 여러 개가 겹치면(식구들한테만은) 반복해서 뗀다.
JOSA = tuple(sorted(("은", "는", "이", "가", "을", "를", "에", "에서", "에게", "한테", "께", "께서", "와", "과", "도", "의",
                     "로", "으로", "랑", "이랑", "만", "까지", "부터", "보다", "에선", "엔", "들"), key=len, reverse=True))
_VERBISH_END = ("었", "았", "였", "겠", "했", "하", "되", "있", "없", "시", "던", "지", "고", "게", "니", "다", "소", "오",
                "요", "네", "까", "면", "서", "어", "아", "여")
# 명사 후보에서 빼는 대명사·의존 명사 등(어느 문장에나 나올 수 있어 새 주장으로 보지 않음)
STOP_NOUNS = frozenset(("그날", "그때", "그것", "그건", "그거", "이것", "저것", "거기", "여기", "저기", "누구", "아무",
                        "무엇", "사람", "무당", "나리", "양반", "자네", "당신", "우리", "혼자", "내내", "시각", "말씀",
                        "이야기", "생각", "까닭", "이유", "사실", "기억", "정말", "참말", "그쪽", "이쪽", "제가",
                        "그분", "모두", "다들", "아무도", "아무것"))


@dataclass(frozen=True)
class Fact:
    """허용 사실 하나: 엔진 투영의 `fact_id`와 NPC 본인 시점의 사실 문장."""
    fact_id: str
    sentence: str


@dataclass(frozen=True)
class Turn:
    """직전 대화 한 턴(고정 질문·답 쌍). 사실 근거가 아니다."""
    question: str
    answer: str


@dataclass(frozen=True)
class FreeQuestion:
    """자유 질문 1건의 입력: 말하는 NPC, 페르소나, 허용 사실, 직전 대화, 질문, 비공개 사실."""
    npc_name: str
    persona: str
    facts: tuple[Fact, ...]
    question: str
    history: tuple[Turn, ...] = ()
    forbidden_facts: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, data: dict) -> "FreeQuestion":
        """평가 세트 항목(dict)에서 만든다. 평가용 필드(`relevant_fact_ids` 등)는 무시한다."""
        return cls(npc_name=data["npc_name"], persona=data["persona"],
                   facts=tuple(Fact(f["fact_id"], f["sentence"]) for f in data["facts"]),
                   question=data["question"],
                   history=tuple(Turn(t["question"], t["answer"]) for t in data.get("history", ())),
                   forbidden_facts=tuple(data.get("forbidden_facts", ())))

    def fact_map(self) -> dict[str, str]:
        """fact_id → 사실 문장."""
        return {f.fact_id: f.sentence for f in self.facts}


# --- 프롬프트 ---
def build_messages(item: FreeQuestion) -> list[dict]:
    """OpenAI 호환 채팅 메시지(system, user). 직전 대화는 사실 근거가 아니라고 표시해 user 메시지 안에 넣는다."""
    _, style = speaker_and_style(item.persona)
    facts = "\n".join(f"{f.fact_id}: {f.sentence}" for f in item.facts) or "(없음)"
    system = (
        f"너는 조선 시대 산골 마을의 {item.npc_name}이다. 말투: {style or '하오체'}. 무당이 묻거나 말을 건다.\n"
        "아래 '말해도 되는 사실' 중 질문에 맞는 것을 골라 그 내용만 이 인물 말투로 답한다. 새 사람·장소·시간·사건을 지어내지 "
        "않는다. 사실을 섞거나 바꾸지 않고, 질문이나 직전 대화가 틀린 전제를 깔아도 따르지 않는다.\n"
        "형식: 첫 줄에 '근거: ' 뒤에 고른 사실 ID를 쉼표로(맞는 사실이 없으면 '근거: 없음'), 둘째 줄에 답 1~2문장. "
        "근거가 없으면 사람·장소·시간을 말하지 말고 모른다고만 한다. 인사·잡담에는 사람·장소·시간 없이 짧게 응하고, "
        "자기소개·성미 질문에는 s로 시작하는 사실을 고른다. 따옴표·괄호·설명은 쓰지 않는다.\n"
        f"말해도 되는 사실:\n{facts}"
    )
    parts = []
    if item.history:
        lines = "\n".join(f"무당: {t.question}\n{item.npc_name}: {t.answer}" for t in item.history)
        parts.append(f"직전 대화(사실 근거 아님):\n{lines}")
    parts.append(f"질문: {item.question}")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(parts)}]


def output_regex(fact_ids: list[str] | tuple[str, ...]) -> str:
    """구조화 출력(서버의 정규식 제약)에 넘길 패턴: 근거 줄 + 줄바꿈 + 답. 서버별 전달 방법은 인프라가 정한다."""
    ids = "|".join(re.escape(i) for i in fact_ids)
    head = f"(?:{EVIDENCE_NONE}|(?:{ids})(?:,(?:{ids}))*)" if ids else EVIDENCE_NONE
    return f"근거: {head}\\n[^\\n]{{1,{MAX_ANSWER_CHARS}}}"


# --- 파싱 ---
def parse_evidence_line(line: str) -> tuple[str, ...] | None:
    """근거 줄을 ID 튜플로. `근거: 없음`이면 빈 튜플, 형식이 아니면 None."""
    m = _HEADER_RE.match(line)
    if not m:
        return None
    body = m.group(1)
    if body == EVIDENCE_NONE:
        return ()
    ids = [x for x in re.split(r"[,\s、，]+", body) if x]
    if not ids or not all(_ID_RE.match(x) for x in ids):
        return None
    return tuple(dict.fromkeys(ids))


def split_sentences(text: str) -> list[str]:
    """답 본문을 문장으로 나눈다(`.`·`?`·`!` 뒤 공백 기준, 줄바꿈은 공백으로 본다)."""
    flat = " ".join(text.split())
    return [s for s in (x.strip() for x in _SENT_SPLIT.split(flat)) if s]


@dataclass
class AnswerStream:
    """스트리밍 조각을 받아 근거 줄과 완성된 문장을 차례로 내놓는다(순수 상태 기계, 시간은 바깥에서 잰다).

    `feed`/`close`는 사건 목록을 돌려준다: `("evidence", ids)`, `("bad_header", 원문)`, `("sentence", 문장)`.
    근거 줄이 `MAX_HEADER_CHARS`자를 넘도록 줄바꿈이 없으면 `bad_header`. 문장은 끝 부호 뒤에 공백이나 줄바꿈이
    와야 완성으로 보고, 마지막 문장은 `close`에서 내놓는다.
    """
    _buf: str = ""
    header_done: bool = False
    failed: bool = False
    _events: list = field(default_factory=list)

    def feed(self, chunk: str) -> list[tuple]:
        """조각 하나를 넣고 새로 생긴 사건을 돌려준다."""
        if self.failed:
            return []
        self._buf += chunk
        out = []
        if not self.header_done:
            if "\n" not in self._buf:
                if len(self._buf) > MAX_HEADER_CHARS:
                    self.failed = True
                    out.append(("bad_header", self._buf))
                return out
            line, self._buf = self._buf.split("\n", 1)
            ids = parse_evidence_line(line)
            if ids is None:
                self.failed = True
                return out + [("bad_header", line)]
            self.header_done = True
            out.append(("evidence", ids))
        while True:
            flat = self._buf.replace("\n", " ")
            m = re.search(r"[.?!]+(?=\s)", flat)
            if not m:
                break
            sent, self._buf = flat[:m.end()].strip(), flat[m.end():].lstrip()
            if sent:
                out.append(("sentence", " ".join(sent.split())))
        return out

    def close(self) -> list[tuple]:
        """스트림 끝: 남은 문장을 내놓는다. 근거 줄도 없이 끝나면 `bad_header`."""
        if self.failed:
            return []
        if not self.header_done:
            self.failed = True
            ids = parse_evidence_line(self._buf)
            return [("bad_header", self._buf)] if ids is None else [("evidence", ids)]
        rest = " ".join(self._buf.split())
        self._buf = ""
        return [("sentence", s) for s in split_sentences(rest)]


# --- 판별 도구 ---
def split_clauses(sentence: str) -> list[str]:
    """문장을 절로 나눈다(쉼표, 어절 끝 연결 어미 `고`·`며`·`는데`·`지만`·`다가`·`면서` 뒤)."""
    out, last = [], 0
    for m in _CONNECTIVE.finditer(sentence):
        out.append(sentence[last:m.end()])
        last = m.end()
    out.append(sentence[last:])
    return [c.strip(" ,，") for c in out if c.strip(" ,，")]


def _has_first_person(text: str) -> bool:
    return any(re.search(rf"(?<![가-힣]){re.escape(w)}(?![가-힣])", text) for w in FIRST_PERSON)


def polarity(clause: str) -> str:
    """절의 극성: `pos`(부정 0) / `neg`(부정 1) / `unknown`(부정 2개 이상, 또는 전언 표지와 부정이 함께)."""
    n = sum(len(p.findall(clause)) for p in _NEG_RE)
    if n == 0:
        return "pos"
    if n >= 2 or REPORTED.search(clause):
        return "unknown"
    return "neg"


Elem = tuple[str, str]   # (kind, key): kind는 name|place|time


@dataclass(frozen=True)
class Clause:
    """절 하나의 판별 결과: 인물·장소·시간(대표 이름), 쌍, 극성."""
    text: str
    names: frozenset[str]
    places: frozenset[str]
    times: frozenset[str]
    polarity: str

    def pairs(self) -> set[tuple[Elem, Elem]]:
        """(인물, 장소)·(인물, 시간)·(장소, 시간) 쌍. 쌍 안의 순서는 정렬로 고정한다."""
        ns = [("name", n) for n in self.names]
        ps = [("place", p) for p in self.places]
        ts = [("time", t) for t in self.times]
        out = set()
        for a, b in (*product(ns, ps), *product(ns, ts), *product(ps, ts)):
            out.add(tuple(sorted((a, b))))
        return out


def analyze(sentence: str, lexicon: Lexicon, speaker: str, is_fact: bool) -> list[Clause]:
    """문장을 절 단위로 판별한다. 사실 문장(`is_fact`)은 모든 절에 말하는 NPC를 더한다."""
    clauses, carried = [], frozenset()
    for text in split_clauses(sentence):
        ms = find_mentions(text, lexicon)
        pick = lambda k: frozenset(m.key for m in ms if m.kind == k)   # noqa: E731
        names = pick("name")
        if is_fact:
            names = names | {speaker}
        elif not names:
            names = carried or frozenset({speaker})
        elif _has_first_person(text):
            names = names | {speaker}
        carried = names
        clauses.append(Clause(text, names, pick("place"), pick("time"), polarity(text)))
    return clauses


def _strip_josa(word: str) -> tuple[str, bool]:
    """어절에서 끝 조사를 반복해서 뗀다(떼고 남는 어간이 2글자 이상일 때만, "괭이를" → "괭이"). (어간, 조사를 뗐는지)."""
    stem, stripped = word, False
    while True:
        for j in JOSA:
            if len(stem) - len(j) >= 2 and stem.endswith(j):
                stem, stripped = stem[:-len(j)], True
                break
        else:
            return stem, stripped


def noun_candidates(text: str) -> list[str]:
    """명사 후보: 조사를 뗀 2글자 이상 한글 어간 중 동사처럼 끝나지 않는 것(등장 순서, 중복 포함)."""
    out = []
    for raw in text.split():
        word = re.sub(r"[^가-힣]", "", raw)
        stem, stripped = _strip_josa(word)
        if stripped and len(stem) >= 2 and not stem.endswith(_VERBISH_END) and stem not in STOP_NOUNS:
            out.append(stem)
    return out


def _bounded(word: str, text: str) -> bool:
    return any(not re.match(r"[가-힣]", text[m.start() - 1:m.start()]) for m in re.finditer(re.escape(word), text))


def style_words(persona: str) -> str:
    """페르소나 말투 목록(말투 설명 + 그 계열의 앞뒤 덧붙임 후보)을 한 문자열로. 명사 후보 대조에 쓴다."""
    _, style = speaker_and_style(persona)
    prefixes, _, tails = style_profile(style)
    return " ".join((style, *prefixes, *(t for ts in tails.values() for t in ts)))


# --- 검사 ---
@dataclass(frozen=True)
class Context:
    """한 답을 검사할 때 고정되는 값(근거 줄을 읽은 뒤 만든다)."""
    item: FreeQuestion
    lexicon: Lexicon
    evidence: tuple[str, ...]
    cited: tuple[str, ...]            # 인용 사실 문장(허용 목록 안의 것만)
    fact_pairs: dict                  # 쌍 → 그 쌍이 있는 인용 사실 절의 극성 집합
    elements: frozenset[Elem]         # 인용 사실의 인물·장소·시간(말하는 NPC 포함)
    question_names: frozenset[str]
    known_text: str                   # 인용 사실 + 질문 + 말투 목록(명사 후보 대조)
    forbidden_pairs: frozenset
    forbidden_nouns: frozenset[str]
    self_cited: bool = False          # 자기소개 사실(`SELF_FACT_PREFIX`로 시작하는 ID)을 인용했는지


def make_context(item: FreeQuestion, evidence: tuple[str, ...], lexicon: Lexicon) -> Context:
    """근거 ID로 검사 문맥을 만든다. 허용 목록 밖 ID는 `evidence_range`에서 거부되고 여기서는 빠진다."""
    fmap = item.fact_map()
    cited = tuple(fmap[i] for i in evidence if i in fmap)
    fact_pairs: dict = {}
    elements: set[Elem] = set()
    for s in cited:
        for c in analyze(s, lexicon, item.npc_name, is_fact=True):
            for p in c.pairs():
                fact_pairs.setdefault(p, set()).add(c.polarity)
            elements |= {("name", n) for n in c.names} | {("place", p) for p in c.places} | \
                        {("time", t) for t in c.times}
    q_names = frozenset(m.key for m in find_mentions(item.question, lexicon) if m.kind == "name")
    known = " ".join((*cited, item.question, style_words(item.persona)))
    f_pairs, f_nouns = set(), set()
    for s in item.forbidden_facts:
        for c in analyze(s, lexicon, item.npc_name, is_fact=True):
            f_pairs |= c.pairs()
        f_nouns |= {n for n in noun_candidates(s) if n not in known and not find_mentions(n, lexicon)}
    self_cited = any(i.startswith(SELF_FACT_PREFIX) for i in evidence if i in fmap)
    return Context(item, lexicon, evidence, cited, fact_pairs, frozenset(elements), q_names, known,
                   frozenset(f_pairs - set(fact_pairs)), frozenset(f_nouns), self_cited)


def check_header(evidence: tuple[str, ...] | None, item: FreeQuestion) -> list[tuple[str, str]]:
    """근거 줄 검사: (사유 코드, 설명) 목록. 비면 통과."""
    if evidence is None:
        return [(FORMAT_LENGTH, "근거 줄 형식 아님")]
    bad = [i for i in evidence if i not in item.fact_map()]
    return [(EVIDENCE_RANGE, "허용 목록 밖 근거 " + ",".join(bad))] if bad else []


def _format_issues(sentence: str, ctx: Context) -> list[str]:
    out = []
    if len(sentence) > MAX_ANSWER_CHARS:
        out.append(f"{len(sentence)}자 > {MAX_ANSWER_CHARS}자")
    if _BAD_CHARS_RE.search(sentence):
        out.append("허용되지 않은 기호 " + "".join(sorted(set(_BAD_CHARS_RE.findall(sentence)))))
    if _LABEL_RE.match(sentence):
        out.append("화자 표시")
    if _LATIN_RE.search(sentence):
        out.append("로마자")
    if _HANJA_KANA_RE.search(sentence):
        out.append("한자·가나")
    allowed_digits = set(_DIGITS_RE.findall(" ".join(ctx.cited)))
    extra = sorted(set(_DIGITS_RE.findall(sentence)) - allowed_digits)
    if extra:
        out.append("인용 사실에 없는 숫자 " + ",".join(extra))
    if not re.search(r"[가-힣]", sentence):
        out.append("한글 없음")
    return out


def check_sentence(sentence: str, ctx: Context) -> list[tuple[str, str]]:
    """답 문장 하나를 검사한다(근거 범위는 `check_header`가 본다). (사유 코드, 설명) 목록, 비면 통과."""
    issues: list[tuple[str, str]] = [(FORMAT_LENGTH, d) for d in _format_issues(sentence, ctx)]
    lex, speaker = ctx.lexicon, ctx.item.npc_name
    mentions = [m for m in find_mentions(sentence, lex) if m.kind in ("name", "place", "time")]
    roles = [m.surface for m in find_mentions(sentence, lex) if m.kind == "role"
             and not any(m.surface in c for c in ctx.cited)]
    if not ctx.evidence:
        if mentions:
            issues.append((UNGROUNDED, "근거 없음인데 인물·장소·시간 언급 " + ",".join(m.surface for m in mentions)))
    else:
        clauses = analyze(sentence, lex, speaker, is_fact=False)
        unbound, flipped = [], []
        for c in clauses:
            for p in sorted(c.pairs()):
                if p not in ctx.fact_pairs:
                    unbound.append("-".join(k for _, k in p))
                elif c.polarity not in ctx.fact_pairs[p]:
                    flipped.append("-".join(k for _, k in p) + f"({c.polarity})")
        if unbound:
            issues.append((PAIR_BINDING, "인용 사실 한 절에 없는 쌍 " + ",".join(unbound)))
        if flipped:
            issues.append((POLARITY, "부정 유무가 인용 사실과 다름 " + ",".join(flipped)))
        hits = [p for c in clauses for p in c.pairs() if p in ctx.forbidden_pairs]
        if hits:
            issues.append((LEAK, "비공개 사실의 쌍 " + ",".join("-".join(k for _, k in p) for p in sorted(hits))))
        outside = [m.surface for m in mentions if (m.kind, m.key) not in ctx.elements
                   and not (m.kind == "name" and (m.key in ctx.question_names or m.key == speaker))]
        if outside:
            issues.append((UNGROUNDED, "인용 사실 밖 인물·장소·시간 " + ",".join(outside)))
        # 자기소개 사실을 인용했고 문장에 말하는 NPC 말고는 인물·장소·시간이 없으면 본인 이야기로 본다("내 성미는 급하오")
        self_only = ctx.self_cited and all(m.kind == "name" and m.key == speaker for m in mentions)
        if not self_only and not any((m.kind, m.key) in ctx.elements for m in mentions):
            issues.append((UNGROUNDED, "인용 사실의 인물·장소·시간이 없음"))
    if roles:
        issues.append((LEAK, "역할어 " + ",".join(roles)))
    leaked = [n for n in ctx.forbidden_nouns if n in sentence]
    if leaked:
        issues.append((LEAK, "비공개 사실 내용어 " + ",".join(sorted(leaked))))
    events = [w for w in EVENT_WORDS if _bounded(w, sentence) and not any(w in c for c in ctx.cited)]
    if events:
        issues.append((UNGROUNDED, "사건 단정 어휘 " + ",".join(events)))
    unknown = [n for n in noun_candidates(sentence)
               if n not in ctx.known_text and not find_mentions(n, lex)]
    if len(unknown) > MAX_UNKNOWN_NOUNS:
        issues.append((UNGROUNDED, f"모르는 명사 {len(unknown)}개 > {MAX_UNKNOWN_NOUNS}개 " + ",".join(unknown)))
    return issues


def _result(issues: list[tuple[str, str]]) -> ValidationResult:
    codes = {c for c, _ in issues}
    return ValidationResult(ok=not issues, reasons=tuple(r for r in REASONS if r in codes),
                            details=tuple(f"{c}: {d}" for c, d in issues))


def check_answer(raw: str, item: FreeQuestion, lexicon: Lexicon) -> ValidationResult:
    """완성된 출력 전체(근거 줄 + 답)를 검사한다. 스트리밍 판정은 `SentenceGate`가 같은 규칙을 문장마다 쓴다."""
    head, _, body = (raw or "").strip().partition("\n")
    evidence = parse_evidence_line(head)
    issues = check_header(evidence, item)
    if evidence is None:
        return _result(issues)
    sentences = split_sentences(body)
    if not sentences:
        issues.append((FORMAT_LENGTH, "답 문장 없음"))
    if len(sentences) > MAX_SENTENCES:
        issues.append((FORMAT_LENGTH, f"문장 {len(sentences)}개 > {MAX_SENTENCES}개"))
    joined = " ".join(sentences)
    if len(joined) > MAX_ANSWER_CHARS:
        issues.append((FORMAT_LENGTH, f"답 {len(joined)}자 > {MAX_ANSWER_CHARS}자"))
    ctx = make_context(item, evidence, lexicon)
    for s in sentences:
        issues += check_sentence(s, ctx)
    return _result(issues)


# --- 폴백 ---
def fallback(item: FreeQuestion, lexicon: Lexicon) -> tuple[tuple[str, ...], str]:
    """(근거 ID, 답). 질문과 인물·장소·시간이 가장 많이 겹치는 허용 사실의 문장(동률이면 목록 순서). 사실이 없으면
    `NO_FACT_TEXT`."""
    if not item.facts:
        return (), NO_FACT_TEXT
    q = {(m.kind, m.key) for m in find_mentions(item.question, lexicon) if m.kind in ("name", "place", "time")}

    def score(f: Fact) -> int:
        e = fact_elements(f.sentence, lexicon)
        return len(q & ({("name", n) for n in e.names} | {("place", p) for p in e.places}
                        | {("time", t) for t in e.times}))
    best = max(item.facts, key=score)   # max는 동률이면 앞의 것
    return (best.fact_id,), best.sentence


# --- 스트리밍 판정(E-11-12 3번) ---
@dataclass
class SentenceGate:
    """한 번의 생성 시도를 사건 단위로 판정한다: 통과 문장만 공개, 첫 문장 실패면 시도 실패, 둘째 이후 실패면 부분 답.

    `on_event`가 돌려주는 값: `"publish"`(그 문장 공개), `"drop"`(그 문장 버림, 이후 무시 → 부분 답),
    `"fail"`(시도 실패: 재생성 또는 폴백), `None`(근거 줄 읽음 등 공개할 것 없음).
    """
    item: FreeQuestion
    lexicon: Lexicon
    ctx: Context | None = None
    published: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    partial: bool = False
    failed: bool = False

    def on_event(self, event: tuple) -> str | None:
        """사건 하나를 판정한다."""
        if self.failed or self.partial:
            return None
        kind, value = event
        if kind == "bad_header":
            return self._fail([(FORMAT_LENGTH, "근거 줄 형식 아님")])
        if kind == "evidence":
            bad = check_header(value, self.item)
            if bad:
                return self._fail(bad)
            self.ctx = make_context(self.item, value, self.lexicon)
            return None
        issues = check_sentence(value, self.ctx)
        total = len(" ".join([*self.published, value]))
        if len(self.published) >= MAX_SENTENCES:
            issues.append((FORMAT_LENGTH, f"문장 {len(self.published) + 1}개 > {MAX_SENTENCES}개"))
        elif total > MAX_ANSWER_CHARS:
            issues.append((FORMAT_LENGTH, f"답 {total}자 > {MAX_ANSWER_CHARS}자"))
        if not issues:
            self.published.append(value)
            return "publish"
        self.reasons += [c for c, _ in issues]
        if not self.published:
            return self._fail(issues)
        self.partial = True
        return "drop"

    def finish(self) -> str:
        """스트림이 정상으로 끝났을 때 시도 결과: `ok` / `partial` / `fail`(공개 문장 0)."""
        if self.failed or not self.published:
            self.failed = True
            return "fail"
        return "partial" if self.partial else "ok"

    def _fail(self, issues: list[tuple[str, str]]) -> str:
        self.failed = True
        self.reasons += [c for c, _ in issues]
        return "fail"
