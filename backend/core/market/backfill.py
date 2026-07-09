"""
REST 분봉 백필 서비스 (수집#3)

KIS 분봉 조회 API로 여러 종목의 1분봉을 mkt_candle에 멱등 적재한다.
broad 수집 유니버스를 스케줄 배치로 채우는 용도(실시간 WS 한도 회피, 구멍 메우기).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from apps.market.models import Candle
from core.broker.kis.broker import KST
from core.market.aggregator import OHLCV
from core.market.ingest import persist_ohlcv

logger = logging.getLogger(__name__)


def backfill_symbol(
    broker,
    stock,
    pages: int = 1,
    source: str = "kis_rest",
    timeframe: str = Candle.Timeframe.MIN_1,
) -> int:
    """단일 종목 분봉을 시간 역순으로 pages회 조회해 적재한다. 신규 적재 봉 수 반환."""
    to_time = ""
    earliest_seen = None
    created = 0
    for _ in range(pages):
        candles = broker.get_minute_candles(stock.symbol, to_time)
        if not candles:
            break
        for c in candles:
            _, was_created = persist_ohlcv(
                stock,
                OHLCV(
                    opened_at=c["opened_at"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c["volume"],
                    trade_count=0,
                ),
                source=source,
                timeframe=timeframe,
            )
            created += int(was_created)

        earliest = candles[0]["opened_at"]  # 오름차순 → [0]이 가장 과거
        if earliest == earliest_seen:
            break
        earliest_seen = earliest
        to_time = (earliest.astimezone(KST) - timedelta(minutes=1)).strftime("%H%M%S")
    return created


def backfill_universe(
    account,
    stocks,
    pages: int = 1,
    source: str = "kis_rest",
    timeframe: str = Candle.Timeframe.MIN_1,
) -> dict:
    """여러 종목을 순회하며 분봉을 백필한다. 종목 단위 실패는 건너뛴다."""
    from apps.account.services import get_broker_for_account

    broker = get_broker_for_account(account)
    total_created = 0
    done = 0
    for stock in stocks:
        try:
            total_created += backfill_symbol(broker, stock, pages, source, timeframe)
            done += 1
        except Exception as e:  # noqa: BLE001 - 종목 단위 격리
            logger.warning("백필 실패 (%s): %s", stock.symbol, e)
    return {"stocks": done, "created": total_created}
