from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Stock


@admin.register(Stock)
class StockAdmin(ModelAdmin):
    list_display = ("symbol", "name", "market", "currency", "is_active", "created_at")
    list_filter = ("market", "currency", "is_active")
    search_fields = ("symbol", "name")
    readonly_fields = ("created_at", "updated_at")
