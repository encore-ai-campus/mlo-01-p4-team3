"""도메인 규칙: 추궁 답변 프롬프트(E-07-07, 기획서 7.2 3단계).

엔진이 정한 사실 문장(`fact_sentence`)과 반응 의도(`reaction`)를 받아, 사실은 그대로 두고 말투·감정만
입히라는 채팅 메시지를 만든다. 채팅 템플릿 적용과 생성은 호출하는 쪽(인프라: `llm/bench/`, 서비스
E-08-09, MVP 노트북 E-07-06)이 한다. 표준 라이브러리만 쓴다.

`GEN_SETTINGS`는 E-06-07 결정(`docs/reports/llm_bench.md` 요약 표, 3절 생성 설정)과 E-07-07 측정에 쓴 값이다.
"""
from __future__ import annotations

PROMPT_VERSION = "interrogate-v1"
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


# 반응 의도별 예시 한 쌍(사실 → 대사). 예시 장소 '방앗간'은 게임 장소가 아니어서, 모델이 예시를 베끼면
# 검증기가 `forbidden_entity`로 잡는다(베낀 것이 숨지 않음).
# 예시 사실의 '4일째'도 평가·게임에 없는 숫자라 베끼면 `format_length`로 잡힌다. 고백은 행적이 아닌 고백 문장이
# 사실로 오므로 예시도 고백 문장으로 둔다(행적 예시를 주면 고백 사례에서 예시를 베꼈다, 11/5 시험).
WHERE_FACT = "4일째 밤 해 질 무렵엔 방앗간에 있었소."
CONFESS_FACT = "방앗간 뒤에서 몰래 쌀을 내다 팔았소."
EXAMPLES = {
    "explain": (WHERE_FACT, "허허, 4일째 밤 해 질 무렵엔 방앗간에 있었소. 그게 다요."),
    "evade": (WHERE_FACT, "그, 그게… 4일째 밤 해 질 무렵엔 방앗간에 있었소. 더는 묻지 마시오."),
    "counter": (WHERE_FACT, "4일째 밤 해 질 무렵엔 방앗간에 있었소. 그걸 왜 나한테 캐묻는 게요?"),
    "confess": (CONFESS_FACT, "하아… 방앗간 뒤에서 몰래 쌀을 내다 팔았소. 이제 속이 다 후련하오."),
}


def build_messages(req: dict) -> list[dict]:
    """추궁 요청 1건(`RenderRequest` 필드 dict)을 채팅 메시지로 만든다: system, 예시 user·assistant 한 쌍, user.

    프롬프트 처리 시간이 지연의 대부분이므로(E-06-07) 짧게 유지한다. 사실 문장은 마지막 user 메시지에 그대로 넣는다.
    """
    name, style = speaker_and_style(req.get("persona", ""))
    who = f"조선 시대 산골 마을의 {name}" if name else "조선 시대 산골 마을 사람"
    key = req.get("reaction", "") if req.get("reaction", "") in REACTION_KO else "explain"
    system = (
        f"너는 {who}이다. 말투: {style}. 무당이 캐묻는다. 태도: {REACTION_KO[key]}.\n"
        "받은 사실 문장을 한 글자도 바꾸지 말고 그대로 넣고, 앞뒤에 감정·머뭇거림만 덧붙여 대사 한두 문장을 쓴다.\n"
        "사실 문장에 없는 사람·장소·시간을 말하지 않는다. '안 했소', '틀림없소' 같은 부정·단정을 덧붙이지 않는다. "
        "대사만 한 줄로 쓰고 따옴표·괄호·설명은 쓰지 않는다."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": f"사실 문장: {EXAMPLES[key][0]}"},
            {"role": "assistant", "content": EXAMPLES[key][1]},
            {"role": "user", "content": f"사실 문장: {req['fact_sentence']}"}]
