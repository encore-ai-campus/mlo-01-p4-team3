"""로그인한 관리자만 캠페인·소재·예산을 편집한다."""
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.views.decorators.http import require_GET

from ads.app.reporting import build_report
from ads.infra.reporting import SEOUL, read_report_rows

from .models import Campaign, Creative


admin.site.site_title = "창귀 광고 관리"
admin.site.site_header = "창귀 광고 관리"
admin.site.index_title = "광고 운영"
admin.site.site_url = None


class CampaignForm(forms.ModelForm):
    """검증된 캠페인 입력에 한국어 이름·단위·지면 안내를 붙인다."""

    class Meta:
        model = Campaign
        fields = "__all__"
        labels = {
            "campaign_id": "캠페인 ID", "advertiser_id": "광고주 ID", "name": "캠페인 이름",
            "slots": "노출 지면", "billing": "과금 방식", "max_bid_mw": "최대 입찰가 (밀리원)",
            "daily_budget_mw": "일 예산 (밀리원)", "target_cpc_mw": "목표 CPC (밀리원)",
            "min_bid_mw": "입찰 하한 (밀리원)", "targeting": "타기팅",
            "start_date": "시작일", "end_date": "종료일", "status": "상태",
        }
        help_texts = {
            "slots": 'JSON 목록으로 입력하세요. 예시 ["lobby_banner", "result_banner"]. '
                     '로비(lobby_banner)·결과(result_banner)는 728×90, 복기(review_side)는 300×250입니다.',
            "max_bid_mw": "CPC는 클릭 1회당, CPM은 노출 1,000회당 입찰 상한입니다. 1원 = 1,000밀리원.",
            "target_cpc_mw": "CPC 자동 입찰을 사용할 때만 입력하세요.",
            "targeting": "제한이 없으면 {}를 입력하세요. 사용 가능한 키는 preset_ids, outcomes, returning, hours입니다.",
        }
        widgets = {"slots": forms.Textarea(attrs={"rows": 2}), "targeting": forms.Textarea(attrs={"rows": 3})}


class CreativeForm(forms.ModelForm):
    """소재 기본 경로와 지면별 이미지 규격을 안내한다."""

    class Meta:
        model = Creative
        fields = "__all__"
        labels = {"creative_id": "소재 ID", "campaign": "캠페인", "title": "제목", "body": "본문",
                  "image_path": "이미지 기본 경로"}
        help_texts = {
            "image_path": "배포된 파일의 기본 경로를 입력하세요. 예시 /static/promo/bujeok.png. "
                          "728×90 파일은 __wide, 300×250 파일은 __box가 자동으로 붙습니다.",
        }
        widgets = {"body": forms.Textarea(attrs={"rows": 3})}


class CreativeInline(admin.StackedInline):
    """캠페인 작성 화면에서 소재를 함께 등록한다."""
    model = Creative
    form = CreativeForm
    verbose_name = "광고 소재"
    verbose_name_plural = "광고 소재"
    extra = 1


class OperationsForm(forms.Form):
    """운영 조회의 기간·트래픽 출처·캠페인 입력을 검증한다."""

    start_day = forms.DateField(label="시작 귀속일", widget=forms.DateInput(attrs={"type": "date"}))
    end_day = forms.DateField(label="종료 귀속일", widget=forms.DateInput(attrs={"type": "date"}))
    source = forms.ChoiceField(label="트래픽", choices=[("human", "사람"), ("synthetic", "합성")])
    campaign_id = forms.CharField(label="캠페인 ID", required=False, max_length=100)

    def clean(self):
        """역전된 기간과 다음 날 경계를 계산할 수 없는 종료일을 거절한다."""
        values = super().clean()
        start, end = values.get("start_day"), values.get("end_day")
        if start and end and start > end:
            self.add_error("end_day", "종료 귀속일은 시작 귀속일 이후여야 합니다.")
        if end and end.year == 9999 and end.month == 12 and end.day == 31:
            self.add_error("end_day", "종료 귀속일은 9999-12-30까지 조회할 수 있습니다.")
        return values


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """캠페인의 일 예산과 상태를 관리한다."""
    form = CampaignForm
    list_display = ("campaign_id", "name", "billing", "daily_budget_mw", "status")
    list_filter = ("status", "billing")
    search_fields = ("campaign_id", "name", "advertiser_id")
    fieldsets = (
        ("캠페인 정보", {"fields": ("name", "campaign_id", "advertiser_id", "status")}),
        ("노출 지면 및 기간", {"fields": ("slots", "start_date", "end_date", "targeting")}),
        ("예산 및 입찰", {"fields": ("billing", "daily_budget_mw", "max_bid_mw", "target_cpc_mw", "min_bid_mw")}),
    )
    inlines = [CreativeInline]

    def get_urls(self):
        """기존 관리자 인증·CSRF로 보호되는 운영 조회 경로를 추가한다."""
        return [path("operations/", self.admin_site.admin_view(require_GET(self.operations)),
                     name="ads_web_campaign_operations")] + super().get_urls()

    def operations(self, request):
        """캠페인 조회 권한이 있는 운영자에게 원장·판정 합계를 표시한다."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        today = timezone.localdate(timezone=SEOUL)
        form = OperationsForm(request.GET or {"start_day": today, "end_day": today, "source": "human"})
        rows = build_report(read_report_rows, **form.cleaned_data) if form.is_valid() else []
        context = self.admin_site.each_context(request) | {
            "title": "광고 운영 조회", "form": form, "rows": rows,
            "as_of": timezone.now().astimezone(SEOUL), "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/ads_web/operations.html", context)


@admin.register(Creative)
class CreativeAdmin(admin.ModelAdmin):
    """소재 내용과 기본 이미지 경로를 관리한다."""
    form = CreativeForm
    list_display = ("creative_id", "campaign", "title", "image_path")
    search_fields = ("creative_id", "title", "campaign__name")
