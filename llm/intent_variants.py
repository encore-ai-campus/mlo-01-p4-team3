"""도메인: 의도 예시 변형 생성의 프롬프트·출력 파싱·보존 검사(E-06-02 2단계).

사람 작성 원본(`content/intent/examples_v1.jsonl`, `label_source: authored`) 한 행마다 로컬 LLM에
말투·어순·조사·오타만 바꾼 문장을 최대 3개 요청하고, 의도·대상·극성·발화 형태가 바뀐 후보를 규칙으로 거른다.
통과한 후보도 `generated`(검수 전)일 뿐이고, 학습에 쓰는 `generated_reviewed`는 검수자 D 판정 뒤에 붙인다.

규칙(11/6 사용자 검토, `docs/evidence/E-06-02.md` 재시도 계획): 하오체 행은 외래어·현대어 금지,
`register=modern` 행은 현대어 유지, 지도에 없는 직업·장소 금지, 사람을 주어로 탁하다·맑다고 하지 않는다.

표준 라이브러리만 쓴다. 모델 호출·파일 읽기·중복 검사(다른 행과 비교)는 인프라·응용(`llm/bench/gen_intent_variants.py`)이 한다.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

PROMPT_VERSION = "intent-variants-v1"
MAX_VARIANTS = 3
GEN_SETTINGS = {"temperature": 0.9, "top_p": 0.9, "top_k": 40, "max_tokens": 160, "n_ctx": 1024}
STOP = ["<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<end_of_turn>", "\n\n\n"]

LABEL_KO = {
    "suspect": "상대를 의심한다고 말한다",
    "defend": "상대를 감싸며 믿는다고 말한다",
    "point_contradiction": "두 사람의 말이 서로 어긋난다고 짚는다",
    "explain": "자기 행적이나 사정을 해명한다",
    "silence": "이번에는 말하지 않고 지켜보겠다고 한다",
    "reveal_divination": "점괘 결과를 밝힌다",
    "interrogate_whereabouts": "상대에게 어디서 무엇을 했는지 묻는다",
    "interrogate_contradiction": "상대에게 말이 어긋나는 까닭을 묻는다",
    "interrogate_suspicion_reason": "상대에게 왜 의심하는지 묻는다",
    "unsupported": "게임 진행과 관계없는 잡담이나 요청을 한다",
}
FORM_KO = {"assertion": "단언하는 말", "question": "묻는 말", "quote": "남의 말을 옮기며 묻는 말",
           "request": "해 달라고 청하는 말"}

REJECT_REASONS = ("empty", "same_as_root", "prompt_copy", "too_long", "placeholder", "form", "negation", "foreign",
                  "register", "new_entity", "new_suspicion", "divination")
# 의심 낱말: 원문에 없는데 후보에 나오면 의심 쪽으로 뜻이 바뀐 것으로 본다(침묵·해명이 의심으로 바뀌는 사례)
SUSPICION_WORDS = ("수상", "의심", "미심쩍", "거짓")

_PLACEHOLDER = re.compile(r"<P\d>")
_ANY_TAG = re.compile(r"<[^>]*>")
_NUMBERING = re.compile(r"^\s*(?:\d+[.)]|[-*•·]|[①②③④⑤])\s*")
_QUOTES = "\"'“”‘’「」『』"
_LATIN_DIGIT = re.compile(r"[A-Za-z0-9]")
_NEG_WORDS = ("않", "못", "아니", "없", "말고", "마시", "말게", "말라")
_NEG_AN = re.compile(r"(?:^|\s)안\s")
_PERSON_CLEAR_MURKY = re.compile(r"<P\d>\s*(?:이|가|은|는|도)\s*\S*\s*(?:맑|탁)")
_MODERN_END = re.compile(r"(?:요|죠|임|ㅋ|ㅎ)[?!.~]*$")


def note_register(notes: str) -> str:
    """`notes`의 `register=` 값(없으면 하오체 "hao")."""
    for part in notes.split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "register":
            return value.strip()
    return "hao"


# 예시 한 쌍(말투 계열 × 묻는 말 여부). 데이터가 아니라 출력 형식을 보여 주는 프롬프트 예시다.
EXAMPLES = {
    ("hao", True): ("원문: <P1>, 어젯밤에 어디 있었소?\n의도: 상대에게 어디서 무엇을 했는지 묻는다 (묻는 말)",
        "<P1>, 간밤에 어디 있었소?\n<P1> 자네 어젯밤에는 어디에 있었는가?\n어젯밤엔 어디 있었소, <P1>?"),
    ("hao", False): ("원문: <P1>이 수상해 보이오\n의도: 상대를 의심한다고 말한다 (단언하는 말)",
        "<P1>이 아무래도 수상해 보이오\n아무래도 <P1>이 수상하오\n<P1> 저 사람이 수상해 보이는구려"),
    ("modern", True): ("원문: <P1> 어제 밤에 어디 있었어?\n의도: 상대에게 어디서 무엇을 했는지 묻는다 (묻는 말)",
        "<P1> 어젯밤에 어디 있었냐?\n어제 밤엔 어디 있었어 <P1>?\n<P1>, 어젯밤 어디 있었던 거야?"),
    ("modern", False): ("원문: <P1> 좀 수상한데\n의도: 상대를 의심한다고 말한다 (단언하는 말)",
        "<P1> 진짜 수상하다\n아무래도 <P1> 수상해\n<P1> 좀 이상한데"),
}

# 예시 답의 문장들(모델이 예시를 그대로 베낀 후보를 거른다)
_EXAMPLE_LINES = {line for _, answer in EXAMPLES.values() for line in answer.splitlines()}

def build_messages(row: dict, n: int = MAX_VARIANTS) -> list[dict]:
    """원본 행(dict) 하나에 대한 채팅 메시지: system(규칙), 예시 user·assistant 한 쌍, user. 출력은 한 줄에 하나, n줄."""
    register = "modern" if note_register(row.get("notes", "")) == "modern" else "hao"
    rules = [
        "<P1>, <P2> 같은 표시는 글자 그대로 남기고, 새 표시를 만들거나 빼지 않는다.",
        "뜻(의도)·대상·긍정과 부정·말의 형태(묻는 말은 묻는 말, 단언은 단언)를 바꾸지 않는다.",
        "말투·어순·조사·낱말 선택·흔한 오타만 바꾼다. 원문과 똑같은 문장은 쓰지 않는다.",
        "원문에 없는 사람·직업·장소·시간·사건을 새로 넣지 않는다.",
    ]
    if register == "modern":
        rules.append("원문처럼 요즘 말투(반말·줄임말)를 유지한다.")
    else:
        rules.append("원문처럼 옛말 하오체(…하오, …소, …시오, …는가)를 유지하고 외래어·요즘 말투(…요, …죠)는 쓰지 않는다.")
    if row.get("label") == "reveal_divination":
        rules.append("점괘가 맑다·탁하다는 점괘나 기운을 주어로 말하고, 사람을 주어로 맑다·탁하다고 하지 않는다.")
    system = ("너는 조선 시대 마을 배경 추리 게임에서 플레이어가 입력한 문장을 같은 뜻의 다른 문장으로 바꿔 쓴다. "
              f"바꾼 문장 {n}개를 한 줄에 하나씩 쓰고, 번호·따옴표·설명은 쓰지 않는다.\n"
              + "\n".join(f"- {r}" for r in rules))
    ex_user, ex_answer = EXAMPLES[(register, "?" in row["text"])]
    user = f"원문: {row['text']}\n의도: {LABEL_KO[row['label']]} ({FORM_KO.get(row['form'], row['form'])})"
    return [{"role": "system", "content": system}, {"role": "user", "content": ex_user},
            {"role": "assistant", "content": ex_answer}, {"role": "user", "content": user}]


def parse_output(raw: str, n: int = MAX_VARIANTS) -> list[str]:
    """생성 원문을 후보 문장 목록으로 푼다: 줄마다 번호·글머리표·감싼 따옴표를 지우고 빈 줄은 버린다."""
    out = []
    for line in (raw or "").splitlines():
        text = _NUMBERING.sub("", line).strip()
        if len(text) >= 2 and text[0] in _QUOTES and text[-1] in _QUOTES:
            text = text[1:-1].strip()
        if text:
            out.append(text)
    return out[:n]


def has_negation(text: str) -> bool:
    """부정 표현이 있는가(간단 규칙: 않·못·아니·없·말고 등, 띄어 쓴 '안')."""
    return any(w in text for w in _NEG_WORDS) or bool(_NEG_AN.search(text))


def check_variant(row: dict, text: str, entities: Iterable[str] = ()) -> str | None:
    """원본 행 대비 후보 문장 하나를 검사한다. 통과면 None, 아니면 `REJECT_REASONS` 중 하나.

    `entities`: 인물 이름·별칭·장소·역할어 같은 어휘(콘텐츠 묶음에서 인프라가 만든다). 원문에 없는데
    후보에 나오면 새 정보로 보고 거부한다.
    """
    orig = row["text"]
    if not text.strip():
        return "empty"
    if text.strip() == orig.strip():
        return "same_as_root"
    if text.strip() in _EXAMPLE_LINES:
        return "prompt_copy"
    if len(text) > max(30, 2 * len(orig)):
        return "too_long"
    if Counter(_PLACEHOLDER.findall(text)) != Counter(_PLACEHOLDER.findall(orig)) \
            or len(_ANY_TAG.findall(text)) != len(_PLACEHOLDER.findall(text)):
        return "placeholder"
    if ("?" in text) != ("?" in orig):
        return "form"
    if has_negation(text) != has_negation(orig):
        return "negation"
    if set(_LATIN_DIGIT.findall(_PLACEHOLDER.sub("", text))) - set(_LATIN_DIGIT.findall(_PLACEHOLDER.sub("", orig))):
        return "foreign"
    if note_register(row.get("notes", "")) != "modern" and _MODERN_END.search(text) and not _MODERN_END.search(orig):
        return "register"
    for word in entities:
        if word and word in text and word not in orig:
            return "new_entity"
    if any(w in text and w not in orig for w in SUSPICION_WORDS):
        return "new_suspicion"
    if row.get("label") == "reveal_divination":
        result = row.get("slots", {}).get("result")
        want, other = ("맑", "탁") if result == "clear" else ("탁", "맑")
        if want not in text or other in text or _PERSON_CLEAR_MURKY.search(text):
            return "divination"
    return None


def variant_row(root: dict, n: int, text: str) -> dict:
    """원본 행에서 계보 규칙(`<원본>_vN`, 라벨·형태·슬롯·대조그룹·분할 상속)을 지킨 `generated` 변형 행."""
    key = dict(root["split_key"], lineage_root=root["example_id"])
    return {"example_id": f"{root['example_id']}_v{n}", "text": text, "label": root["label"], "form": root["form"],
            "slots": root["slots"], "label_source": "generated", "split": root["split"], "split_key": key,
            "contrast_group": root.get("contrast_group"), "notes": root.get("notes", "")}
