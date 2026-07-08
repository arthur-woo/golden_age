"""
거래비용분석(TCA) · 백테스트 성과 지표 (C-11)

체결 내역과 자본곡선으로부터 순수익·실현손익·승률·비용드래그·MDD·샤프를 계산한다.
순수 함수로 두어 리서치 엔진과 다봉 러너가 동일 지표 정의를 공유한다.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Sequence


def compute_trade_pnls(fills: Sequence[dict]) -> list[float]:
    """
    체결 시퀀스(시간순)를 FIFO로 매칭하여 라운드트립 실현손익 리스트를 반환한다.

    fills 원소: {side: 'BUY'|'SELL', qty, price, cost}. cost는 그 체결의 수수료+세금+슬리피지.
    """
    lots: deque = deque()  # [남은수량, 매수가, 주당비용]
    pnls: list[float] = []
    for f in fills:
        qty = float(f["qty"])
        price = float(f["price"])
        cost = float(f.get("cost", 0.0))
        if qty <= 0:
            continue
        if f["side"] == "BUY":
            lots.append([qty, price, cost / qty])
        else:  # SELL
            sell_cost_ps = cost / qty
            remaining = qty
            while remaining > 1e-12 and lots:
                lot = lots[0]
                matched = min(remaining, lot[0])
                buy_basis = (lot[1] + lot[2]) * matched
                sell_proceeds = (price - sell_cost_ps) * matched
                pnls.append(sell_proceeds - buy_basis)
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-12:
                    lots.popleft()
    return pnls


def max_drawdown(equity: Sequence[float]) -> float:
    """자본곡선의 최대 낙폭(0~1)."""
    peak = -math.inf
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def sharpe(equity: Sequence[float]) -> float:
    """자본곡선 봉간 수익률의 (비연율화) 샤프. 표본<2 또는 무변동이면 0."""
    rets = [
        equity[i] / equity[i - 1] - 1.0
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]
    n = len(rets)
    if n < 2:
        return 0.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    std = math.sqrt(var)
    return mean / std if std > 0 else 0.0


def summarize(
    initial_capital: float,
    final_equity: float,
    equity_curve: Sequence[float],
    fills: Sequence[dict],
) -> dict:
    """백테스트 성과·비용 지표를 집계한다."""
    pnls = compute_trade_pnls(fills)
    wins = sum(1 for p in pnls if p > 0)
    total_cost = sum(float(f.get("cost", 0.0)) for f in fills)
    net_pnl = final_equity - initial_capital
    return {
        "net_pnl": net_pnl,
        "total_return": net_pnl / initial_capital if initial_capital else 0.0,
        "num_fills": len(fills),
        "num_round_trips": len(pnls),
        "win_rate": (wins / len(pnls)) if pnls else 0.0,
        "realized_pnl": sum(pnls),
        "total_cost": total_cost,
        "cost_drag": total_cost / initial_capital if initial_capital else 0.0,
        "max_drawdown": max_drawdown(equity_curve),
        "sharpe": sharpe(equity_curve),
    }
