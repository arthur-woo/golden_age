from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Stock, UniverseMembership


@admin.register(Stock)
class StockAdmin(ModelAdmin):
    list_display = ("symbol", "name", "market", "currency", "is_active", "created_at")
    list_filter = ("market", "currency", "is_active")
    search_fields = ("symbol", "name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UniverseMembership)
class UniverseMembershipAdmin(ModelAdmin):
    list_display = ("universe", "stock", "effective_from", "effective_to", "created_at")
    list_filter = ("universe",)
    search_fields = ("stock__symbol", "stock__name")
    readonly_fields = ("created_at",)
    list_select_related = ("stock",)
