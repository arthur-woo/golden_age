"""
리스크 가드 (Phase 5-6)

포트폴리오/시스템 레벨의 진입 허용 여부와 킬스위치를 순수 함수로 판정한다.
거래 파이프라인(trader_executor)이 신규 진입 직전에 호출한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RiskLimits:
    max_gross_exposure_ratio: float = 1.0  # 총노출/자본 상한
    max_positions: int = 10  # 동시 보유 종목 수 상한
    max_position_ratio: float = 0.2  # 종목당 노출 상한
    daily_loss_limit_ratio: float = 0.03  # 일일 손실 한도(자본 대비)


@dataclass(frozen=True)
class PortfolioState:
    equity: float  # 총자본
    gross_exposure: float  # 현재 총노출(보유 평가액 합)
    num_positions: int  # 현재 보유 종목 수
    day_pnl: float  # 당일 실현+평가 손익
    regime_blocked: bool = False  # 레짐 게이팅(CHAOS 등)으로 신규 진입 차단


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str


def daily_loss_breached(state: PortfolioState, limits: RiskLimits) -> bool:
    """일일 손실 한도 초과 여부(킬스위치)."""
    if state.equity <= 0:
        return True
    loss_ratio = -state.day_pnl / state.equity
    return loss_ratio >= limits.daily_loss_limit_ratio


def can_open_new_position(
    state: PortfolioState,
    add_notional: float,
    limits: Optional[RiskLimits] = None,
) -> GuardDecision:
    """
    새 포지션(add_notional 규모)을 열 수 있는지 판정한다.

    순서: 레짐 차단 → 킬스위치 → 종목 수 → 종목당 상한 → 총노출 상한.
    """
    limits = limits or RiskLimits()

    if state.regime_blocked:
        return GuardDecision(False, "레짐 차단(신규 진입 중단)")

    if daily_loss_breached(state, limits):
        return GuardDecision(False, "일일 손실 한도 초과(킬스위치)")

    if state.num_positions >= limits.max_positions:
        return GuardDecision(False, f"동시 보유 종목 수 상한({limits.max_positions}) 도달")

    if state.equity > 0:
        if add_notional / state.equity > limits.max_position_ratio:
            return GuardDecision(False, "종목당 노출 상한 초과")
        if (
            state.gross_exposure + add_notional
        ) / state.equity > limits.max_gross_exposure_ratio:
            return GuardDecision(False, "총노출 상한 초과")

    return GuardDecision(True, "OK")
