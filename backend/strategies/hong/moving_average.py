from decimal import Decimal
from typing import List, Optional

from apps.stock.models import Stock
from apps.market.models import Candle, RegimeSnapshot
from core.strategy.base import BaseStrategy, StrategyResult


class HongMovingAverageStrategy(BaseStrategy):
    """
    Hong의 단순 이동평균 크로스 전략 (Golden/Dead Cross)
    """

    def run(
        self,
        stock: Stock,
        candles: List[Candle],
        regime_snapshot: Optional[RegimeSnapshot] = None,
    ) -> StrategyResult:
        fast_period = int(self.config.get("fast_period", 5))
        slow_period = int(self.config.get("slow_period", 20))

        if len(candles) < slow_period:
            return StrategyResult(
                action="HOLD",
                confidence_score=Decimal("0.0"),
                reason=f"데이터 부족 (필요: {slow_period}, 실제: {len(candles)})"
            )

        # 캔들은 최신순으로 정렬되어 들어온다고 가정 (정렬 방향: 최신 -> 과거)
        # candles[0] 이 가장 최신 캔들
        closes = [c.close_price for c in candles]

        fast_ma = sum(closes[:fast_period]) / fast_period
        slow_ma = sum(closes[:slow_period]) / slow_period

        # 이전 MA 값 (크로스 감지용)
        prev_fast_ma = sum(closes[1:fast_period + 1]) / fast_period
        prev_slow_ma = sum(closes[1:slow_period + 1]) / slow_period

        if fast_ma > slow_ma and prev_fast_ma <= prev_slow_ma:
            # 골든 크로스 발생 -> 매수
            confidence = Decimal("0.8")
            return StrategyResult(
                action="BUY",
                confidence_score=confidence,
                reason=f"골든 크로스 감지 (Fast MA: {fast_ma:.2f} > Slow MA: {slow_ma:.2f})"
            )
        elif fast_ma < slow_ma and prev_fast_ma >= prev_slow_ma:
            # 데드 크로스 발생 -> 매도
            confidence = Decimal("0.8")
            return StrategyResult(
                action="SELL",
                confidence_score=confidence,
                reason=f"데드 크로스 감지 (Fast MA: {fast_ma:.2f} < Slow MA: {slow_ma:.2f})"
            )
        else:
            return StrategyResult(
                action="HOLD",
                confidence_score=Decimal("0.0"),
                reason=f"추세 없음 (Fast MA: {fast_ma:.2f}, Slow MA: {slow_ma:.2f})"
            )
