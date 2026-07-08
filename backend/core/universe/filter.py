"""
후보 종목 필터 (A-3 · 설계 2장)

정적 유니버스(예: KOSPI200 point-in-time)를 매 분 거래 가능·유동적·엣지 기대 종목으로 축소한다.
동적 컷(유동성/스프레드/변동성밴드/상태) 통과 종목을 score로 랭킹하여 top-K만 반환한다.

select_candidates는 순수 함수이며, 입력 StockSnapshot은 호출자가 시세/캔들로 구성한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CandidateConfig:
    min_turnover: float = 1e8  # 당일 누적 거래대금 하한
    min_recent_volume: float = 0.0  # 최근 분당 거래량(대금) 하한
    max_spread_bps: float = 50.0  # 스프레드 상한(bps)
    vol_low: float = 0.0005  # 변동성(1분 RV) 허용 하한
    vol_high: float = 0.03  # 변동성 허용 상한
    open_block_minutes: int = 5  # 개장 후 N분 신규 진입 금지
    close_block_from: int = 375  # 개장 후 M분(=15:15) 이후 신규 진입 금지
    top_k: int = 20  # 동시 후보 상한


@dataclass(frozen=True)
class StockSnapshot:
    stock_id: int
    turnover: float  # 당일 누적 거래대금
    recent_volume: float  # 최근 분당 거래량(또는 대금)
    volatility: float  # 1분 실현변동성
    score: float = 0.0  # 랭킹 점수(모델 확률 등), 클수록 우선
    spread_bps: Optional[float] = None  # 호가 스프레드(bps), None이면 미확인
    halted: bool = False  # VI/거래정지/단일가


def is_entry_window(minutes_since_open: Optional[int], config: CandidateConfig) -> bool:
    """현재가 신규 진입 허용 시간대인지 판정한다."""
    if minutes_since_open is None:
        return True
    if minutes_since_open < config.open_block_minutes:
        return False
    if minutes_since_open >= config.close_block_from:
        return False
    return True


def passes_filters(snapshot: StockSnapshot, config: CandidateConfig) -> bool:
    """단일 종목이 동적 컷을 통과하는지 판정한다."""
    if snapshot.halted:
        return False
    if snapshot.turnover < config.min_turnover:
        return False
    if snapshot.recent_volume < config.min_recent_volume:
        return False
    if snapshot.spread_bps is not None and snapshot.spread_bps > config.max_spread_bps:
        return False
    if not (config.vol_low <= snapshot.volatility <= config.vol_high):
        return False
    return True


def select_candidates(
    snapshots: list[StockSnapshot],
    config: Optional[CandidateConfig] = None,
    minutes_since_open: Optional[int] = None,
) -> list[int]:
    """
    필터 통과 종목을 score 내림차순으로 정렬해 top-K stock_id를 반환한다.

    진입 금지 시간대(개장 초/종가 근접)면 빈 리스트를 반환한다.
    """
    config = config or CandidateConfig()
    if not is_entry_window(minutes_since_open, config):
        return []

    passed = [s for s in snapshots if passes_filters(s, config)]
    passed.sort(key=lambda s: s.score, reverse=True)
    return [s.stock_id for s in passed[: config.top_k]]
