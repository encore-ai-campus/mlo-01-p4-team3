"""로그인한 관리자만 캠페인·소재·예산을 편집한다."""
from django.contrib import admin
from .models import Campaign, Creative


class CreativeInline(admin.TabularInline):
    """캠페인 작성 화면에서 소재를 함께 등록한다."""
    model = Creative
    extra = 1


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """캠페인의 일 예산과 상태를 관리한다."""
    list_display = ("campaign_id", "name", "billing", "daily_budget_mw", "status")
    list_filter = ("status", "billing")
    inlines = [CreativeInline]


@admin.register(Creative)
class CreativeAdmin(admin.ModelAdmin):
    """소재 내용과 기본 이미지 경로를 관리한다."""
    list_display = ("creative_id", "campaign", "title", "image_path")
