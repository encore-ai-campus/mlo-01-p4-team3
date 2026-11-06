"""템플릿에서 공개 ID와 화면 문구만 변환하는 도우미."""
from django import template
from .assets import asset_url

register = template.Library()
register.simple_tag(asset_url)

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
