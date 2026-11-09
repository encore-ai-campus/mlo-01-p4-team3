"""템플릿에서 공개 ID와 화면 문구만 변환하는 도우미."""
from django import template
from .assets import asset_url

register = template.Library()
register.simple_tag(asset_url)


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
def action_icon(kind):
    """UI manifest에 공통 아이콘이 있는 행동의 ID를 고른다."""
    return f"ui:{kind}" if kind in {"suspect", "defend", "interrogate", "vote"} else ""
