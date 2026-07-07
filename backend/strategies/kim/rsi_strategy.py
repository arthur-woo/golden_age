from decimal import Decimal
from typing import List, Optional

from apps.stock.models import Stock
from apps.market.models import Candle, RegimeSnapshot
from core.strategy.base import BaseStrategy, StrategyResult


class KimRsiStrategy(BaseStrategy):
    """
    Kim의 RSI 기반 과매수/과매도 전략
    """

    def run(
        self,
        stock: Stock,
        candles: List[Candle],
        regime_snapshot: Optional[RegimeSnapshot] = None,
    ) -> StrategyResult:
        period = int(self.config.get("period", 14))
        oversold = Decimal(str(self.config.get("oversold_threshold", "30")))
        overbought = Decimal(str(self.config.get("overbought_threshold", "70")))

        # RSI 14를 계산하려면 최소 15개 이상의 캔들이 필요함
        if len(candles) < period + 1:
            return StrategyResult(
                action="HOLD",
                confidence_score=Decimal("0.0"),
                reason=f"데이터 부족 (필요: {period + 1}, 실제: {len(candles)})"
            )

        # 최신 -> 과거 순으로 되어 있으므로, 계산을 위해 과거 -> 최신 순으로 뒤집음
        history = list(reversed(candles[:period + 1]))

        gains = []
        losses = []

        for i in range(len(history) - 1):
            change = history[i + 1].close_price - history[i].close_price
            if change > 0:
                gains.append(change)
                losses.append(Decimal("0.0"))
            else:
                gains.append(Decimal("0.0"))
                losses.append(-change)

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            rsi = Decimal("100.0")
        else:
            rs = avg_gain / avg_loss
            rsi = Decimal("100.0") - (Decimal("100.0") / (Decimal("1.0") + rs))

        if rsi <= oversold:
            # 과매도 구간 -> 매수
            confidence = Decimal("0.7")
            return StrategyResult(
                action="BUY",
                confidence_score=confidence,
                reason=f"과매도 도달 (RSI: {rsi:.2f} <= {oversold})"
            )
        elif rsi >= overbought:
            # 과매도 구간 -> 매도
            confidence = Decimal("0.7")
            return StrategyResult(
                action="SELL",
                confidence_score=confidence,
                reason=f"과매수 도달 (RSI: {rsi:.2f} >= {overbought})"
            )
        else:
            return StrategyResult(
                action="HOLD",
                confidence_score=Decimal("0.0"),
                reason=f"중립 구간 (RSI: {rsi:.2f})"
            )
