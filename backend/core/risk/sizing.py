"""
포지션 사이징 (Phase 5-6)

여러 상한의 최소값으로 최종 수량을 정한다(가장 보수적인 제약이 지배).
- 신호강도(프랙셔널 켈리): 승률/페이오프 기반
- 변동성 타겟팅: 종목 변동성으로 위험 균등화
- 유동성 상한: 분당거래량/ADV 참여율로 슬리피지 억제
- 종목당 자본비중 상한

모든 함수는 순수 함수이며 정수 주(shares)를 반환한다(한국주식 최소 단위 1주).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SizingConfig:
    max_position_fraction: float = 0.2  # 종목당 최대 자본비중
    target_volatility: float = 0.002  # 봉당 목표 변동성(0.2%)
    kelly_fraction: float = 0.25  # 프랙셔널 켈리 배수(풀켈리의 1/4)
    adv_participation: float = 0.05  # ADV 참여 상한
    minute_participation: float = 0.1  # 분당거래량 참여 상한


@dataclass(frozen=True)
class SizingResult:
    shares: int
    breakeven_prob: float
    kelly_shares: float
    vol_shares: float
    liquidity_shares: float
    cap_shares: float
    reason: str


def breakeven_probability(upper: float, lower: float, cost: float) -> float:
    """기대 순수익 0이 되는 승률. p*upper - (1-p)*lower - cost = 0."""
    denom = upper + lower
    if denom <= 0:
        return 1.0
    return (lower + cost) / denom


def kelly_fraction(prob: float, upper: float, lower: float) -> float:
    """비대칭 페이오프 켈리 비율 f* = (b*p - q)/b, b=upper/lower. [0,1] 클리핑."""
    if lower <= 0 or upper <= 0:
        return 0.0
    b = upper / lower
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(0.0, min(f, 1.0))


def volatility_target_shares(
    capital: float, price: float, instrument_vol: float, target_vol: float
) -> float:
    """변동성 타겟팅 수량(주). instrument_vol 이 클수록 작아진다."""
    if instrument_vol <= 0 or price <= 0:
        return 0.0
    dollar_alloc = capital * (target_vol / instrument_vol)
    return dollar_alloc / price


def liquidity_cap_shares(
    minute_volume: Optional[float], adv: Optional[float], config: SizingConfig
) -> float:
    """유동성 상한 수량(주). 정보가 없으면 무한대(제약 없음)."""
    caps = []
    if minute_volume and minute_volume > 0:
        caps.append(minute_volume * config.minute_participation)
    if adv and adv > 0:
        caps.append(adv * config.adv_participation)
    return min(caps) if caps else math.inf


def compute_position_size(
    capital: float,
    price: float,
    prob: float,
    instrument_vol: float,
    upper: float,
    lower: float,
    cost: float,
    minute_volume: Optional[float] = None,
    adv: Optional[float] = None,
    config: Optional[SizingConfig] = None,
) -> SizingResult:
    """
    최종 매수 수량(주)을 계산한다. 엣지가 없으면(승률<=손익분기) 0주.

    최종 = min(켈리 자본상한, 변동성타겟, 유동성상한) 을 정수 절삭.
    """
    config = config or SizingConfig()
    be = breakeven_probability(upper, lower, cost)

    if price <= 0 or prob <= be:
        return SizingResult(0, be, 0.0, 0.0, 0.0, 0.0, "엣지 없음(승률<=손익분기)")

    # 신호강도(프랙셔널 켈리) -> 자본비중 -> 주
    frac = min(
        kelly_fraction(prob, upper, lower) * config.kelly_fraction,
        config.max_position_fraction,
    )
    kelly_shares = capital * frac / price

    vol_shares = volatility_target_shares(
        capital, price, instrument_vol, config.target_volatility
    )
    liquidity_shares = liquidity_cap_shares(minute_volume, adv, config)
    cap_shares = capital * config.max_position_fraction / price

    final = math.floor(min(kelly_shares, vol_shares, liquidity_shares, cap_shares))
    final = max(final, 0)
    reason = "OK" if final > 0 else "상한 계산 결과 0주"
    return SizingResult(
        shares=final,
        breakeven_prob=be,
        kelly_shares=kelly_shares,
        vol_shares=vol_shares,
        liquidity_shares=liquidity_shares,
        cap_shares=cap_shares,
        reason=reason,
    )
