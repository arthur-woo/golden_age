"""
Market Analyzer

현재 시장 국면(Regime)을 분석하고, Trader/Strategy에 전달할 파라미터를 조정한다.

시장 상태:
- BULL: 상승장
- SIDEWAYS: 횡보장
- BEAR: 하락장

MVP에서는 단순 이동평균선 기반으로 판단한다.
"""

import logging
from decimal import Decimal
from django.utils import timezone

from apps.market.models import Candle, RegimeSnapshot
from apps.stock.models import Stock

logger = logging.getLogger(__name__)

# MVP 기본 파라미터 프리셋
REGIME_PARAMS = {
    RegimeSnapshot.Regime.BULL: {
        "position_size_multiplier": Decimal("1.0"),
        "entry_threshold_offset": Decimal("-0.05"),
        "stop_loss_multiplier": Decimal("1.2"),
        "take_profit_multiplier": Decimal("1.0"),
    },
    RegimeSnapshot.Regime.SIDEWAYS: {
        "position_size_multiplier": Decimal("0.7"),
        "entry_threshold_offset": Decimal("0.0"),
        "stop_loss_multiplier": Decimal("1.0"),
        "take_profit_multiplier": Decimal("1.0"),
    },
    RegimeSnapshot.Regime.BEAR: {
        "position_size_multiplier": Decimal("0.3"),
        "entry_threshold_offset": Decimal("0.10"),
        "stop_loss_multiplier": Decimal("0.8"),
        "take_profit_multiplier": Decimal("1.5"),
    },
}


def analyze_regime(stock: Stock, lookback: int = 20) -> RegimeSnapshot:
    """
    단순 이동평균선 기반으로 시장 국면을 판단한다.

    MVP 로직:
    - 최근 lookback개 일봉의 종가 이동평균을 구한다.
    - 현재가 > SMA: BULL
    - 현재가 < SMA * 0.98: BEAR
    - 그 외: SIDEWAYS

    Returns:
        RegimeSnapshot: 생성된 스냅샷 (DB에 저장됨)
    """
    candles = (
        Candle.objects
        .filter(stock=stock, timeframe=Candle.Timeframe.DAY_1)
        .order_by("-opened_at")[:lookback]
    )

    candle_list = list(candles)
    if len(candle_list) < lookback:
        logger.warning(
            "캔들 데이터 부족 (필요: %d, 실제: %d). SIDEWAYS로 판단합니다.",
            lookback, len(candle_list)
        )
        regime = RegimeSnapshot.Regime.SIDEWAYS
        confidence = Decimal("0.3")
        reason = f"캔들 데이터 부족 ({len(candle_list)}/{lookback})"
    else:
        closes = [c.close_price for c in candle_list]
        sma = sum(closes) / len(closes)
        current_price = closes[0]  # 가장 최근 종가

        if current_price > sma:
            regime = RegimeSnapshot.Regime.BULL
            diff_ratio = (current_price - sma) / sma
            confidence = min(Decimal("0.9"), Decimal("0.5") + diff_ratio)
            reason = f"현재가({current_price}) > SMA{lookback}({sma:.2f})"
        elif current_price < sma * Decimal("0.98"):
            regime = RegimeSnapshot.Regime.BEAR
            diff_ratio = (sma - current_price) / sma
            confidence = min(Decimal("0.9"), Decimal("0.5") + diff_ratio)
            reason = f"현재가({current_price}) < SMA{lookback}({sma:.2f}) * 0.98"
        else:
            regime = RegimeSnapshot.Regime.SIDEWAYS
            confidence = Decimal("0.5")
            reason = f"현재가({current_price}) ≈ SMA{lookback}({sma:.2f})"

    params = REGIME_PARAMS[regime]

    snapshot = RegimeSnapshot.objects.create(
        stock=stock,
        regime=regime,
        confidence_score=confidence,
        parameter_payload={k: str(v) for k, v in params.items()},
        reason=reason,
        analyzed_at=timezone.now(),
    )

    logger.info("Market regime for %s: %s (confidence: %s)", stock.symbol, regime, confidence)
    return snapshot
