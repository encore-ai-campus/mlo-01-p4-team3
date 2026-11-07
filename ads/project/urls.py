"""광고 서비스 내부 HTTP 경로."""
from django.contrib import admin
from django.urls import path
from ads.web import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("ads/v1/serve", views.serve),
    path("ads/v1/impression", views.impression),
    path("ads/v1/click", views.click),
    path("ads/landing/<str:creative_id>", views.landing),
    path("metrics", views.metrics),
]
