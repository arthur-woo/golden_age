"""
1분봉 집계기 (Phase 5-1)

실시간 체결(틱) 스트림을 1분 OHLCV 캔들로 집계하는 순수 함수 모음.
부작용이 없으므로 실시간 수집 워커와 백테스트 리샘플링이 동일 로직을 공유한다.

캔들 규약(db_schema.md / mkt_candle 준수)
- opened_at : 해당 분의 시작 시각(초·마이크로초 절삭)
- open      : 그 분 첫 체결가
- close     : 그 분 마지막 체결가
- high/low  : 그 분 최고/최저 체결가
- volume    : 그 분 체결량 합
- 체결이 없는 분은 캔들을 생성하지 않는다(gap은 상위에서 처리).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class Tick:
    """단일 체결(틱)."""

    ts: datetime
    price: Decimal
    volume: Decimal


@dataclass(frozen=True)
class OHLCV:
    """1분 집계 결과."""

    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    trade_count: int


def floor_to_minute(ts: datetime) -> datetime:
    """초·마이크로초를 절삭하여 분 단위로 내린다."""
    return ts.replace(second=0, microsecond=0)


def aggregate_ticks(ticks: Iterable[Tick]) -> list[OHLCV]:
    """
    틱들을 1분 OHLCV 캔들 리스트로 집계한다(opened_at 오름차순).

    같은 분 안에서는 틱 순서(체결 시각)를 존중하여 open/close를 정한다.
    입력 순서와 무관하게 동일 결과를 내도록 내부에서 (ts) 기준 정렬한다.
    """
    ordered = sorted(ticks, key=lambda t: t.ts)

    buckets: dict[datetime, list[Tick]] = {}
    for tick in ordered:
        minute = floor_to_minute(tick.ts)
        buckets.setdefault(minute, []).append(tick)

    candles: list[OHLCV] = []
    for minute in sorted(buckets.keys()):
        group = buckets[minute]
        prices = [t.price for t in group]
        candles.append(
            OHLCV(
                opened_at=minute,
                open=group[0].price,
                high=max(prices),
                low=min(prices),
                close=group[-1].price,
                volume=sum((t.volume for t in group), Decimal("0")),
                trade_count=len(group),
            )
        )
    return candles
