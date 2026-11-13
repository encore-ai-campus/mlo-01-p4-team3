"""인증된 검토 화면에서 현재 공식 manifest의 이미지 상태만 보여 준다."""
import json
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET


@login_required
@require_GET
def ui_review(request):
    """등록 그림과 임시 배경을 구분하며 게임 상태를 바꾸지 않는다."""
    if settings.GAME_UI != 'vn':
        raise Http404
    manifest = json.loads(settings.ASSET_MANIFEST.read_text(encoding='utf-8'))
    return render(request, 'ui_review_index.html', {
        'official_assets': [item for item in manifest if not item['placeholder']],
        'backgrounds': [item for item in manifest if item['kind'] == 'background'],
        'placeholder_count': sum(item['placeholder'] for item in manifest)})
