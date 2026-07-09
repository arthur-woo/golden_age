"""
원장 리컨실리에이션 (안전#3)

브로커 실제 보유수량 ↔ 내부 PositionLedger 집계를 대조하고,
불일치 시 브로커를 진실원천으로 삼아 조정(ADJUSTMENT) 원장을 남긴다.
앱 재시작·체결 누락으로 내부 장부가 어긋나는 것을 바로잡는다.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.account.models import PositionLedger
from apps.stock.models import Stock


def internal_positions(account) -> dict:
    """내부 원장 기준 종목별 순보유 수량 {symbol: qty}(0 포함 가능)."""
    rows = (
        PositionLedger.objects.filter(account=account)
        .values("stock__symbol")
        .annotate(qty=Sum("quantity_delta"))
    )
    return {r["stock__symbol"]: (r["qty"] or Decimal("0")) for r in rows}


def reconcile_account(account, broker, apply: bool = False) -> dict:
    """
    브로커 보유수량과 내부 원장을 대조한다.

    apply=True면 diff(브로커-내부)만큼 ADJUSTMENT 원장을 추가해 내부를 브로커에 맞춘다.
    반환: {"positions": {symbol: {internal, broker, diff}}, "adjusted": n}
    """
    internal = internal_positions(account)
    broker_pos = broker.get_positions()
    symbols = set(internal) | set(broker_pos)

    diffs = {}
    adjusted = 0
    for sym in sorted(symbols):
        iq = Decimal(str(internal.get(sym, 0)))
        bq = Decimal(str(broker_pos.get(sym, 0)))
        d = bq - iq
        diffs[sym] = {"internal": float(iq), "broker": float(bq), "diff": float(d)}
        if apply and d != 0:
            stock = Stock.objects.filter(symbol=sym).first()
            if stock is None:
                continue
            PositionLedger.objects.create(
                account=account,
                stock=stock,
                quantity_delta=d,
                price=Decimal("0"),
                reason="ADJUSTMENT(리컨실: 브로커 기준 보정)",
                occurred_at=timezone.now(),
            )
            adjusted += 1
    return {"positions": diffs, "adjusted": adjusted}
