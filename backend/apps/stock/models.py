"""
Stock 도메인 모델

stk_stock              : 거래 가능 종목
stk_universe_membership: 지수/유니버스 구성종목의 시점별(point-in-time) 편입 이력
"""

from django.db import models
from django.utils import timezone


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
    currency = models.CharField(
        max_length=8, choices=Currency.choices, default=Currency.KRW
    )
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
            models.Index(
                fields=["market", "is_active"], name="stk_stock_market_active_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.market}] {self.name} ({self.symbol})"


class UniverseMembership(models.Model):
    """
    유니버스 구성종목 편입 이력 (stk_universe_membership)

    KOSPI200 등 지수 구성종목은 정기 리밸런싱으로 편입/편출된다.
    백테스트의 생존편향(survivorship bias)을 제거하려면 특정 시점에
    어떤 종목이 유니버스에 있었는지 point-in-time으로 재현할 수 있어야 한다.

    구간 표현: [effective_from, effective_to) (effective_to=None 이면 현재 편입 중)
    """

    class Universe(models.TextChoices):
        KOSPI200 = "KOSPI200", "코스피200"
        KOSDAQ150 = "KOSDAQ150", "코스닥150"
        COLLECTION = "COLLECTION", "수집대상"  # 매매와 분리된 상시 수집 유니버스(넓은 상위집합)
        CUSTOM = "CUSTOM", "사용자정의"

    universe = models.CharField(max_length=32, choices=Universe.choices)
    stock = models.ForeignKey(
        Stock, on_delete=models.CASCADE, related_name="universe_memberships"
    )
    effective_from = models.DateTimeField(help_text="편입 시각 (구간 시작, 포함)")
    effective_to = models.DateTimeField(
        null=True, blank=True, help_text="편출 시각 (구간 끝, 미포함). None이면 현재 편입 중"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stk_universe_membership"
        verbose_name = "유니버스 편입 이력"
        verbose_name_plural = "유니버스 편입 이력 목록"
        indexes = [
            models.Index(
                fields=["universe", "stock", "-effective_from"],
                name="stk_univ_mem_uni_stk_idx",
            ),
            models.Index(
                fields=["universe", "effective_from", "effective_to"],
                name="stk_univ_mem_uni_range_idx",
            ),
        ]

    def __str__(self) -> str:
        end = self.effective_to or "현재"
        return f"{self.universe} · {self.stock.symbol} [{self.effective_from} ~ {end}]"


def get_universe_stock_ids(universe: str, at=None) -> list[int]:
    """
    특정 시점(at)에 유니버스에 편입되어 있던 종목 id 목록을 반환한다(point-in-time).

    at 이 None 이면 현재 시각을 사용한다.
    """
    at = at or timezone.now()
    qs = UniverseMembership.objects.filter(
        universe=universe,
        effective_from__lte=at,
    ).filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=at))
    return list(qs.values_list("stock_id", flat=True).distinct())


def get_collection_stock_ids(at=None) -> list[int]:
    """상시 수집 유니버스(COLLECTION)에 편입된 종목 id 목록(point-in-time)."""
    return get_universe_stock_ids(UniverseMembership.Universe.COLLECTION, at=at)


def rebalance_universe(universe: str, stocks, at=None) -> dict:
    """
    지수 유니버스(예: KOSPI200)를 주어진 구성종목으로 리밸런싱한다.

    목록에서 빠진 활성 종목은 편출(effective_to=at), 새로 든 종목은 편입한다.
    point-in-time 이력이 남아 백테스트 생존편향을 제거한다. {added, removed} 반환.
    """
    now = at or timezone.now()
    target_ids = {s.id for s in stocks}
    active = UniverseMembership.objects.filter(
        universe=universe, effective_to__isnull=True
    )
    active_ids = set(active.values_list("stock_id", flat=True))

    removed = active.exclude(stock_id__in=target_ids).update(effective_to=now)
    to_add = [s for s in stocks if s.id not in active_ids]
    UniverseMembership.objects.bulk_create(
        [
            UniverseMembership(universe=universe, stock=s, effective_from=now)
            for s in to_add
        ]
    )
    return {"added": len(to_add), "removed": removed}


def sync_collection_universe(stocks, effective_from=None) -> int:
    """
    주어진 종목들을 수집 유니버스(COLLECTION)에 활성 편입한다(멱등, append-only).

    이미 활성 편입된 종목은 건너뛴다. 편출(effective_to 설정)은 별도로 하지 않는다 —
    수집은 한 번 시작하면 계속(데이터 구멍 방지). 신규 추가 종목 수를 반환한다.
    """
    now = effective_from or timezone.now()
    active_ids = set(
        UniverseMembership.objects.filter(
            universe=UniverseMembership.Universe.COLLECTION,
            effective_to__isnull=True,
        ).values_list("stock_id", flat=True)
    )
    to_add = [s for s in stocks if s.id not in active_ids]
    UniverseMembership.objects.bulk_create(
        [
            UniverseMembership(
                universe=UniverseMembership.Universe.COLLECTION,
                stock=s,
                effective_from=now,
            )
            for s in to_add
        ]
    )
    return len(to_add)
