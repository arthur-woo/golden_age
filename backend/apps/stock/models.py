"""
Stock 도메인 모델

stk_stock : 거래 가능 종목
"""

from django.db import models


class Stock(models.Model):
    """
    거래 가능 종목 (stk_stock)

    Strategy와 Trader가 거래할 수 있는 종목 목록.
    """

    class Market(models.TextChoices):
        KRX = "KRX", "KRX"
        KOSPI = "KOSPI", "KOSPI"
        KOSDAQ = "KOSDAQ", "KOSDAQ"

    class Currency(models.TextChoices):
        KRW = "KRW", "원화"

    market = models.CharField(max_length=32, choices=Market.choices)
    symbol = models.CharField(max_length=32)
    name = models.CharField(max_length=100)
    currency = models.CharField(max_length=8, choices=Currency.choices, default=Currency.KRW)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stk_stock"
        verbose_name = "종목"
        verbose_name_plural = "종목 목록"
        unique_together = [("market", "symbol")]
        indexes = [
            models.Index(fields=["symbol"], name="stk_stock_symbol_idx"),
            models.Index(fields=["market", "is_active"], name="stk_stock_market_active_idx"),
        ]

    def __str__(self) -> str:
        return f"[{self.market}] {self.name} ({self.symbol})"
