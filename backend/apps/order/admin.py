from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Order, OrderEvent, TradeExecution

class OrderEventInline(TabularInline):
    model = OrderEvent
    extra = 0

class TradeExecutionInline(TabularInline):
    model = TradeExecution
    extra = 0

@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = ("id", "account", "stock", "side", "order_type", "status", "requested_at")
    list_filter = ("status", "side", "order_type", "account")
    search_fields = ("stock__symbol", "broker_order_id")
    inlines = [OrderEventInline, TradeExecutionInline]

@admin.register(TradeExecution)
class TradeExecutionAdmin(ModelAdmin):
    list_display = ("id", "account", "stock", "side", "executed_quantity", "executed_price", "executed_at")
    list_filter = ("side", "account", "stock")
    search_fields = ("stock__symbol", "broker_execution_id")
