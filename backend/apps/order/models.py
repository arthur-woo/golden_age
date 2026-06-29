"""
Order 도메인 모델

ord_order            : 주문 요청 및 현재 상태
ord_order_event      : 주문 상태 변경 이벤트 이력 (Append Only)
ord_trade_execution  : 실제 체결 결과
"""

from django.db import models
from apps.account.models import Account
from apps.stock.models import Stock

# 순환 참조 방지를 위해 trading 모듈은 지연(lazy) 임포트 권장, 혹은 문자열 참조 활용
# 여기서는 ForeignKey 쪽에 문자열("trading.DecisionLog")로 참조할 예정.


class Order(models.Model):
    """
    주문 정보 및 현재 상태 (ord_order)
    """
    class Side(models.TextChoices):
        BUY = "BUY", "매수"
        SELL = "SELL", "매도"

    class OrderType(models.TextChoices):
        MARKET = "MARKET", "시장가"
        LIMIT = "LIMIT", "지정가"

    class Status(models.TextChoices):
        PENDING = "PENDING", "대기중"
        ACCEPTED = "ACCEPTED", "접수완료"
        PARTIALLY_FILLED = "PARTIALLY_FILLED", "부분체결"
        FILLED = "FILLED", "전체체결"
        CANCELED = "CANCELED", "취소됨"
        REJECTED = "REJECTED", "거절됨"

    trader_decision_log = models.ForeignKey(
        "trading.DecisionLog", on_delete=models.PROTECT, related_name="orders",
        help_text="주문 생성의 원인이 된 최종 판단"
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="orders")
    stock = models.ForeignKey(Stock, on_delete=models.PROTECT, related_name="orders")
    side = models.CharField(max_length=8, choices=Side.choices)
    order_type = models.CharField(max_length=16, choices=OrderType.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=8, help_text="주문 수량")
    limit_price = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True, help_text="지정가")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    broker_order_id = models.CharField(max_length=100, null=True, blank=True, help_text="Broker 주문번호")
    request_payload = models.JSONField(default=dict, blank=True, help_text="주문 요청 원본")
    response_payload = models.JSONField(default=dict, blank=True, help_text="주문 응답 원본")
    requested_at = models.DateTimeField(help_text="요청 시각")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ord_order"
        verbose_name = "주문"
        verbose_name_plural = "주문 목록"
        indexes = [
            models.Index(fields=["account", "-requested_at"], name="ord_order_acc_req_idx"),
            models.Index(fields=["stock", "-requested_at"], name="ord_order_stk_req_idx"),
            models.Index(fields=["broker_order_id"], name="ord_order_broker_id_idx"),
            models.Index(fields=["status", "-requested_at"], name="ord_order_sts_req_idx"),
        ]

    def __str__(self):
        return f"Order {self.id} [{self.side}] {self.stock.symbol} ({self.status})"


class OrderEvent(models.Model):
    """
    주문 상태 변경 이벤트 (ord_order_event)
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=32, choices=Order.Status.choices)
    broker_status = models.CharField(max_length=64, blank=True, help_text="Broker 원본 상태")
    event_payload = models.JSONField(default=dict, blank=True, help_text="원본 이벤트")
    occurred_at = models.DateTimeField(help_text="이벤트 발생 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ord_order_event"
        verbose_name = "주문 이벤트"
        verbose_name_plural = "주문 이벤트 목록"
        indexes = [
            models.Index(fields=["order", "occurred_at"], name="ord_event_ord_occ_idx"),
            models.Index(fields=["event_type", "-occurred_at"], name="ord_event_type_occ_idx"),
        ]

    def __str__(self):
        return f"Event {self.event_type} for Order {self.order_id}"


class TradeExecution(models.Model):
    """
    실제 체결 결과 (ord_trade_execution)
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="executions")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="executions")
    stock = models.ForeignKey(Stock, on_delete=models.PROTECT, related_name="executions")
    side = models.CharField(max_length=8, choices=Order.Side.choices)
    executed_quantity = models.DecimalField(max_digits=18, decimal_places=8, help_text="체결 수량")
    executed_price = models.DecimalField(max_digits=18, decimal_places=2, help_text="체결 가격")
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="수수료")
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="세금")
    slippage_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, help_text="슬리피지")
    broker_execution_id = models.CharField(max_length=100, null=True, blank=True, help_text="Broker 체결번호")
    executed_at = models.DateTimeField(help_text="체결 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ord_trade_execution"
        verbose_name = "체결"
        verbose_name_plural = "체결 목록"
        indexes = [
            models.Index(fields=["account", "-executed_at"], name="ord_exec_acc_exec_idx"),
            models.Index(fields=["stock", "-executed_at"], name="ord_exec_stk_exec_idx"),
            models.Index(fields=["order"], name="ord_exec_order_idx"),
            models.Index(fields=["broker_execution_id"], name="ord_exec_broker_id_idx"),
        ]

    def __str__(self):
        return f"Execution {self.id} [{self.side}] {self.stock.symbol} @ {self.executed_price}"
