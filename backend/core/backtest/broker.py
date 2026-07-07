"""
백테스트용 시뮬레이션 브로커 (Phase 5-3)

core.broker.BaseBroker 인터페이스를 구현하여, 향후 trader_executor 를
실거래와 동일한 코드 경로로 백테스트 구동할 수 있도록 한다(LiveBroker ↔ BacktestBroker 교체).

체결은 core.backtest.costs.estimate_fill 로 슬리피지/수수료/세금을 반영한다.
현재가는 외부 클럭(백테스트 루프)이 set_market 으로 주입한다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from core.broker.dtos import AccountBalanceDTO, OrderResultDTO, PriceDTO
from core.broker.interfaces import BaseBroker
from core.backtest.costs import (
    SIDE_BUY,
    SIDE_SELL,
    CostConfig,
    estimate_fill,
)


class BacktestBroker(BaseBroker):
    """단일 계좌 시뮬레이션 브로커."""

    def __init__(self, initial_cash: Decimal, cost_config: Optional[CostConfig] = None):
        self.cash = Decimal(str(initial_cash))
        self.positions: dict[str, Decimal] = {}  # symbol -> 수량
        self.cost = cost_config or CostConfig()
        self._market: dict[
            str, tuple[Decimal, Decimal]
        ] = {}  # symbol -> (price, volume)
        self._order_seq = 0

    # --- 클럭 주입 ---
    def set_market(
        self, symbol: str, price: Decimal, volume: Decimal = Decimal("0")
    ) -> None:
        """현재 봉의 시세를 주입한다(백테스트 루프가 매 봉 호출)."""
        self._market[symbol] = (Decimal(str(price)), Decimal(str(volume)))

    # --- BaseBroker 구현 ---
    def get_current_price(self, symbol: str) -> PriceDTO:
        price, volume = self._market[symbol]
        return PriceDTO(symbol=symbol, price=price, volume=volume, raw_payload={})

    def get_balance(self) -> AccountBalanceDTO:
        holdings_value = Decimal("0")
        for symbol, qty in self.positions.items():
            price, _ = self._market.get(symbol, (Decimal("0"), Decimal("0")))
            holdings_value += qty * price
        total = self.cash + holdings_value
        return AccountBalanceDTO(
            cash_balance=self.cash,
            total_asset_value=total,
            raw_payload={"positions": {s: str(q) for s, q in self.positions.items()}},
        )

    def create_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Optional[Decimal] = None,
    ) -> OrderResultDTO:
        quantity = Decimal(str(quantity))
        ref_price, volume = self._market[symbol]
        fill = estimate_fill(side, ref_price, quantity, volume, self.cost)
        fees = fill.commission + fill.tax

        if side == SIDE_BUY:
            required = fill.gross_amount + fees
            if required > self.cash:
                return self._reject("잔고 부족")
            self.cash -= required
            self.positions[symbol] = self.positions.get(symbol, Decimal("0")) + quantity
        elif side == SIDE_SELL:
            held = self.positions.get(symbol, Decimal("0"))
            if quantity > held:
                return self._reject("보유 수량 부족")
            self.cash += fill.gross_amount - fees
            self.positions[symbol] = held - quantity
        else:
            return self._reject(f"알 수 없는 주문 방향: {side}")

        self._order_seq += 1
        return OrderResultDTO(
            success=True,
            order_id=f"BT-{self._order_seq}",
            error_message=None,
            raw_payload={
                "fill_price": str(fill.fill_price),
                "commission": str(fill.commission),
                "tax": str(fill.tax),
                "slippage_cost": str(fill.slippage_cost),
            },
        )

    def _reject(self, message: str) -> OrderResultDTO:
        return OrderResultDTO(
            success=False, order_id=None, error_message=message, raw_payload={}
        )
