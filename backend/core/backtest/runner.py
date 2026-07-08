"""
다봉 재현 백테스트 러너 (잔여 A)

trader_executor를 BacktestBroker로 구동하여, 실거래와 **동일한 의사결정·비용 코드**로
과거 1분봉 구간을 재현한다(look-ahead 방지: 각 봉의 as_of 이전 캔들만 사용).

전제
- 대상 캔들은 mkt_candle에 이미 적재되어 있어야 한다(수집기/백필로 확보).
- decision_bars 는 (as_of, exec_price, volume) 튜플 시퀀스. as_of까지의 캔들로 판단하고
  exec_price(예: 다음 봉 시가)에 체결한다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

from django.utils import timezone

from apps.account.models import ExecutionRun
from apps.order.models import TradeExecution
from apps.stock.models import Stock
from apps.trading.models import Trader, TraderExecutionRun
from core.backtest.broker import BacktestBroker
from core.backtest.costs import CostConfig
from core.backtest import tca
from core.pipeline.trader_executor import execute_trader_for_stock


def run_trader_backtest(
    trader: Trader,
    stock: Stock,
    decision_bars: Iterable[tuple],
    initial_cash: Decimal,
    cost_config: Optional[CostConfig] = None,
    regime=None,
) -> dict:
    """
    trader를 BacktestBroker로 다봉 구동한다.

    Returns:
        dict(broker, final_equity, cash, positions, num_bars, equity_curve, metrics)
        metrics는 core.backtest.tca.summarize 결과(순PnL·승률·비용드래그·MDD·샤프 등).
    """
    broker = BacktestBroker(initial_cash, cost_config)

    account_run = ExecutionRun.objects.create(
        account=trader.account,
        run_type=ExecutionRun.RunType.SCHEDULED,
        status=ExecutionRun.Status.RUNNING,
        started_at=timezone.now(),
    )

    num_bars = 0
    equity_curve: list[float] = [float(initial_cash)]
    for as_of, exec_price, volume in decision_bars:
        broker.set_market(stock.symbol, Decimal(str(exec_price)), Decimal(str(volume)))
        run = TraderExecutionRun.objects.create(
            account_run=account_run,
            trader=trader,
            status=TraderExecutionRun.Status.RUNNING,
            started_at=as_of,
        )
        execute_trader_for_stock(trader, run, stock, regime, as_of=as_of, broker=broker)
        equity_curve.append(float(broker.get_balance().total_asset_value))
        num_bars += 1

    balance = broker.get_balance()
    account_run.status = ExecutionRun.Status.SUCCESS
    account_run.finished_at = timezone.now()
    account_run.save(update_fields=["status", "finished_at"])

    # 체결 내역 → TCA 지표
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
        "cash": balance.cash_balance,
        "positions": dict(broker.positions),
        "num_bars": num_bars,
        "equity_curve": equity_curve,
        "metrics": metrics,
    }
