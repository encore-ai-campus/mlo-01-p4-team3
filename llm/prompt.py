"""도메인 규칙: 추궁 답변 프롬프트(E-07-07, 기획서 7.2 3단계).

엔진이 정한 사실 문장(`fact_sentence`)과 반응 의도(`reaction`)를 받아, 사실은 그대로 두고 말투·감정만
입히라는 채팅 메시지를 만든다. 채팅 템플릿 적용과 생성은 호출하는 쪽(인프라: `llm/bench/`, 서비스
E-08-09, MVP 노트북 E-07-06)이 한다. 표준 라이브러리만 쓴다.

`GEN_SETTINGS`는 E-06-07 결정(`docs/reports/llm_bench.md` 요약 표, 3절 생성 설정)과 E-07-07 측정에 쓴 값이다.
"""
from __future__ import annotations

PROMPT_VERSION = "interrogate-v2"   # v2: 말투 계열별 예시(11/6)
MODEL_FILE = "kanana-2-3b-instruct.Q4_K_M.gguf"   # E-06-07 결정(잠정): Kanana-2-3B Instruct Q4_K_M

# 생성 설정. 노트북(E-07-06)은 이 값을 첫 칸에 표시하고 그대로 쓴다. seed는 실행마다 run_eval 인자로 준다.
GEN_SETTINGS = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "max_tokens": 128,
    "n_ctx": 1024,
    "n_threads": 8,
}

STOP = ["<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<end_of_turn>", "\n"]

REACTION_KO = {
    "explain": "차분히 해명한다",
    "evade": "말을 흐리며 얼버무린다",
    "counter": "언짢아하며 무당에게 되묻는다",
    "confess": "망설이다 털어놓는다",
}


def speaker_and_style(persona: str) -> tuple[str, str]:
    """persona를 (이름, 말투)로 나눈다. 계약 예시 형식 `"<이름>. 말투: <말투>"`가 아니면 이름은 빈 문자열."""
    head, sep, style = persona.partition("말투:")
    if not sep:
        return "", persona.strip()
    return head.strip().rstrip(".").strip(), style.strip()


# 예시 한 쌍(사실 → 대사). 예시 장소 '방앗간'은 게임 장소가 아니어서, 모델이 예시를 베끼면 검증기가
# `forbidden_entity`로 잡는다(베낀 것이 숨지 않음). 예시 사실의 '4일째'도 평가·게임에 없는 숫자라 베끼면
# `format_length`로 잡힌다. 고백은 행적이 아닌 고백 문장이 사실로 오므로 예시도 고백 문장으로 둔다
# (행적 예시를 주면 고백 사례에서 예시를 베꼈다, 11/5 시험).
WHERE_FACT = "4일째 밤 해 질 무렵엔 방앗간에 있었소."
CONFESS_FACT = "방앗간 뒤에서 몰래 쌀을 내다 팔았소."

# 말투 계열별 예시 덧붙임: (말투 설명에서 찾을 낱말들, 앞에 붙일 말, 반응 의도별 뒤에 붙일 말).
# 사실 문장은 엔진이 하오체로 완성하므로 고치지 않고, 말투는 앞뒤 덧붙임에만 입힌다. 위에서부터 처음 맞는 계열을 쓴다.
# 덧붙임에는 부정·단정 표현과 게임 어휘(인물·장소·시간·역할어)를 넣지 않는다(검증기 거부 대상, 테스트로 고정).
STYLE_PROFILES = (
    (("어린아이",), "히잉…", {"explain": "그게 다예요.", "evade": "몰라요, 그만 물어봐요.",
                             "counter": "무당 아저씨는 어디 있었는데요?", "confess": "잘못했어요, 혼내지 마세요."}),
    (("노인",), "허허… 그게 말이여…", {"explain": "그게 다여.", "evade": "늙은이한테 더 묻지 말게나.",
                                    "counter": "그러는 무당 양반은 어디 있었는감?", "confess": "늙은이가 주책이었구먼."}),
    (("더듬",), "저, 저기…", {"explain": "그, 그게 다입니다요.", "evade": "더, 더는 묻지 말아 주십시오.",
                            "counter": "무, 무당 나리는 어디 계셨습니까요?", "confess": "죄, 죄송합니다요…"}),
    (("사투리",), "아따,", {"explain": "고것이 다여.", "evade": "더 묻지 마쇼잉.",
                         "counter": "그라는 무당 양반은 어디 있었당가?", "confess": "에라, 다 불었소잉."}),
    (("능청",), "어머나,", {"explain": "그게 다랍니다.", "evade": "그 이상은 비밀이랍니다.",
                         "counter": "무당 나리야말로 어디 계셨수?", "confess": "호호, 들켜 버렸네요."}),
    (("말수",), "…", {"explain": "그뿐.", "evade": "됐소.", "counter": "당신은?", "confess": "…그랬소."}),
    (("합장",), "나무아미타불…", {"explain": "그뿐이옵니다.", "evade": "더 묻지 마시옵소서.",
                              "counter": "무당께서는 어디 계셨사옵니까?", "confess": "소승이 업을 지었사옵니다."}),
    (("단정", "조용"), "…예.", {"explain": "그뿐입니다.", "evade": "더는 여쭙지 마셔요.",
                              "counter": "무당께서는 어디 계셨는지요?", "confess": "부끄러울 따름입니다."}),
    (("하게체", "반말"), "에그, 참.", {"explain": "그게 다일세.", "evade": "더 묻지 말게.",
                                   "counter": "그러는 자네는 어디 있었나?", "confess": "에휴, 다 털어놨네."}),
    (("하오체", "문자"), "허허, 이것 참.", {"explain": "그뿐이오.", "evade": "그 이상은 묻지 마시오.",
                                       "counter": "그러는 무당은 어디 계셨소?", "confess": "참으로 부끄러운 일이오."}),
    (("거친", "짧은"), "쳇.", {"explain": "됐소?", "evade": "그만 물으쇼.", "counter": "그러는 당신은?",
                             "confess": "젠장, 다 말했소."}),
)
DEFAULT_PROFILE = ("", {"explain": "그게 다요.", "evade": "더는 묻지 마시오.", "counter": "그걸 왜 나한테 캐묻는 게요?",
                        "confess": "이제 속이 다 후련하오."})


def style_profile(style: str) -> tuple[str, dict[str, str]]:
    """말투 설명에 맞는 (앞 덧붙임, 반응 의도별 뒤 덧붙임). 맞는 계열이 없으면 `DEFAULT_PROFILE`."""
    for keywords, prefix, tails in STYLE_PROFILES:
        if any(k in style for k in keywords):
            return prefix, tails
    return DEFAULT_PROFILE


def example_pair(style: str, reaction: str) -> tuple[str, str]:
    """예시 한 쌍(사실 문장, 대사). 대사 = 말투 계열의 앞 덧붙임 + 사실 문장 그대로 + 반응 의도별 뒤 덧붙임."""
    key = reaction if reaction in REACTION_KO else "explain"
    fact = CONFESS_FACT if key == "confess" else WHERE_FACT
    prefix, tails = style_profile(style)
    return fact, " ".join(x for x in (prefix, fact, tails[key]) if x)


def build_messages(req: dict) -> list[dict]:
    """추궁 요청 1건(`RenderRequest` 필드 dict)을 채팅 메시지로 만든다: system, 예시 user·assistant 한 쌍(말투 계열별), user.

    프롬프트 처리 시간이 지연의 대부분이므로(E-06-07) 짧게 유지한다. 사실 문장은 마지막 user 메시지에 그대로 넣는다.
    """
    name, style = speaker_and_style(req.get("persona", ""))
    who = f"조선 시대 산골 마을의 {name}" if name else "조선 시대 산골 마을 사람"
    key = req.get("reaction", "") if req.get("reaction", "") in REACTION_KO else "explain"
    ex_fact, ex_line = example_pair(style, key)
    system = (
        f"너는 {who}이다. 말투: {style}. 무당이 캐묻는다. 태도: {REACTION_KO[key]}.\n"
        "받은 사실 문장을 한 글자도 바꾸지 말고 그대로 넣고, 앞뒤에 이 인물 말투로 감정·머뭇거림만 덧붙여 대사 한두 문장을 쓴다.\n"
        "사실 문장에 없는 사람·장소·시간을 말하지 않는다. '안 했소', '틀림없소' 같은 부정·단정을 덧붙이지 않는다. "
        "대사만 한 줄로 쓰고 따옴표·괄호·설명은 쓰지 않는다."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"사실 문장: {ex_fact}"},
            {"role": "assistant", "content": ex_line},
            {"role": "user", "content": f"사실 문장: {req['fact_sentence']}"}]
