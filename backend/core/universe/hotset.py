"""
실시간 수집 hot set 선정 (수집#2)

수집 유니버스(COLLECTION) 중 실시간 WebSocket으로 스트리밍할 소수 종목을 고른다.
WS는 세션당 등록 종목 수 제한이 있으므로 유동성 상위 top_k만 hot으로 삼고,
나머지 broad는 REST 백필 배치(수집#3)로 채운다.
"""

from __future__ import annotations

from typing import Optional

from apps.market.models import Candle
from apps.stock.models import Stock, get_collection_stock_ids


def select_hot_symbols(
    top_k: int = 60,
    at=None,
    timeframe: str = Candle.Timeframe.MIN_1,
) -> list[str]:
    """
    수집 유니버스에서 최근 거래대금(latest close*volume) 상위 top_k 종목코드를 반환한다.

    캔들이 없는 종목은 제외한다.
    """
    ids = get_collection_stock_ids(at)
    scored: list[tuple[float, str]] = []
    for stock in Stock.objects.filter(id__in=ids):
        latest = (
            Candle.objects.filter(stock=stock, timeframe=timeframe)
            .order_by("-opened_at")
            .first()
        )
        if latest is None:
            continue
        turnover = float(latest.close_price) * float(latest.volume)
        scored.append((turnover, stock.symbol))
    scored.sort(reverse=True)
    return [sym for _, sym in scored[:top_k]]
