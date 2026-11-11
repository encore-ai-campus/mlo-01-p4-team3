"""템플릿에서 공개 ID와 화면 문구만 변환하는 도우미."""
from django import template
from django.utils.html import format_html
from .assets import asset_url

register = template.Library()
register.simple_tag(asset_url)
from .ui_assets import review_asset_url, vn_sprite_url
register.simple_tag(review_asset_url, name='review_asset')
register.simple_tag(vn_sprite_url, name='vn_sprite')


@register.inclusion_tag('village_assets.html')
def village_guide():
    """manifest의 고정 지도와 공개 인물 목록만 표시한다."""
    from .village_assets import village_assets
    return village_assets()


MODEL_LABELS = {"intent": "의도 해석", "suspicion": "의심 판단", "bandit": "광고 배치"}
STAGE_LABELS = {"canary": "시험 적용", "previous": "이전 버전"}


@register.inclusion_tag('update_history.html')
def update_history():
    """게임 화면 안 업데이트 내역 패널(B-09-01 3번). 레지스트리 승격 기록만 읽고, 없으면 빈 내역을 보인다."""
    from server.app.model_versions import update_history as history
    from server.paldo_web.composition import GameServiceUnavailable, get_game_service
    try:
        registry = getattr(get_game_service(), "model_registry", None)
    except GameServiceUnavailable:
        registry = None
    records = [{"at": r.at, "model": r.model, "version": r.version, "stage": r.stage,
                "label": MODEL_LABELS.get(r.model, r.model), "stage_label": STAGE_LABELS.get(r.stage, "")}
               for r in history(registry)]
    return {"records": records}


@register.inclusion_tag('asset_credits.html')
def asset_attribution():
    """게임 화면 하단에 출처 파일의 공개 요약을 표시한다."""
    from .village_assets import image_source
    return {'asset_source': image_source()}

ACTION_LABELS = {"advance": "다음으로", "suspect": "의심 제기", "defend": "변호", "silence": "지켜보기",
                 "interrogate": "캐묻기", "point_contradiction": "모순 지적", "reveal_divination": "점괘 공개",
                 "vote": "투표", "divine": "점치기", "explain": "설명하기"}


@register.filter
def action_label(kind):
    """명령 종류를 플레이어가 읽을 문구로 바꾼다."""
    return ACTION_LABELS.get(kind, kind)


SLOT_LABELS = {"target": "대상", "addressee": "질문 상대", "interrogation_kind": "질문", "evidence": "관련 진술",
               "divination": "점괘", "night": "밤", "slot": "시간", "night_plan": "밤 계획"}


@register.filter
def slot_label(slot):
    """카드의 빠진 항목(명령 칸 이름)을 카드 수정 칸과 같은 화면 문구로 바꾼다."""
    return SLOT_LABELS.get(slot, slot)


@register.filter
def phase_label(value):
    """엔진 단계 값을 화면용 한국어로 표시한다."""
    return {"morning": "아침", "round1": "첫 회의", "round2": "둘째 회의",
            "interrogation": "캐묻기", "vote": "투표", "runoff": "결선 투표",
            "night": "밤", "ended": "종료"}.get(value, value)


@register.filter
def status_label(value):
    """인물 상태를 화면 문구로 변환한다."""
    return {"active": "활동 중", "locked": "감금", "missing": "실종"}.get(value, value)


@register.filter
def display_name(value):
    """가짜 콘텐츠에도 플레이어 표시 이름을 제공한다."""
    return "무당" if value == "mudang" else value


@register.filter
def get_item(mapping, key):
    """사전에서 키 값을 꺼낸다(없으면 빈 목록). 발언 ID별 단서 알림 줄처럼 템플릿 반복 안에서 쓴다."""
    return (mapping or {}).get(key, ())


@register.filter
def action_icon(kind):
    """UI manifest에 공통 아이콘이 있는 행동의 ID를 고른다."""
    return f"ui:{kind}" if kind in {"suspect", "defend", "interrogate", "vote"} else ""


# 게임 화면 버튼의 마우스 올림 설명(기능 안내 UI 문구, 대사 아님). 문구는 이 사전 한 곳에서만 고친다.
# aria-label이 있는 버튼은 같은 문구를 쓴다. {name}은 공개된 인물 이름이다.
UI_TIPS = {
    "records_open": "공개 기록 창을 엽니다",
    "notebook_open": "내 수첩을 엽니다",
    "help_open": "게임 방법 도움말을 엽니다",
    "help_close": "도움말 닫기",
    "actor": "다음 대사로 넘깁니다",
    "person": "{name} 행동 보기",
    "stage_actions": "지금 할 수 있는 행동을 고릅니다",
    "stage_people": "마을 사람 현황을 봅니다",
    "stage_records": "진술·발언·투표 기록을 봅니다",
    "stage_notebook": "내 수첩을 엽니다",
    "stage_map": "마을 지도와 인물 안내를 봅니다",
    "vn_full_close": "전신 그림을 닫고 대사로 돌아갑니다",
    "vn_prev": "이전 대사를 다시 봅니다",
    "vn_next": "다음 대사로 넘깁니다(무대를 눌러도 됩니다)",
    "vn_show_all": "지금까지의 공개 발언을 모두 봅니다",
    "vn_skip": "연출을 건너뛰고 발언 기록을 엽니다",
    "vn_instant": "켜면 대사를 한 번에 표시합니다",
    "vn_guide_skip": "이번 판의 남은 안내를 건너뜁니다",
    "mark:100": "{name} · 수상함으로 수첩에 적습니다",
    "mark:50": "{name} · 모르겠음으로 수첩에 적습니다",
    "mark:0": "{name} · 믿음으로 수첩에 적습니다",
    "record_compare": "선택한 두 진술을 나란히 봅니다",
    "record_clear": "진술 선택을 모두 풉니다",
    "overlay_close": "이 창을 닫습니다",
    "confirm_cancel": "행동을 보내지 않고 닫습니다",
    "confirm_submit": "선택한 행동을 보냅니다",
    "utterance_submit": "입력한 말을 행동 카드로 바꿔 확인합니다",
    "guide_toggle": "켜면 이번 판에서 족제비 안내를 첫 판처럼 무대에 띄웁니다",
    "notebook_save": "투표 직전 확신도를 저장합니다",
    "card_run": "이 카드의 행동을 실행합니다",
    "card_edit_run": "고친 카드로 행동을 실행합니다",
    "reaction_retry": "입력한 말을 다시 해석합니다",
    "reaction_cancel": "이번 입력을 취소합니다",
    "reaction_switch_to_buttons": "버튼으로 행동을 고릅니다",
    "reaction_none_of_these": "맞는 카드가 없다고 알립니다",
    "end_tab_result": "판 결과를 봅니다",
    "end_tab_review": "그날의 선택을 돌아봅니다",
    "end_tab_clues": "원한 단서를 봅니다",
    "survey_submit": "설문 응답을 보냅니다",
    "survey_skip": "설문을 건너뜁니다",
    "action:suspect": "이 인물을 의심한다고 말합니다",
    "action:defend": "이 인물을 감싸 줍니다",
    "action:interrogate": "이 인물에게 행적이나 이유를 따져 묻습니다",
    "action:point_contradiction": "두 진술이 서로 어긋난다고 지적합니다",
    "action:explain": "내 행동을 해명합니다",
    "action:silence": "이번 차례에는 말하지 않습니다",
    "action:reveal_divination": "내 점괘를 모두에게 공개합니다",
    "action:vote": "이 인물에게 감금 표를 던집니다",
    "action:divine": "오늘 밤 이 인물을 점칩니다",
    "action:advance": "다음 단계로 넘어갑니다",
    "open_resume": "중단하지 않고 진행 중인 판으로 돌아갑니다",
    "open_restart": "진행 중인 판을 중단하고 새 판을 시작합니다",
}


@register.simple_tag
def tip_text(key, **values):
    """버튼 설명 문구만 돌려준다(JS가 읽는 data 속성용)."""
    return UI_TIPS[key].format(**values)


@register.filter
def notebook_mark(value):
    """저장된 수첩 값(0~100)을 버튼 값으로 묶는다. 저장한 적 없으면 기본값 50과 같이 '모르겠음'."""
    if value is None or value == "":
        return "50"
    value = int(value)
    return "100" if value >= 67 else "0" if value <= 33 else "50"


@register.simple_tag
def tip(key, **values):
    """버튼에 붙일 ` title="…"` 속성을 만든다. 사전에 없는 키는 오류로 드러낸다."""
    return format_html(' title="{}"', tip_text(key, **values))
