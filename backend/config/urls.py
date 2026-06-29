"""
Golden Age - URL 설정
"""

from django.contrib import admin
from django.urls import path

admin.site.site_header = "Golden Age 관리자"
admin.site.site_title = "Golden Age"
admin.site.index_title = "자동매매 플랫폼"

urlpatterns = [
    path("admin/", admin.site.urls),
]
