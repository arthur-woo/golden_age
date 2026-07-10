"""
시스템 헬스체크·모니터링 (안전#12)

실거래 안전을 위해 최소한의 상태를 점검한다: 데이터 신선도, 미체결 주문,
마지막 실행 상태, 보유 종목 수. 스케줄/외부 감시가 주기 호출해 이상 시 알린다.
"""

from __future__ import annotations

from django.utils import timezone

from apps.account.models import ExecutionRun
from apps.market.models import Candle
from apps.order.models import Order
from core.pipeline.trader_executor import get_open_position_count


def system_health(
    account, now=None, stale_minutes: int = 5, timeframe: str = "1m"
) -> dict:
    """계좌 기준 헬스 지표를 반환한다. healthy=False면 개입 필요."""
    now = now or timezone.now()

    latest = Candle.objects.filter(timeframe=timeframe).order_by("-opened_at").first()
    candle_age_min = (now - latest.opened_at).total_seconds() / 60.0 if latest else None
    data_stale = candle_age_min is None or candle_age_min > stale_minutes

    pending_orders = Order.objects.filter(
        account=account, status=Order.Status.ACCEPTED
    ).count()

    last_run = (
        ExecutionRun.objects.filter(account=account).order_by("-started_at").first()
    )
    last_run_status = last_run.status if last_run else None

    healthy = (not data_stale) and (last_run_status != ExecutionRun.Status.FAILED)
    return {
        "healthy": healthy,
        "candle_age_min": candle_age_min,
        "data_stale": data_stale,
        "pending_orders": pending_orders,
        "last_run_status": last_run_status,
        "open_positions": get_open_position_count(account),
        "checked_at": now.isoformat(),
    }
