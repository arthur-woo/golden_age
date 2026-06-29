from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Candle, RegimeSnapshot, FeatureSnapshot

@admin.register(Candle)
class CandleAdmin(ModelAdmin):
    list_display = ("stock", "timeframe", "opened_at", "close_price", "volume")
    list_filter = ("timeframe", "stock")
    search_fields = ("stock__symbol",)

@admin.register(RegimeSnapshot)
class RegimeSnapshotAdmin(ModelAdmin):
    list_display = ("stock", "regime", "confidence_score", "analyzed_at")
    list_filter = ("regime", "stock")

@admin.register(FeatureSnapshot)
class FeatureSnapshotAdmin(ModelAdmin):
    list_display = ("stock", "timeframe", "captured_at")
    list_filter = ("timeframe", "stock")
