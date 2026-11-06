"""도메인 규칙: 추궁 답변 프롬프트(E-07-07, 기획서 7.2 3단계).

엔진이 정한 사실 문장(`fact_sentence`)과 반응 의도(`reaction`)를 받아, 사실 요소는 그대로 두고 끝 어미와
앞뒤 덧붙임에 말투·감정을 입히라는 채팅 메시지를 만든다. 채팅 템플릿 적용과 생성은 호출하는 쪽(인프라: `llm/bench/`, 서비스
E-08-09, MVP 노트북 E-07-06)이 한다. 표준 라이브러리만 쓴다.

`GEN_SETTINGS`는 E-06-07 결정(`docs/reports/llm_bench.md` 요약 표, 3절 생성 설정)과 E-07-07 측정에 쓴 값이다.
"""
from __future__ import annotations

import zlib

PROMPT_VERSION = "interrogate-v6"   # v2: 말투 계열별 예시, v3: 사실 문장 끝 어미도 말투로, v4: 덧붙임 후보를 발언마다, v5: 앞말 없음 후보 삭제, v6: 사투리 계열을 강원도식으로(11/6)
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
_PAST_SO = ("었소", "았소")   # 예시 사실 문장의 끝 서술어("있었소", "팔았소")에서 어미 "소"를 바꿀 자리

# 말투 계열: (말투 설명에서 찾을 낱말들, 앞에 붙일 말 후보들, 끝 어미들, 반응 의도별 뒤에 붙일 말 후보들).
# 사실 문장은 엔진이 하오체로 완성한다. 장소·시간·인물·행동은 그대로 두고 끝 서술어의 어미("있었소"의 "소")만 이
# 계열 어미로 바꾼다(11/6 사용자 결정). 끝 어미들의 첫째는 예시에 쓰고, 전부는 검증기가 이 인물에게 허용하는 어미
# 목록이다(`allowed_endings`, 목록 밖은 엔진 원래 어미만 허용). 평서·명령 어미만 두고 의문·추측·전언은 넣지
# 않는다. 위에서부터 처음 맞는 계열을 쓴다.
# 앞·뒤 덧붙임은 후보 중 하나를 발언 ID로 고른다(`example_pick`, 11/6 사용자 지시: 같은 말버릇이 매번 나오지 않게).
# 앞말은 비워 두지 않는다: 앞말 없는 예시는 모델이 예시 대사(방앗간 문장)를 그대로 베꼈다(11/6 측정, 일관성
# 85 % → 65 %). 덧붙임에는 부정·단정 표현과 게임 어휘(인물·장소·시간·역할어)를 넣지
# 않는다(검증기 거부 대상, 모든 조합을 테스트로 고정).
STYLE_PROFILES = (
    (("어린아이",), ("히잉…", "음…", "저기요,"),
     ("어요", "아요", "여요", "에요", "예요", "요", "세요"),
     {"explain": ("그게 다예요.", "그것뿐이에요."),
      "evade": ("몰라요, 그만 물어봐요.", "이제 그만 물어보면 좋겠어요."),
      "counter": ("무당 아저씨는 어디 있었는데요?", "아저씨는요? 아저씨는 어디 있었어요?"),
      "confess": ("잘못했어요, 혼내지 마세요.", "다 말했어요. 혼내지 마세요.")}),
    (("노인",), ("허허… 그게 말이여…", "어디 보자…", "에헴,"),
     ("구먼", "구먼요", "여", "어", "아", "네", "지", "게", "게나"),
     {"explain": ("그게 다여.", "그뿐이구먼."),
      "evade": ("늙은이한테 더 묻지 말게나.", "그만허면 됐구먼."),
      "counter": ("그러는 무당 양반은 어디 있었는감?", "무당 양반은 어디 계셨는가?"),
      "confess": ("늙은이가 주책이었구먼.", "허허, 부끄럽구먼.")}),
    (("더듬",), ("저, 저기…", "그, 그러니까…", "예, 예…"),
     ("습니다요", "습니다", "니다요", "니다", "어요", "아요", "여요", "요", "십시오", "세요"),
     {"explain": ("그, 그게 다입니다요.", "그, 그뿐입니다요."),
      "evade": ("더, 더는 묻지 말아 주십시오.", "그, 그만 여쭈어 주십시오…"),
      "counter": ("무, 무당 나리는 어디 계셨습니까요?", "나, 나리께서는 어디 계셨습니까요?"),
      "confess": ("죄, 죄송합니다요…", "요, 용서해 주십시오…")}),
    # 강원도 산골(솔뫼골) 투박한 사투리(11/6 사용자 결정: 전라도식 "~당께·~잉·거시기"에서 바꿈). 과장된 방언·현대 은어는 피한다.
    (("사투리", "강원"), ("아이고 참…", "그기 말이요…", "에이 참,"),
     ("드래요", "잖소", "지요", "어요", "아요", "소", "우", "시오", "세요"),
     {"explain": ("그기 다래요.", "그뿐이드래요."),
      "evade": ("고만 물어보시오.", "더는 묻지 마시오."),
      "counter": ("무당 양반은 어디 있었소?", "그러는 무당 양반은요?"),
      "confess": ("아이고, 다 말했잖소.", "미안하게 됐소.")}),
    (("능청",), ("어머나,", "호호,", "어머,"),
     ("답니다", "지요", "죠", "네요", "어요", "아요", "수", "우", "세요", "셔요"),
     {"explain": ("그게 다랍니다.", "그뿐이랍니다."),
      "evade": ("그 이상은 비밀이랍니다.", "궁금해도 참으셔요."),
      "counter": ("무당 나리야말로 어디 계셨수?", "나리는 어디 계셨는지 먼저 말해 보셔요."),
      "confess": ("호호, 들켜 버렸네요.", "에구, 다 들켜 버렸네요.")}),
    # 단정·조용은 말수 계열보다 먼저 본다(실제 과부 "조용하고 단정한 말투. 말수가 적고…", 11/6)
    (("단정", "조용"), ("…예.", "그게…", "말씀드리지요."),
     ("습니다", "니다", "어요", "아요", "여요", "요", "지요", "셔요", "세요", "십시오"),
     {"explain": ("그뿐입니다.", "그것이 전부입니다."),
      "evade": ("더는 여쭙지 마셔요.", "그 이상은 말씀드리기 어렵습니다."),
      "counter": ("무당께서는 어디 계셨는지요?", "무당께서는 어디 계셨나요?"),
      "confess": ("부끄러울 따름입니다.", "부디 덮어 주셔요.")}),
    (("말수",), ("…", "흠."),
     ("소", "오"),
     {"explain": ("그뿐.", "그게 다요."), "evade": ("됐소.", "거기까지."),
      "counter": ("당신은?", "그쪽은?"), "confess": ("…그랬소.", "…다 말했소.")}),
    (("합장",), ("나무아미타불…", "관세음보살…", "시주님,"),
     ("사옵니다", "옵니다", "습니다", "니다", "소이다", "시옵소서", "소서", "십시오"),
     {"explain": ("그뿐이옵니다.", "그것이 전부이옵니다."),
      "evade": ("더 묻지 마시옵소서.", "그 이상은 부처님만 아시옵니다."),
      "counter": ("무당께서는 어디 계셨사옵니까?", "무당께서는 어디에 계셨는지요?"),
      "confess": ("소승이 업을 지었사옵니다.", "소승의 죄가 크옵니다.")}),
    (("하게체", "반말"), ("에그, 참.", "이 사람아,", "거 참,"),
     ("네", "다네", "일세", "어", "아", "지", "게", "게나", "라"),
     {"explain": ("그게 다일세.", "그뿐이네."),
      "evade": ("더 묻지 말게.", "그만 묻게나."),
      "counter": ("그러는 자네는 어디 있었나?", "자네야말로 어디 있었는가?"),
      "confess": ("에휴, 다 털어놨네.", "허, 부끄럽네그려.")}),
    (("하오체", "문자"), ("허허, 이것 참.", "에헴.", "거 참,"),
     ("소", "오", "이오", "구려", "소이다", "시오"),
     {"explain": ("그뿐이오.", "그것이 전부요."),
      "evade": ("그 이상은 묻지 마시오.", "그만 물으시오."),
      "counter": ("그러는 무당은 어디 계셨소?", "무당이야말로 어디 계셨소?"),
      "confess": ("참으로 부끄러운 일이오.", "참으로 송구하오.")}),
    # "거칠"·"짧게": 실제 대장장이 "거칠고 짧게 끊어 말한다"(11/6)
    (("거친", "거칠", "짧은", "짧게"), ("쳇.", "흥.", "에잇,"),
     ("다", "소", "어", "아", "지", "쇼", "라"),
     {"explain": ("됐소?", "끝."), "evade": ("그만 물으쇼.", "귀찮게 굴지 마쇼."),
      "counter": ("그러는 당신은?", "당신이나 말해 보쇼."), "confess": ("젠장, 다 말했소.", "젠장, 다 불었소.")}),
)
DEFAULT_PROFILE = (("흠,", "그게…", "허,"), ("소", "오", "시오"),
                   {"explain": ("그게 다요.", "그뿐이오."), "evade": ("더는 묻지 마시오.", "그만 물으시오."),
                    "counter": ("그걸 왜 나한테 캐묻는 게요?", "무당은 어디 있었소?"),
                    "confess": ("이제 속이 다 후련하오.", "부끄럽소.")})


def style_profile(style: str) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, tuple[str, ...]]]:
    """말투 설명에 맞는 (앞 덧붙임 후보들, 끝 어미들, 반응 의도별 뒤 덧붙임 후보들). 없으면 `DEFAULT_PROFILE`."""
    for keywords, prefixes, endings, tails in STYLE_PROFILES:
        if any(k in style for k in keywords):
            return prefixes, endings, tails
    return DEFAULT_PROFILE


def all_style_endings() -> frozenset[str]:
    """모든 말투 계열의 끝 어미. 검증기가 '다른 말투 어미'(사실은 그대로)와 그 밖의 어미(추측·전언 등)를 가른다."""
    return frozenset(e for p in (*STYLE_PROFILES, DEFAULT_PROFILE) for e in p[-2])


def allowed_endings(persona: str) -> frozenset[str]:
    """이 인물(persona)의 사실 문장 끝 서술어에 허용하는 어미(말투 계열의 끝 어미들). 검증기가 쓴다."""
    return frozenset(style_profile(speaker_and_style(persona)[1])[1])


def restyle_example_fact(fact: str, ending: str) -> str:
    """예시 사실 문장의 끝 서술어 어미 "소"를 계열 어미로 바꾼다("있었소." → "있었어요.")."""
    for past in _PAST_SO:
        if past in fact:
            return fact.replace(past, past[0] + ending, 1)
    return fact


def example_pick(speech_id: str) -> int:
    """덧붙임 후보를 고를 수. 발언 ID에서 정해지므로 같은 발언은 늘 같은 예시(재측정 재현), 발언마다는 달라진다.

    파이썬 `hash`는 실행마다 달라지므로 쓰지 않고 CRC32를 쓴다.
    """
    return zlib.crc32(speech_id.encode("utf-8"))


def example_pair(style: str, reaction: str, pick: int = 0) -> tuple[str, str]:
    """예시 한 쌍(사실 문장, 대사). 대사 = 앞 덧붙임 + 끝 어미만 말투로 바꾼 사실 문장 + 반응 의도별 뒤 덧붙임.

    `pick`으로 앞 덧붙임 후보(pick % 앞말 수)와 뒤 덧붙임 후보((pick // 앞말 수) % 뒷말 수)를 고른다.
    """
    key = reaction if reaction in REACTION_KO else "explain"
    fact = CONFESS_FACT if key == "confess" else WHERE_FACT
    prefixes, endings, tails = style_profile(style)
    prefix = prefixes[pick % len(prefixes)]
    tail = tails[key][(pick // len(prefixes)) % len(tails[key])]
    return fact, " ".join(x for x in (prefix, restyle_example_fact(fact, endings[0]), tail) if x)


def build_messages(req: dict) -> list[dict]:
    """추궁 요청 1건(`RenderRequest` 필드 dict)을 채팅 메시지로 만든다: system, 예시 user·assistant 한 쌍(말투 계열별), user.

    프롬프트 처리 시간이 지연의 대부분이므로(E-06-07) 짧게 유지한다. 사실 문장은 마지막 user 메시지에 그대로 넣는다.
    """
    name, style = speaker_and_style(req.get("persona", ""))
    who = f"조선 시대 산골 마을의 {name}" if name else "조선 시대 산골 마을 사람"
    key = req.get("reaction", "") if req.get("reaction", "") in REACTION_KO else "explain"
    ex_fact, ex_line = example_pair(style, key, example_pick(req.get("speech_id", "")))
    system = (
        f"너는 {who}이다. 말투: {style}. 무당이 캐묻는다. 태도: {REACTION_KO[key]}.\n"
        "받은 사실 문장의 장소·시간·사람·행동은 그대로 두고 문장 끝 어미만 이 인물 말투로 바꾼다. "
        "앞뒤에 이 인물 말투로 감정·머뭇거림을 덧붙여 대사 한두 문장을 쓴다.\n"
        "사실 문장에 없는 사람·장소·시간을 말하지 않는다. '안 했소', '틀림없소' 같은 부정·단정을 덧붙이지 않는다. "
        "대사만 한 줄로 쓰고 따옴표·괄호·설명은 쓰지 않는다."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"사실 문장: {ex_fact}"},
            {"role": "assistant", "content": ex_line},
            {"role": "user", "content": f"사실 문장: {req['fact_sentence']}"}]
