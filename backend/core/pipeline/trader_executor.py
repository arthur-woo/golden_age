import logging
import math
from decimal import Decimal
from typing import Dict, Optional, Tuple
from django.db import transaction
from django.utils import timezone

from apps.account.models import Account, CashLedger, PositionLedger
from apps.stock.models import Stock
from apps.trading.models import (
    Trader,
    TraderExecutionRun,
    StrategyDecisionLog,
    DecisionLog,
)
from apps.order.models import Order, TradeExecution
from apps.market.models import RegimeSnapshot, Candle, FeatureSnapshot
from apps.account.services import get_broker_for_account
from core.pipeline.strategy_runner import StrategyRunner
from core.ml.filter import MLFilterEngine
from core.features.builder import build_features
from core.risk.sizing import SizingConfig, compute_position_size
from core.risk.guard import PortfolioState, RiskLimits, can_open_new_position
from core.backtest.costs import round_trip_cost_ratio

logger = logging.getLogger(__name__)

# ML Filter 기본 임계치 (Trader.config_payload로 개별 오버라이드 가능)
DEFAULT_ML_RISK_THRESHOLD = Decimal("0.7")
DEFAULT_ML_MIN_TRADE_PROBABILITY = Decimal("0.5")


def capture_feature_snapshot(
    stock: Stock,
    candles: list,
    current_price: Decimal,
    timeframe: str = Candle.Timeframe.DAY_1,
    context: Optional[dict] = None,
) -> FeatureSnapshot:
    """
    현재 시점의 Feature 스냅샷을 mkt_feature_snapshot에 저장한다.

    Feature 계산은 core.features.builder.build_features(실거래/백테스트/학습 공용)에 위임한다.
    context: 지수/레짐/횡단면 컨텍스트(설계 3.1 (7),(8))를 Feature에 조인.
    """
    return FeatureSnapshot.objects.create(
        stock=stock,
        timeframe=timeframe,
        feature_payload=build_features(candles, float(current_price), context),
        source_payload={"candle_count": len(candles)},
        captured_at=timezone.now(),
    )


def get_position_info(account: Account, stock: Stock) -> Tuple[Decimal, Decimal]:
    """
    계좌의 특정 종목에 대한 현재 잔고 수량 및 평균 매수 단가를 계산합니다.
    """
    ledgers = PositionLedger.objects.filter(account=account, stock=stock).order_by(
        "occurred_at"
    )
    qty = Decimal("0.0")
    total_cost = Decimal("0.0")

    for ledger in ledgers:
        qty += ledger.quantity_delta
        if qty <= 0:
            qty = Decimal("0.0")
            total_cost = Decimal("0.0")
        else:
            if ledger.quantity_delta > 0:
                total_cost += ledger.quantity_delta * ledger.price

    avg_price = total_cost / qty if qty > 0 else Decimal("0.0")
    return qty, avg_price


def get_open_position_count(account: Account) -> int:
    """계좌에서 현재 순보유(잔량>0)인 종목 수를 반환한다."""
    from django.db.models import Sum

    rows = (
        PositionLedger.objects.filter(account=account)
        .values("stock")
        .annotate(qty=Sum("quantity_delta"))
    )
    return sum(1 for r in rows if r["qty"] and r["qty"] > 0)


def get_day_start_equity(account: Account, at) -> Optional[float]:
    """당일(자정 이후) 첫 잔고 스냅샷의 총자산을 반환한다(일중 손익 기준). 없으면 None."""
    from apps.account.models import BalanceSnapshot

    day_start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    snap = (
        BalanceSnapshot.objects.filter(
            account=account, snapshotted_at__gte=day_start, snapshotted_at__lte=at
        )
        .order_by("snapshotted_at")
        .first()
    )
    return float(snap.total_asset_value) if snap else None


def _risk_limits(trader: Trader) -> RiskLimits:
    """Trader.config_payload['risk_limits']로 한도를 오버라이드(없으면 기본값)."""
    cfg = (trader.config_payload or {}).get("risk_limits", {})
    return RiskLimits(
        max_gross_exposure_ratio=float(cfg.get("max_gross_exposure_ratio", 1.0)),
        max_positions=int(cfg.get("max_positions", 10)),
        max_position_ratio=float(cfg.get("max_position_ratio", 0.2)),
        daily_loss_limit_ratio=float(cfg.get("daily_loss_limit_ratio", 0.03)),
    )


def compute_target_buy_qty(
    trader: Trader,
    total_asset_value: Decimal,
    current_price: Decimal,
    adjusted_position_size: Decimal,
    weighted_score_sum: Decimal,
    feature_snapshot=None,
    ml_output=None,
    candles=None,
) -> Decimal:
    """
    목표 매수 수량(주)을 계산한다.

    기본: 기존 비율 기반(포지션 비율 × 자산, 최대 노출 제한).
    trader.config_payload["advanced_sizing"]=True 이면 core.risk.sizing.compute_position_size
    (프랙셔널 켈리 · 변동성 타겟팅 · 유동성 상한)를 사용한다.
    """
    config = trader.config_payload or {}

    # 기존(레거시) 사이징 — Phase 4 호환 기본값
    if not config.get("advanced_sizing"):
        max_exposure = trader.max_exposure_ratio * total_asset_value
        target_value = min(total_asset_value * adjusted_position_size, max_exposure)
        return Decimal(str(math.floor(target_value / current_price)))

    s = config.get("sizing", {})
    sizing_config = SizingConfig(
        max_position_fraction=float(
            s.get("max_position_fraction", float(trader.max_exposure_ratio))
        ),
        target_volatility=float(s.get("target_volatility", 0.002)),
        kelly_fraction=float(s.get("kelly_fraction", 0.25)),
        adv_participation=float(s.get("adv_participation", 0.05)),
        minute_participation=float(s.get("minute_participation", 0.1)),
    )
    upper = float(s.get("upper", 0.004))
    lower = float(s.get("lower", 0.004))
    cost = float(s.get("cost", round_trip_cost_ratio(Decimal("0"))))

    # 성공 확률: ML 산출값 우선, 없으면 전략 종합 점수를 [0,1)로 매핑
    if ml_output is not None:
        prob = float(ml_output.trade_probability)
    else:
        prob = min(max(0.5 + float(weighted_score_sum) / 2.0, 0.0), 0.999)

    # 종목 변동성: Feature 스냅샷의 vol_std_20, 없으면 기본값
    instrument_vol = 0.0
    if feature_snapshot is not None:
        instrument_vol = float(feature_snapshot.feature_payload.get("vol_std_20", 0.0))
    if instrument_vol <= 0:
        instrument_vol = float(s.get("default_vol", 0.002))

    minute_volume = float(candles[0].volume) if candles else None

    result = compute_position_size(
        capital=float(total_asset_value),
        price=float(current_price),
        prob=prob,
        instrument_vol=instrument_vol,
        upper=upper,
        lower=lower,
        cost=cost,
        minute_volume=minute_volume,
        adv=s.get("adv"),
        config=sizing_config,
    )
    return Decimal(result.shares)


def execute_trader_for_stock(
    trader: Trader,
    trader_run: TraderExecutionRun,
    stock: Stock,
    regime_snapshot: Optional[RegimeSnapshot] = None,
    as_of=None,
    broker=None,
    context: Optional[dict] = None,
):
    """
    특정 Trader가 특정 Stock에 대해 매매 전략을 평가하고 주문을 실행합니다.

    as_of: 지정 시 그 시각 이전(포함) 캔들만 사용한다(백테스트 look-ahead 방지).
    broker: 주입 시 그대로 사용한다(백테스트 BacktestBroker). 미주입 시 계좌 브로커.
    context: Feature에 조인할 지수/레짐/횡단면 컨텍스트(A-4).
    """
    account = trader.account
    broker = broker or get_broker_for_account(account)

    # 1. 현재 계좌 정보 및 종목 가격 정보 가져오기
    try:
        balance = broker.get_balance()
        cash_balance = balance.cash_balance
        total_asset_value = balance.total_asset_value
    except Exception as e:
        logger.error("계좌 잔고 조회 실패 (%s): %s", account, e)
        return

    try:
        price_dto = broker.get_current_price(stock.symbol)
        current_price = price_dto.price
    except Exception as e:
        logger.error("종목 현재가 조회 실패 (%s): %s", stock.symbol, e)
        return

    # 2. 현재 포지션 정보 파악
    current_qty, avg_entry_price = get_position_info(account, stock)

    # 3. Market Regime 파라미터 튜닝 적용
    position_size_multiplier = Decimal("1.0")
    entry_threshold_offset = Decimal("0.0")
    stop_loss_multiplier = Decimal("1.0")
    take_profit_multiplier = Decimal("1.0")

    if regime_snapshot and regime_snapshot.parameter_payload:
        payload = regime_snapshot.parameter_payload
        position_size_multiplier = Decimal(
            payload.get("position_size_multiplier", "1.0")
        )
        entry_threshold_offset = Decimal(payload.get("entry_threshold_offset", "0.0"))
        stop_loss_multiplier = Decimal(payload.get("stop_loss_multiplier", "1.0"))
        take_profit_multiplier = Decimal(payload.get("take_profit_multiplier", "1.0"))

    adjusted_position_size = trader.position_size_ratio * position_size_multiplier
    adjusted_entry_threshold = trader.entry_threshold + entry_threshold_offset
    adjusted_stop_loss = trader.stop_loss_ratio * stop_loss_multiplier
    adjusted_take_profit = trader.take_profit_ratio * take_profit_multiplier

    final_action = DecisionLog.FinalAction.HOLD
    reason_parts = []
    weighted_score_sum = Decimal("0.0")

    # 최근 100개 캔들 조회 (전략 분석 및 Feature 스냅샷 공용)
    # timeframe은 Trader 설정에서 지정(기본 1일봉). as_of 지정 시 미래 캔들 차단(백테스트).
    timeframe = (trader.config_payload or {}).get(
        "candle_timeframe", Candle.Timeframe.DAY_1
    )
    candle_qs = Candle.objects.filter(stock=stock, timeframe=timeframe)
    if as_of is not None:
        candle_qs = candle_qs.filter(opened_at__lte=as_of)
    candles = list(candle_qs.order_by("-opened_at")[:100])

    # ML Filter가 활성화된 경우 현재 시점의 Feature 스냅샷을 캡처하여 저장
    feature_snapshot = None
    if trader.ml_filter_enabled:
        feature_snapshot = capture_feature_snapshot(
            stock, candles, current_price, timeframe, context
        )

    # 4. 리스크 관리: 손절/익절 조건 체크 (포지션이 있는 경우 우선 처리)
    if current_qty > 0:
        if current_price <= avg_entry_price * (1 - adjusted_stop_loss):
            final_action = DecisionLog.FinalAction.SELL
            reason_parts.append(
                f"[Stop Loss] 현재가({current_price})가 손절선({avg_entry_price * (1 - adjusted_stop_loss):.2f}) 도달."
            )
        elif current_price >= avg_entry_price * (1 + adjusted_take_profit):
            final_action = DecisionLog.FinalAction.SELL
            reason_parts.append(
                f"[Take Profit] 현재가({current_price})가 익절선({avg_entry_price * (1 + adjusted_take_profit):.2f}) 도달."
            )

    # 5. 전략 실행 및 스코어링 (손절/익절 미발생 시)
    if final_action == DecisionLog.FinalAction.HOLD:
        trader_strategies = trader.trader_strategies.filter(is_active=True).order_by(
            "slot"
        )

        active_strategy_count = 0

        for ts in trader_strategies:
            sv = ts.strategy_version
            try:
                runner = StrategyRunner(sv, ts.config_payload)
                res = runner.run(stock, candles, regime_snapshot)

                # 전략 판단 기록 저장
                StrategyDecisionLog.objects.create(
                    trader_run=trader_run,
                    strategy_version=sv,
                    stock=stock,
                    action=res.action,
                    confidence_score=res.confidence_score,
                    feature_snapshot=feature_snapshot,
                    reason=res.reason,
                    decided_at=timezone.now(),
                )

                # 스코어 결합
                # BUY: +confidence, SELL: -confidence, HOLD: 0
                if res.action == "BUY":
                    score = res.confidence_score
                elif res.action == "SELL":
                    score = -res.confidence_score
                else:
                    score = Decimal("0.0")

                weighted_score_sum += score * ts.weight
                active_strategy_count += 1
                reason_parts.append(
                    f"[{sv} Slot {ts.slot}] {res.action} (conf: {res.confidence_score}, weight: {ts.weight})"
                )

            except Exception as e:
                logger.error("전략 실행 에러 (%s): %s", sv, e)
                reason_parts.append(f"[{sv}] 실행 실패 ({e})")

        # 최종 의사결정 수집
        if active_strategy_count > 0:
            if weighted_score_sum >= adjusted_entry_threshold:
                final_action = DecisionLog.FinalAction.BUY
                reason_parts.append(
                    f"종합 점수({weighted_score_sum:.4f}) >= 진입 장벽({adjusted_entry_threshold:.4f}) -> BUY"
                )
            elif weighted_score_sum <= -adjusted_entry_threshold:
                final_action = DecisionLog.FinalAction.SELL
                reason_parts.append(
                    f"종합 점수({weighted_score_sum:.4f}) <= 진입 장벽(-{adjusted_entry_threshold:.4f}) -> SELL"
                )
            else:
                reason_parts.append(
                    f"종합 점수({weighted_score_sum:.4f})가 진입 장벽 내 존재 -> HOLD"
                )

    # 5-1. 레짐 킬스위치: 시장 국면 파라미터가 신규 진입 차단을 지시하면 BUY -> HOLD
    #      (CHAOS/급변 등 상위 레짐 판단이 parameter_payload에 block_new_entries=true를 실음)
    if (
        final_action == DecisionLog.FinalAction.BUY
        and regime_snapshot
        and regime_snapshot.parameter_payload.get("block_new_entries")
    ):
        final_action = DecisionLog.FinalAction.HOLD
        reason_parts.append("[Regime Guard] 신규 진입 차단 레짐 -> BUY 차단(HOLD)")

    # 6. ML Filter 오버라이드 (활성화 시 신규 진입(BUY) 신호에 한해 적용)
    #    손절/익절 등 리스크 관리성 SELL은 보호 목적이므로 필터링하지 않는다.
    ml_output = None
    if trader.ml_filter_enabled and feature_snapshot is not None:
        ml_output = MLFilterEngine().run_inference(trader_run, feature_snapshot)

        risk_threshold = Decimal(
            str(
                trader.config_payload.get(
                    "ml_risk_threshold", DEFAULT_ML_RISK_THRESHOLD
                )
            )
        )
        min_probability = Decimal(
            str(
                trader.config_payload.get(
                    "ml_min_trade_probability", DEFAULT_ML_MIN_TRADE_PROBABILITY
                )
            )
        )

        if final_action == DecisionLog.FinalAction.BUY and (
            ml_output.risk_score >= risk_threshold
            or ml_output.trade_probability <= min_probability
        ):
            final_action = DecisionLog.FinalAction.HOLD
            reason_parts.append(
                f"[ML Filter] 리스크({ml_output.risk_score}) >= {risk_threshold} "
                f"또는 성공확률({ml_output.trade_probability}) <= {min_probability} "
                f"-> BUY 차단(HOLD)"
            )
        else:
            reason_parts.append(
                f"[ML Filter] 통과 (리스크: {ml_output.risk_score}, 성공확률: {ml_output.trade_probability})"
            )

    # 6-1. 리스크 가드: 신규 진입(BUY) 규모가 포트폴리오 한도를 넘으면 차단
    intended_qty = Decimal("0")
    if final_action == DecisionLog.FinalAction.BUY:
        intended_qty = compute_target_buy_qty(
            trader=trader,
            total_asset_value=total_asset_value,
            current_price=current_price,
            adjusted_position_size=adjusted_position_size,
            weighted_score_sum=weighted_score_sum,
            feature_snapshot=feature_snapshot,
            ml_output=ml_output,
            candles=candles,
        )
        day_start_equity = get_day_start_equity(account, timezone.now())
        day_pnl = (
            float(total_asset_value) - day_start_equity
            if day_start_equity is not None
            else 0.0
        )
        portfolio = PortfolioState(
            equity=float(total_asset_value),
            gross_exposure=float(total_asset_value - cash_balance),
            num_positions=get_open_position_count(account),
            day_pnl=day_pnl,  # 당일 시작 자본 대비 손익 → 손실 한도 킬스위치
        )
        guard = can_open_new_position(
            portfolio,
            float(intended_qty) * float(current_price),
            _risk_limits(trader),
        )
        if not guard.allowed:
            final_action = DecisionLog.FinalAction.HOLD
            reason_parts.append(f"[Risk Guard] {guard.reason}")

    # 7. 최종 결정 의사록 작성
    decision_reason = " | ".join(reason_parts)
    decision = DecisionLog.objects.create(
        trader_run=trader_run,
        stock=stock,
        ml_output_log=ml_output,
        final_action=final_action,
        final_score=weighted_score_sum,
        position_size_ratio=adjusted_position_size,
        stop_loss_ratio=adjusted_stop_loss,
        take_profit_ratio=adjusted_take_profit,
        max_exposure_ratio=trader.max_exposure_ratio,
        target_quantity=Decimal("0.0"),  # 아래에서 계산 후 업데이트
        reason=decision_reason,
        decision_payload={
            "weighted_score_sum": str(weighted_score_sum),
            "entry_threshold": str(adjusted_entry_threshold),
            "ml_filter_enabled": trader.ml_filter_enabled,
            "ml_output_id": ml_output.id if ml_output else None,
        },
        decided_at=timezone.now(),
    )

    # 8. 주문 집행 및 체결 시뮬레이션/기록 (Atomic 트랜잭션 사용)
    if final_action == DecisionLog.FinalAction.BUY:
        target_qty = intended_qty  # 6-1에서 계산한 목표 수량 재사용

        if target_qty > current_qty:
            qty_to_buy = target_qty - current_qty
            required_cash = qty_to_buy * current_price

            if required_cash > cash_balance:
                # 잔고에 맞춰 조절
                qty_to_buy = Decimal(str(math.floor(cash_balance / current_price)))

            if qty_to_buy > 0:
                decision.target_quantity = qty_to_buy
                decision.save(update_fields=["target_quantity"])

                execute_order(
                    trader=trader,
                    decision=decision,
                    stock=stock,
                    side=Order.Side.BUY,
                    quantity=qty_to_buy,
                    price=current_price,
                    broker=broker,
                )

    elif final_action == DecisionLog.FinalAction.SELL:
        if current_qty > 0:
            decision.target_quantity = current_qty
            decision.save(update_fields=["target_quantity"])

            execute_order(
                trader=trader,
                decision=decision,
                stock=stock,
                side=Order.Side.SELL,
                quantity=current_qty,
                price=current_price,
                broker=broker,
            )


def execute_order(
    trader: Trader,
    decision: DecisionLog,
    stock: Stock,
    side: str,
    quantity: Decimal,
    price: Decimal,
    broker,
):
    """
    실제 증권사 주문 전송 및 내부 장부 기록을 실행합니다.
    """
    logger.info(
        "[%s] %s 주문 전송 시작: %s (수량: %s, 단가: %s)",
        trader.name,
        side,
        stock.symbol,
        quantity,
        price,
    )

    try:
        # API 주문 제출
        order_res = broker.create_order(
            symbol=stock.symbol,
            side=side,
            quantity=quantity,
            price=None,  # 시장가 주문 디폴트
        )

        with transaction.atomic():
            # 주문 기록 생성
            order = Order.objects.create(
                trader_decision_log=decision,
                account=trader.account,
                stock=stock,
                side=side,
                order_type=Order.OrderType.MARKET,
                quantity=quantity,
                status=Order.Status.FILLED
                if order_res.success
                else Order.Status.REJECTED,
                broker_order_id=order_res.order_id,
                request_payload={
                    "symbol": stock.symbol,
                    "side": side,
                    "quantity": str(quantity),
                },
                response_payload=order_res.raw_payload
                if isinstance(order_res.raw_payload, dict)
                else {},
                requested_at=timezone.now(),
            )

            if order_res.success:
                # 브로커가 체결가/비용을 제공하면(BacktestBroker·일부 실브로커) 그대로 반영하여
                # 백테스트-실거래가 동일 비용 모델(core.backtest.costs)로 정합되도록 한다.
                # 미제공 시 요청가(price)로 폴백(기존 동작 유지).
                raw = (
                    order_res.raw_payload
                    if isinstance(order_res.raw_payload, dict)
                    else {}
                )
                fill_price = (
                    Decimal(str(raw["fill_price"])) if "fill_price" in raw else price
                )
                fee = Decimal(str(raw.get("commission", "0")))
                tax = Decimal(str(raw.get("tax", "0")))
                slippage = Decimal(str(raw.get("slippage_cost", "0")))

                execution = TradeExecution.objects.create(
                    order=order,
                    account=trader.account,
                    stock=stock,
                    side=side,
                    executed_quantity=quantity,
                    executed_price=fill_price,
                    fee_amount=fee,
                    tax_amount=tax,
                    slippage_amount=slippage,
                    executed_at=timezone.now(),
                )

                # 현금 원장 업데이트 (수수료·세금 반영)
                notional = quantity * fill_price
                if side == Order.Side.BUY:
                    cash_change = -(notional + fee + tax)
                else:
                    cash_change = notional - fee - tax
                CashLedger.objects.create(
                    account=trader.account,
                    trade_execution=execution,
                    event_type=CashLedger.EventType.BUY
                    if side == Order.Side.BUY
                    else CashLedger.EventType.SELL,
                    amount=cash_change,
                    occurred_at=timezone.now(),
                    reason=f"[{trader.name}] {stock.symbol} {side} 체결 반영",
                )

                # 포지션 원장 업데이트
                qty_change = quantity if side == Order.Side.BUY else -quantity
                PositionLedger.objects.create(
                    account=trader.account,
                    stock=stock,
                    trade_execution=execution,
                    quantity_delta=qty_change,
                    price=fill_price,
                    occurred_at=timezone.now(),
                    reason=f"[{trader.name}] {stock.symbol} {side} 체결 반영",
                )
                logger.info("[%s] 주문 처리 성공: %s", trader.name, order.broker_order_id)
            else:
                logger.error("[%s] 증권사 주문 거부: %s", trader.name, order_res.error_message)

    except Exception as e:
        logger.exception("[%s] 주문 처리 도중 치명적인 오류 발생: %s", trader.name, e)
