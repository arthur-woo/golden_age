from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Strategy, StrategyVersion, Trader, TraderStrategy


class TraderStrategyInline(TabularInline):
    model = TraderStrategy
    extra = 0
    fields = ("slot", "strategy_version", "weight", "is_active", "config_payload")
    readonly_fields = ()


@admin.register(Trader)
class TraderAdmin(ModelAdmin):
    list_display = ("name", "account", "status", "ml_filter_enabled", "created_at")
    list_filter = ("status", "ml_filter_enabled", "account__broker")
    search_fields = ("name", "code", "account__name")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("account",)
    inlines = [TraderStrategyInline]

    fieldsets = (
        ("기본 정보", {
            "fields": ("account", "name", "code", "status"),
        }),
        ("매매 파라미터", {
            "fields": (
                "position_size_ratio", "entry_threshold",
                "stop_loss_ratio", "take_profit_ratio", "max_exposure_ratio",
            ),
        }),
        ("ML", {
            "fields": ("ml_filter_enabled",),
        }),
        ("추가 설정", {
            "fields": ("config_payload",),
            "classes": ("collapse",),
        }),
        ("이력", {
            "fields": ("created_at", "updated_at", "deleted_at"),
        }),
    )


class StrategyVersionInline(TabularInline):
    model = StrategyVersion
    extra = 0
    fields = ("version", "module_path", "class_name", "status")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Strategy)
class StrategyAdmin(ModelAdmin):
    list_display = ("namespace", "code", "name", "owner", "is_active", "created_at")
    list_filter = ("namespace", "is_active")
    search_fields = ("namespace", "code", "name", "owner__username")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("owner",)
    inlines = [StrategyVersionInline]


@admin.register(StrategyVersion)
class StrategyVersionAdmin(ModelAdmin):
    list_display = ("strategy", "version", "module_path", "class_name", "status", "created_at")
    list_filter = ("status", "strategy__namespace")
    search_fields = ("strategy__code", "version", "module_path")
    readonly_fields = ("created_at",)
    list_select_related = ("strategy",)
