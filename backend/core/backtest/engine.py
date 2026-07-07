"""
이벤트 드리븐 백테스트 엔진 (Phase 5-3)

look-ahead(미래 참조) 방지 원칙
- 시각 t 봉 **종가**까지의 정보로만 의사결정한다.
- 체결은 **t+1 봉 시가**에 거래비용/슬리피지를 반영하여 이뤄진다.

long-only 초단기 매매를 가정한다(진입=매수, 청산=매도).
청산은 삼중 배리어(익절/손절/시간)로 처리하며, 마지막 봉에서 강제 플랫한다.

비용/슬리피지는 core.backtest.costs 를 그대로 사용하여 백테스트와 실거래 사후검증이
동일한 비용 모델을 공유한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Optional, Sequence

from core.backtest.costs import (
    SIDE_BUY,
    SIDE_SELL,
    CostConfig,
    estimate_fill,
)
from core.features.builder import build_features

# signal_fn(features: dict, index: int, candle) -> "BUY" | "HOLD"
SignalFn = Callable[[dict, int, object], str]


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10_000_000.0
    position_fraction: float = 0.2  # 진입 시 현금의 비율
    take_profit: float = 0.004  # 익절 +0.4%
    stop_loss: float = 0.004  # 손절 -0.4%
    horizon: int = 10  # 최대 보유 봉 수
    warmup: int = 20  # Feature 계산용 최소 이력
    cost: CostConfig = field(default_factory=CostConfig)


@dataclass
class Trade:
    entry_index: int
    exit_index: int
    quantity: float
    entry_price: float  # 비용 반영 체결가
    exit_price: float
    gross_pnl: float  # (exit-entry)*qty (수수료/세금 제외)
    cost: float  # 진입/청산 수수료+세금+슬리피지 합
    net_pnl: float
    exit_reason: str  # TAKE / STOP / TIME / EOD


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[float]
    metrics: dict


def _latest_first(candles: Sequence, upto: int, lookback: int = 100) -> list:
    """candles[:upto+1] 을 최신 -> 과거 순으로 (최대 lookback개) 반환."""
    return list(reversed(candles[: upto + 1]))[:lookback]


def _slippage_cost(fill) -> float:
    return float(fill.slippage_cost)


def run_backtest(
    candles: Sequence,
    signal_fn: SignalFn,
    config: Optional[BacktestConfig] = None,
) -> BacktestResult:
    """
    시간 오름차순(과거 -> 최신) 캔들로 백테스트를 수행한다.

    candles 원소는 open_price/high_price/low_price/close_price/volume 속성을 갖는
    apps.market.Candle(또는 동일 인터페이스)여야 한다.
    """
    config = config or BacktestConfig()
    n = len(candles)
    closes = [float(c.close_price) for c in candles]

    cash = config.initial_capital
    position: Optional[dict] = None
    trades: list[Trade] = []
    equity_curve: list[float] = []

    def fee_of(fill) -> float:
        return float(fill.commission) + float(fill.tax) + _slippage_cost(fill)

    # 의사결정 봉 t, 체결 봉 t+1 이므로 마지막 봉(n-1)은 진입 결정에 쓰지 않는다.
    for t in range(config.warmup, n - 1):
        exec_candle = candles[t + 1]
        exec_ref = float(exec_candle.open_price)
        exec_vol = float(exec_candle.volume)

        if position is None:
            feats = build_features(_latest_first(candles, t), closes[t])
            if signal_fn(feats, t, candles[t]) == SIDE_BUY:
                budget = cash * config.position_fraction
                qty = math.floor(budget / exec_ref) if exec_ref > 0 else 0
                if qty > 0:
                    fill = estimate_fill(
                        SIDE_BUY,
                        Decimal(str(exec_ref)),
                        Decimal(qty),
                        Decimal(str(exec_vol)),
                        config.cost,
                    )
                    entry_price = float(fill.fill_price)
                    entry_cost = fee_of(fill)
                    cash -= entry_price * qty + entry_cost
                    position = {
                        "entry_index": t + 1,
                        "qty": qty,
                        "entry_price": entry_price,
                        "entry_cost": entry_cost,
                    }
        else:
            held = t - position["entry_index"]
            ret = closes[t] / position["entry_price"] - 1.0
            reason = None
            if ret >= config.take_profit:
                reason = "TAKE"
            elif ret <= -config.stop_loss:
                reason = "STOP"
            elif held >= config.horizon:
                reason = "TIME"

            if reason is not None:
                fill = estimate_fill(
                    SIDE_SELL,
                    Decimal(str(exec_ref)),
                    Decimal(position["qty"]),
                    Decimal(str(exec_vol)),
                    config.cost,
                )
                exit_price = float(fill.fill_price)
                exit_cost = fee_of(fill)
                qty = position["qty"]
                cash += exit_price * qty - exit_cost
                gross = (exit_price - position["entry_price"]) * qty
                total_cost = position["entry_cost"] + exit_cost
                trades.append(
                    Trade(
                        entry_index=position["entry_index"],
                        exit_index=t + 1,
                        quantity=qty,
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        gross_pnl=gross,
                        cost=total_cost,
                        net_pnl=gross - exit_cost,  # entry_cost는 이미 진입 시 현금 차감됨
                        exit_reason=reason,
                    )
                )
                position = None

        # 자본 곡선 (현금 + 보유 평가액)
        mark = closes[t]
        equity = cash + (position["qty"] * mark if position else 0.0)
        equity_curve.append(equity)

    # 종료 시 강제 플랫 (마지막 봉 종가)
    if position is not None:
        last = n - 1
        fill = estimate_fill(
            SIDE_SELL,
            Decimal(str(closes[last])),
            Decimal(position["qty"]),
            Decimal(str(float(candles[last].volume))),
            config.cost,
        )
        exit_price = float(fill.fill_price)
        exit_cost = fee_of(fill)
        qty = position["qty"]
        cash += exit_price * qty - exit_cost
        gross = (exit_price - position["entry_price"]) * qty
        trades.append(
            Trade(
                entry_index=position["entry_index"],
                exit_index=last,
                quantity=qty,
                entry_price=position["entry_price"],
                exit_price=exit_price,
                gross_pnl=gross,
                cost=position["entry_cost"] + exit_cost,
                net_pnl=gross - exit_cost,
                exit_reason="EOD",
            )
        )
        position = None
        equity_curve.append(cash)

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=_compute_metrics(config.initial_capital, cash, trades, equity_curve),
    )


def _compute_metrics(
    initial_capital: float,
    final_cash: float,
    trades: list[Trade],
    equity_curve: list[float],
) -> dict:
    num_trades = len(trades)
    wins = sum(1 for tr in trades if tr.net_pnl > 0)
    gross = sum(tr.gross_pnl for tr in trades)
    total_cost = sum(tr.cost for tr in trades)
    net_pnl = final_cash - initial_capital

    # 최대 낙폭(MDD)
    peak = -math.inf
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)

    return {
        "num_trades": num_trades,
        "win_rate": (wins / num_trades) if num_trades else 0.0,
        "gross_pnl": gross,
        "total_cost": total_cost,
        "net_pnl": net_pnl,
        "total_return": net_pnl / initial_capital if initial_capital else 0.0,
        "max_drawdown": max_dd,
    }
