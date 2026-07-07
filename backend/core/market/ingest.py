"""
실시간 1분봉 수집/적재 (연결 #1)

KIS WebSocket 등 실시간 체결(틱) 스트림을 1분봉으로 집계하여 mkt_candle에 멱등 적재한다.

설계
- 집계는 core.market.aggregator(순수 함수)를 재사용한다.
- WebSocket I/O와 분리하기 위해 RealtimeCollector는 '틱 소스'를 주입받는다
  (테스트/리플레이/실 WS 어댑터 모두 동일 코드 경로).
- 적재는 (stock, timeframe, opened_at, source) 유니크 키로 get_or_create → 재수집/재시작에도 중복 없음.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional

from apps.market.models import Candle
from apps.stock.models import Stock
from core.market.aggregator import OHLCV, Tick, aggregate_ticks, floor_to_minute


def persist_ohlcv(
    stock: Stock,
    ohlcv: OHLCV,
    source: str = "kis_ws",
    timeframe: str = Candle.Timeframe.MIN_1,
) -> tuple[Candle, bool]:
    """단일 1분봉을 mkt_candle에 멱등 적재한다. (candle, created) 반환."""
    return Candle.objects.get_or_create(
        stock=stock,
        timeframe=timeframe,
        opened_at=ohlcv.opened_at,
        source=source,
        defaults=dict(
            open_price=ohlcv.open,
            high_price=ohlcv.high,
            low_price=ohlcv.low,
            close_price=ohlcv.close,
            volume=ohlcv.volume,
            raw_payload={"trade_count": ohlcv.trade_count},
        ),
    )


class MinuteBarBuffer:
    """종목별 틱을 모아 '완성된 분'의 캔들만 배출하는 버퍼."""

    def __init__(self) -> None:
        self._ticks: dict[int, list[Tick]] = {}

    def add(self, stock_id: int, tick: Tick) -> None:
        self._ticks.setdefault(stock_id, []).append(tick)

    def flush_completed(self, stock_id: int, now: datetime) -> list[OHLCV]:
        """
        now가 속한 분보다 이전(=완성된) 분들의 캔들만 반환하고 버퍼에서 제거한다.

        진행 중인 현재 분은 아직 확정되지 않았으므로 배출하지 않는다.
        """
        current_minute = floor_to_minute(now)
        ticks = self._ticks.get(stock_id, [])
        completed = [t for t in ticks if floor_to_minute(t.ts) < current_minute]
        self._ticks[stock_id] = [
            t for t in ticks if floor_to_minute(t.ts) >= current_minute
        ]
        return aggregate_ticks(completed)

    def flush_all(self, stock_id: int) -> list[OHLCV]:
        """남은 틱을 모두 캔들로 배출한다(장 마감 등 강제 확정)."""
        ticks = self._ticks.pop(stock_id, [])
        return aggregate_ticks(ticks)


class RealtimeCollector:
    """
    틱 소스를 소비하여 완성된 1분봉을 mkt_candle에 적재하는 수집기.

    tick_source는 (stock_id, ts, price, volume) 튜플을 산출하는 iterable이면 된다.
    """

    def __init__(self, source: str = "kis_ws") -> None:
        self.source = source
        self.buffer = MinuteBarBuffer()
        self._stocks: dict[int, Stock] = {}

    def _stock(self, stock_id: int) -> Stock:
        if stock_id not in self._stocks:
            self._stocks[stock_id] = Stock.objects.get(id=stock_id)
        return self._stocks[stock_id]

    def ingest_tick(
        self, stock_id: int, ts: datetime, price: Decimal, volume: Decimal
    ) -> int:
        """틱 1건을 반영하고, 그로 인해 완성된 이전 분 캔들을 적재한다. 적재된 캔들 수 반환."""
        self.buffer.add(
            stock_id,
            Tick(ts=ts, price=Decimal(str(price)), volume=Decimal(str(volume))),
        )
        persisted = 0
        for ohlcv in self.buffer.flush_completed(stock_id, ts):
            persist_ohlcv(self._stock(stock_id), ohlcv, self.source)
            persisted += 1
        return persisted

    def consume(self, tick_source: Iterable[tuple], finalize: bool = True) -> int:
        """
        틱 소스를 끝까지 소비한다. finalize=True면 종료 시 남은 분도 확정 적재.
        총 적재 캔들 수를 반환한다.
        """
        total = 0
        seen: set[int] = set()
        for stock_id, ts, price, volume in tick_source:
            seen.add(stock_id)
            total += self.ingest_tick(stock_id, ts, price, volume)
        if finalize:
            for stock_id in seen:
                for ohlcv in self.buffer.flush_all(stock_id):
                    persist_ohlcv(self._stock(stock_id), ohlcv, self.source)
                    total += 1
        return total
