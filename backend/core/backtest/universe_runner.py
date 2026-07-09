"""
멀티종목(유니버스) 백테스트 드라이버 (배선: A-3 + A-4)

매 봉 select_candidates(A-3)로 대상 종목을 top-K로 축소하고,
지수/횡단면 컨텍스트(A-4)를 build_features에 주입하여 trader_executor를 구동한다.
단일 BacktestBroker를 공유해 포트폴리오 수준 TCA를 산출한다.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Iterable, Optional

from django.utils import timezone

from apps.account.models import ExecutionRun
from apps.market.models import Candle
from apps.order.models import TradeExecution
from apps.stock.models import Stock
from apps.trading.models import Trader, TraderExecutionRun
from core.backtest import tca
from core.backtest.broker import BacktestBroker
from core.backtest.costs import CostConfig
from core.features.builder import cross_sectional_rank
from core.pipeline.trader_executor import execute_trader_for_stock
from core.universe.filter import CandidateConfig, StockSnapshot, select_candidates

FEATURE_LOOKBACK = 100
MIN_HISTORY = 5


def _std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


def build_stock_snapshot(stock_id: int, candles: list) -> StockSnapshot:
    """최신 -> 과거 순 캔들로부터 후보 필터용 스냅샷을 구성한다."""
    latest = candles[0]
    closes = [float(c.close_price) for c in candles]
    rets = [
        closes[i] / closes[i + 1] - 1
        for i in range(min(20, len(closes) - 1))
        if closes[i + 1] > 0
    ]
    k = min(5, len(closes) - 1)
    momentum = (closes[0] / closes[k] - 1) if k > 0 and closes[k] > 0 else 0.0
    return StockSnapshot(
        stock_id=stock_id,
        turnover=float(latest.close_price) * float(latest.volume),
        recent_volume=float(latest.volume),
        volatility=_std(rets),
        score=momentum,
    )


def run_universe_backtest(
    trader: Trader,
    stocks: list[Stock],
    bar_times: Iterable,
    initial_cash: Decimal,
    candidate_config: Optional[CandidateConfig] = None,
    cost_config: Optional[CostConfig] = None,
    regime=None,
) -> dict:
    """
    유니버스를 매 봉 후보 필터로 축소하며 포트폴리오 백테스트를 수행한다.

    반환: dict(broker, final_equity, num_bars, equity_curve, metrics, selected_counts)
    """
    timeframe = (trader.config_payload or {}).get(
        "candle_timeframe", Candle.Timeframe.DAY_1
    )
    broker = BacktestBroker(initial_cash, cost_config)
    account_run = ExecutionRun.objects.create(
        account=trader.account,
        run_type=ExecutionRun.RunType.SCHEDULED,
        status=ExecutionRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    by_id = {s.id: s for s in stocks}

    equity_curve = [float(initial_cash)]
    selected_counts = []
    for t in bar_times:
        snapshots = []
        for stock in stocks:
            candles = list(
                Candle.objects.filter(
                    stock=stock, timeframe=timeframe, opened_at__lte=t
                ).order_by("-opened_at")[:FEATURE_LOOKBACK]
            )
            if len(candles) < MIN_HISTORY:
                continue
            latest = candles[0]
            broker.set_market(
                stock.symbol, latest.close_price, latest.volume
            )  # 전 종목 마킹
            snapshots.append(build_stock_snapshot(stock.id, candles))

        selected = select_candidates(snapshots, candidate_config)
        selected_counts.append(len(selected))

        # A-4 컨텍스트: 지수(평균 모멘텀) + 횡단면 랭크
        scores = {s.stock_id: s.score for s in snapshots}
        ranks = cross_sectional_rank(scores)
        index_ret = sum(scores.values()) / len(scores) if scores else 0.0

        for sid in selected:
            stock = by_id[sid]
            run = TraderExecutionRun.objects.create(
                account_run=account_run,
                trader=trader,
                status=TraderExecutionRun.Status.RUNNING,
                started_at=t,
            )
            context = {"index_ret_1": index_ret, "cs_return_rank": ranks.get(sid, 0.5)}
            execute_trader_for_stock(
                trader, run, stock, regime, as_of=t, broker=broker, context=context
            )

        equity_curve.append(float(broker.get_balance().total_asset_value))

    balance = broker.get_balance()
    account_run.status = ExecutionRun.Status.SUCCESS
    account_run.finished_at = timezone.now()
    account_run.save(update_fields=["status", "finished_at"])

    fills = [
        {
            "side": ex.side,
            "qty": ex.executed_quantity,
            "price": ex.executed_price,
            "cost": float(ex.fee_amount + ex.tax_amount + ex.slippage_amount),
        }
        for ex in TradeExecution.objects.filter(account=trader.account).order_by(
            "executed_at", "id"
        )
    ]
    metrics = tca.summarize(
        float(initial_cash), float(balance.total_asset_value), equity_curve, fills
    )

    return {
        "broker": broker,
        "final_equity": balance.total_asset_value,
        "num_bars": len(selected_counts),
        "equity_curve": equity_curve,
        "selected_counts": selected_counts,
        "metrics": metrics,
    }
