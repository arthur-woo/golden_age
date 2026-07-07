"""
거래비용 · 슬리피지 모델 (Phase 5-1)

한국 시장(KRX)의 현실적 체결 비용을 계산하는 순수 함수 모음.
백테스트와 실거래 사후검증(TCA)이 동일 로직을 공유하도록 부작용 없이 설계한다.

구성 요소
- 호가단위(tick) 그리드 반올림
- 명시적 비용: 위탁수수료(양방향) + 매도 세금(증권거래세+농특세)
- 묵시적 비용: 스프레드 + 제곱근 시장충격 슬리피지

비용/세율은 연도·증권사·시장별로 변하므로 CostConfig로 주입한다.
기본값은 보수적인 근사치이며 운영 시 실측으로 캘리브레이션한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Optional

# 매수/매도 방향 상수 (apps.order.Order.Side 값과 동일)
SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

# KRX 호가단위 표 (2023.1 개편 반영). (상한가격_미만, tick)
# 예: 5,000 이상 20,000 미만 -> 10원 단위
KRX_TICK_TABLE: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("2000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("20000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("200000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
)
# 500,000원 이상
KRX_TICK_DEFAULT = Decimal("1000")


@dataclass(frozen=True)
class CostConfig:
    """비용/슬리피지 파라미터 (연도·증권사·시장별로 주입)."""

    commission_rate: Decimal = Decimal("0.00015")  # 위탁수수료 0.015% (양방향)
    sell_tax_rate: Decimal = Decimal("0.0018")  # 매도세 0.18% (증권거래세+농특세 근사)
    half_spread_ticks: Decimal = Decimal("1")  # 시장가 체결 시 부담하는 스프레드(틱 수)
    impact_coef: Decimal = Decimal("0.1")  # 제곱근 시장충격 계수 k


@dataclass(frozen=True)
class FillEstimate:
    """체결 추정 결과."""

    side: str
    ref_price: Decimal  # 기준가(신호 시점 가격)
    fill_price: Decimal  # 슬리피지 반영 체결가 (호가단위 정렬)
    quantity: Decimal
    slippage_cost: Decimal  # (fill - ref) * qty 의 절대값
    commission: Decimal
    tax: Decimal

    @property
    def gross_amount(self) -> Decimal:
        """체결 금액 (fill_price * quantity)."""
        return self.fill_price * self.quantity

    @property
    def total_cost(self) -> Decimal:
        """총 거래비용 (슬리피지 + 수수료 + 세금)."""
        return self.slippage_cost + self.commission + self.tax


def tick_size(price: Decimal) -> Decimal:
    """가격대에 해당하는 KRX 호가단위를 반환한다."""
    price = Decimal(price)
    for upper, tick in KRX_TICK_TABLE:
        if price < upper:
            return tick
    return KRX_TICK_DEFAULT


def round_to_tick(price: Decimal, rounding: str = ROUND_HALF_UP) -> Decimal:
    """가격을 유효한 호가단위 그리드로 정렬한다."""
    price = Decimal(price)
    tick = tick_size(price)
    steps = (price / tick).to_integral_value(rounding=rounding)
    return steps * tick


def transaction_cost(
    side: str,
    price: Decimal,
    quantity: Decimal,
    config: Optional[CostConfig] = None,
) -> tuple[Decimal, Decimal]:
    """
    명시적 비용을 계산한다.

    Returns:
        (commission, tax) 튜플. 세금은 매도 시에만 부과된다.
    """
    config = config or CostConfig()
    notional = Decimal(price) * Decimal(quantity)
    commission = notional * config.commission_rate
    tax = notional * config.sell_tax_rate if side == SIDE_SELL else Decimal("0")
    return commission, tax


def slippage_per_share(
    price: Decimal,
    quantity: Decimal,
    minute_volume: Optional[Decimal] = None,
    config: Optional[CostConfig] = None,
) -> Decimal:
    """
    주당 슬리피지(가격 열위분)를 계산한다.

    = 스프레드 성분(half_spread_ticks * tick) + 제곱근 시장충격 성분.
    분당 거래량 정보가 없거나 0이면 시장충격은 0으로 두고 스프레드만 반영한다.
    """
    config = config or CostConfig()
    price = Decimal(price)

    spread_component = config.half_spread_ticks * tick_size(price)

    impact_component = Decimal("0")
    if minute_volume and Decimal(minute_volume) > 0:
        ratio = Decimal(quantity) / Decimal(minute_volume)
        impact_component = config.impact_coef * price * ratio.sqrt()

    return spread_component + impact_component


def estimate_fill(
    side: str,
    ref_price: Decimal,
    quantity: Decimal,
    minute_volume: Optional[Decimal] = None,
    config: Optional[CostConfig] = None,
) -> FillEstimate:
    """
    기준가 대비 슬리피지·수수료·세금을 반영한 체결 추정치를 반환한다.

    매수는 기준가보다 불리(높게), 매도는 불리(낮게) 체결된다고 가정하고
    호가단위 그리드로 보수적으로 반올림한다(매수 올림, 매도 내림).
    """
    config = config or CostConfig()
    ref_price = Decimal(ref_price)
    quantity = Decimal(quantity)

    slip = slippage_per_share(ref_price, quantity, minute_volume, config)

    if side == SIDE_BUY:
        fill_price = round_to_tick(ref_price + slip, rounding=ROUND_UP)
    else:
        fill_price = round_to_tick(ref_price - slip, rounding=ROUND_DOWN)
        if fill_price < tick_size(ref_price):
            # 음수/0 방지: 최소 1틱
            fill_price = tick_size(ref_price)

    slippage_cost = abs(fill_price - ref_price) * quantity
    commission, tax = transaction_cost(side, fill_price, quantity, config)

    return FillEstimate(
        side=side,
        ref_price=ref_price,
        fill_price=fill_price,
        quantity=quantity,
        slippage_cost=slippage_cost,
        commission=commission,
        tax=tax,
    )


def round_trip_cost_ratio(
    price: Decimal,
    config: Optional[CostConfig] = None,
) -> Decimal:
    """
    슬리피지를 제외한 명시적 왕복 비용률(수수료 왕복 + 매도세)을 반환한다.

    라벨링/손익분기 임계치 계산에 사용한다. 슬리피지는 수량 의존이므로 제외한다.
    """
    config = config or CostConfig()
    return config.commission_rate * 2 + config.sell_tax_rate
