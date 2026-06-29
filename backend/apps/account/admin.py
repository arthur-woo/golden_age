from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Account, BrokerToken


@admin.register(Account)
class AccountAdmin(ModelAdmin):
    list_display = ("name", "user", "broker", "account_type", "account_number", "is_active", "created_at")
    list_filter = ("broker", "account_type", "is_active", "investment_profile")
    search_fields = ("name", "account_number", "user__username")
    readonly_fields = ("created_at", "updated_at")
    list_select_related = ("user",)

    fieldsets = (
        ("기본 정보", {
            "fields": ("user", "broker", "account_type", "account_number", "name", "investment_profile", "is_active"),
        }),
        ("API 인증 정보", {
            "fields": ("app_key_encrypted", "app_secret_encrypted"),
            "classes": ("collapse",),
            "description": "암호화된 값을 저장합니다.",
        }),
        ("이력", {
            "fields": ("created_at", "updated_at", "deleted_at"),
        }),
    )


@admin.register(BrokerToken)
class BrokerTokenAdmin(ModelAdmin):
    list_display = ("account", "token_type", "expires_at", "issued_at", "revoked_at", "is_valid")
    list_filter = ("token_type",)
    search_fields = ("account__name",)
    readonly_fields = ("created_at",)
    list_select_related = ("account",)

    @admin.display(description="유효 여부", boolean=True)
    def is_valid(self, obj: BrokerToken) -> bool:
        return obj.is_valid
