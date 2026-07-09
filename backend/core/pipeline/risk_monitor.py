"""
상시 손절/익절 감시 (안전#7)

전체 매매 파이프라인(분 주기)과 별개로 더 자주 돌며, 보유 포지션이 손절/익절선을
이탈하면 즉시 보호 청산(SELL)한다. 급락 시 반응 지연을 줄인다.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.account.models import ExecutionRun
from apps.stock.models import Stock
from apps.trading.models import DecisionLog, Trader, TraderExecutionRun
from core.pipeline.reconcile import internal_positions
from core.pipeline.trader_executor import execute_order, get_position_info


def find_breached_positions(account, broker, stop_ratio: float, take_ratio: float):
    """
    보유 포지션 중 손절/익절선을 이탈한 종목을 찾는다.

    반환: [(stock, qty, reason)] — reason은 'STOP' 또는 'TAKE'.
    """
    breached = []
    for symbol, qty in internal_positions(account).items():
        if not qty or qty <= 0:
            continue
        stock = Stock.objects.filter(symbol=symbol).first()
        if stock is None:
            continue
        try:
            cur = float(broker.get_current_price(symbol).price)
        except Exception:
            continue
        _, avg_entry = get_position_info(account, stock)
        avg = float(avg_entry)
        if avg <= 0 or cur <= 0:
            continue
        if cur <= avg * (1 - stop_ratio):
            breached.append((stock, qty, "STOP"))
        elif cur >= avg * (1 + take_ratio):
            breached.append((stock, qty, "TAKE"))
    return breached


def monitor_and_exit(account, broker, stop_ratio: float, take_ratio: float) -> int:
    """이탈 포지션을 보호 청산한다. 청산 주문 수를 반환한다."""
    breached = find_breached_positions(account, broker, stop_ratio, take_ratio)
    if not breached:
        return 0

    trader = account.traders.filter(status=Trader.Status.ACTIVE).first()
    if trader is None:
        return 0

    account_run = ExecutionRun.objects.create(
        account=account,
        run_type=ExecutionRun.RunType.MANUAL,
        status=ExecutionRun.Status.RUNNING,
        started_at=timezone.now(),
    )
    exits = 0
    for stock, qty, reason in breached:
        run = TraderExecutionRun.objects.create(
            account_run=account_run,
            trader=trader,
            status=TraderExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        price = broker.get_current_price(stock.symbol).price
        decision = DecisionLog.objects.create(
            trader_run=run,
            stock=stock,
            final_action=DecisionLog.FinalAction.SELL,
            target_quantity=qty,
            reason=f"[Risk Monitor] {reason} 보호 청산",
            decided_at=timezone.now(),
        )
        from apps.order.models import Order

        execute_order(trader, decision, stock, Order.Side.SELL, qty, price, broker)
        exits += 1

    account_run.status = ExecutionRun.Status.SUCCESS
    account_run.finished_at = timezone.now()
    account_run.save(update_fields=["status", "finished_at"])
    return exits
