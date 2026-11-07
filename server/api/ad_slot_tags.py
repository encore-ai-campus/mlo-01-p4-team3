"""광고 계약의 지면 이름과 크기로 지연 로드 영역을 만든다."""
from django import template
from ads.domain.model import AdSlot, SLOT_SIZES

register = template.Library()


@register.inclusion_tag("ad_placeholder.html")
def ad_slot(name):
    """게임 명령 처리에서 광고를 호출하지 않고 GET 자리만 만든다."""
    width, height = SLOT_SIZES[AdSlot(name)]
    return {"slot": name, "width": width, "height": height}
