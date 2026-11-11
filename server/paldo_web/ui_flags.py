"""템플릿에 넘기는 화면 표시 설정. 판단은 설정값 그대로이며 규칙은 두지 않는다."""
from django.conf import settings


def ui_flags(request):
    """직접 입력창 표시 여부(`PALDO_FREE_TEXT_INPUT`)를 모든 템플릿에 `free_text_input`으로 준다."""
    return {"free_text_input": settings.FREE_TEXT_INPUT}
