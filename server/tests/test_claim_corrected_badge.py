"""진술표 카드: 내부 진술 번호는 글로 보이지 않고, 정정 진술에만 '정정한 진술' 배지와 고치기 전 문장 설명(11/10 사용자 승인)."""
import re
from dataclasses import replace

from django.template.loader import render_to_string
from django.test import SimpleTestCase, override_settings

from server.api.meeting_view import meeting_context
from .support import make_service


@override_settings(GAME_UI="vn")
class ClaimCorrectedBadge(SimpleTestCase):
    """녹화 판 화면 정보에 정정 진술 하나를 끼워 진술표를 그린다(녹화 판에는 정정이 나오지 않는다)."""

    def view_with_correction(self, target_present=True):
        """세 번째 진술이 첫 진술(또는 화면에 없는 진술)을 고친 것으로 바꾼 화면 정보."""
        view = make_service().engine.views[1]
        claims = list(view.public.claims)
        target = claims[0].claim_id if target_present else "cl-not-shown"
        claims[2] = replace(claims[2], corrects=target)
        return view, replace(view, public=replace(view.public, claims=claims))

    def cards(self, view):
        """진술표 카드(li)별 HTML과 화면 글자."""
        html = render_to_string("meeting_parts/records.html", meeting_context(view))
        items = re.findall(r'<li data-record-speaker.*?</li>', html, re.S)
        return items, [re.sub(r"<[^>]+>", " ", item) for item in items]

    def test_claim_number_not_shown_as_text(self):
        """진술 번호·기록 상세는 화면 글자에 없다(동작용 속성은 유지)."""
        _, view = self.view_with_correction()
        items, texts = self.cards(view)
        self.assertTrue(items)
        for claim, item, text in zip(view.public.claims, items, texts):
            self.assertNotIn(claim.claim_id, text)
            self.assertIn(f'data-record-id="{claim.claim_id}"', item)
            self.assertNotIn("기록 상세", item)
            self.assertNotIn("진술 번호", item)

    def test_badge_only_on_corrected_claim_with_original_text(self):
        """정정 진술 카드에만 배지가 붙고, 마우스 올림 설명에 고친 대상 진술 문장이 있다."""
        _, view = self.view_with_correction()
        items, _ = self.cards(view)
        badged = [i for i, item in enumerate(items) if "정정한 진술" in item]
        self.assertEqual(badged, [2])
        original = meeting_context(view)["claims"][0].card.text
        title = re.search(r'class="claim-corrected" title="([^"]*)"', items[2]).group(1)
        self.assertIn(original.replace("'", "&#x27;").replace('"', "&quot;"), title)

    def test_badge_without_title_when_target_not_shown(self):
        """대상 진술이 화면 기록에 없으면 배지만 붙는다."""
        _, view = self.view_with_correction(target_present=False)
        items, _ = self.cards(view)
        self.assertRegex(items[2], r'<span class="claim-corrected">정정한 진술</span>')
