from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from core.backtest.costs import (
    CostConfig,
    SIDE_BUY,
    SIDE_SELL,
    estimate_fill,
    round_to_tick,
    round_trip_cost_ratio,
    slippage_per_share,
    tick_size,
    transaction_cost,
)
from core.backtest.broker import BacktestBroker
from core.backtest.engine import BacktestConfig, run_backtest


# 무비용 설정 (부호 검증용)
ZERO_COST = CostConfig(
    commission_rate=Decimal("0"),
    sell_tax_rate=Decimal("0"),
    half_spread_ticks=Decimal("0"),
    impact_coef=Decimal("0"),
)


def _bar(price, volume=100000):
    p = Decimal(str(price))
    return SimpleNamespace(
        open_price=p,
        high_price=p,
        low_price=p,
        close_price=p,
        volume=Decimal(str(volume)),
        opened_at=None,
    )


class TickSizeTestCase(SimpleTestCase):
    def test_tick_bands(self):
        self.assertEqual(tick_size(Decimal("1500")), Decimal("1"))
        self.assertEqual(tick_size(Decimal("3000")), Decimal("5"))
        self.assertEqual(tick_size(Decimal("15000")), Decimal("10"))
        self.assertEqual(tick_size(Decimal("35000")), Decimal("50"))
        self.assertEqual(tick_size(Decimal("70000")), Decimal("100"))
        self.assertEqual(tick_size(Decimal("300000")), Decimal("500"))
        self.assertEqual(tick_size(Decimal("800000")), Decimal("1000"))

    def test_round_to_tick(self):
        # 70,123 -> 100원 그리드 -> 70,100
        self.assertEqual(round_to_tick(Decimal("70123")), Decimal("70100"))
        # 70,150 -> 반올림 -> 70,200
        self.assertEqual(round_to_tick(Decimal("70150")), Decimal("70200"))


class TransactionCostTestCase(SimpleTestCase):
    def test_buy_has_no_tax(self):
        commission, tax = transaction_cost(SIDE_BUY, Decimal("70000"), Decimal("10"))
        self.assertEqual(tax, Decimal("0"))
        # 700,000 * 0.00015 = 105
        self.assertEqual(commission, Decimal("105.0000000"))

    def test_sell_has_tax(self):
        commission, tax = transaction_cost(SIDE_SELL, Decimal("70000"), Decimal("10"))
        # 700,000 * 0.0018 = 1260
        self.assertEqual(tax, Decimal("1260.000000"))

    def test_round_trip_cost_ratio(self):
        cfg = CostConfig()
        # 0.00015*2 + 0.0018 = 0.0021
        self.assertEqual(
            round_trip_cost_ratio(Decimal("70000"), cfg), Decimal("0.0021")
        )


class SlippageTestCase(SimpleTestCase):
    def test_spread_only_when_no_volume(self):
        # minute_volume 없으면 스프레드(1틱=100원)만
        slip = slippage_per_share(Decimal("70000"), Decimal("10"), minute_volume=None)
        self.assertEqual(slip, Decimal("100"))

    def test_impact_grows_with_size(self):
        small = slippage_per_share(
            Decimal("70000"), Decimal("10"), minute_volume=Decimal("10000")
        )
        large = slippage_per_share(
            Decimal("70000"), Decimal("1000"), minute_volume=Decimal("10000")
        )
        self.assertGreater(large, small)


class EstimateFillTestCase(SimpleTestCase):
    def test_buy_fills_worse_and_snaps_to_tick(self):
        fill = estimate_fill(
            SIDE_BUY, Decimal("70000"), Decimal("10"), minute_volume=None
        )
        # 매수는 기준가보다 높게(불리) 체결, 호가단위 정렬
        self.assertGreaterEqual(fill.fill_price, Decimal("70000"))
        self.assertEqual(fill.fill_price % Decimal("100"), Decimal("0"))
        self.assertEqual(fill.tax, Decimal("0"))  # 매수 무세금
        self.assertGreater(fill.total_cost, Decimal("0"))

    def test_sell_fills_worse_lower(self):
        fill = estimate_fill(
            SIDE_SELL, Decimal("70000"), Decimal("10"), minute_volume=None
        )
        self.assertLessEqual(fill.fill_price, Decimal("70000"))
        self.assertGreater(fill.tax, Decimal("0"))  # 매도 세금 존재

    def test_total_cost_components(self):
        fill = estimate_fill(
            SIDE_SELL, Decimal("70000"), Decimal("10"), minute_volume=Decimal("5000")
        )
        self.assertEqual(
            fill.total_cost,
            fill.slippage_cost + fill.commission + fill.tax,
        )


class BacktestBrokerTestCase(SimpleTestCase):
    def setUp(self):
        self.broker = BacktestBroker(Decimal("10000000"))
        self.broker.set_market("A", Decimal("70000"), Decimal("100000"))

    def test_buy_updates_cash_and_position(self):
        res = self.broker.create_order("A", SIDE_BUY, Decimal("10"))
        self.assertTrue(res.success)
        self.assertEqual(self.broker.positions["A"], Decimal("10"))
        self.assertLess(self.broker.cash, Decimal("10000000"))
        # 슬리피지로 체결가 >= 기준가
        self.assertGreaterEqual(Decimal(res.raw_payload["fill_price"]), Decimal("70000"))

    def test_balance_marks_to_market(self):
        self.broker.create_order("A", SIDE_BUY, Decimal("10"))
        bal = self.broker.get_balance()
        # 총자산 ≈ 현금 + 10주 평가액
        self.assertEqual(bal.total_asset_value, bal.cash_balance + Decimal("10") * Decimal("70000"))

    def test_sell_returns_cash_and_flattens(self):
        self.broker.create_order("A", SIDE_BUY, Decimal("10"))
        cash_after_buy = self.broker.cash
        res = self.broker.create_order("A", SIDE_SELL, Decimal("10"))
        self.assertTrue(res.success)
        self.assertEqual(self.broker.positions["A"], Decimal("0"))
        self.assertGreater(self.broker.cash, cash_after_buy)

    def test_reject_insufficient_cash(self):
        res = self.broker.create_order("A", SIDE_BUY, Decimal("1000000"))
        self.assertFalse(res.success)
        self.assertIn("잔고", res.error_message)

    def test_reject_oversell(self):
        res = self.broker.create_order("A", SIDE_SELL, Decimal("5"))
        self.assertFalse(res.success)
        self.assertIn("수량", res.error_message)


class BacktestEngineTestCase(SimpleTestCase):
    def _rising(self, n=50, start=100.0, step=1.0):
        # 과거 -> 최신 순 상승 캔들
        return [_bar(start + i * step) for i in range(n)]

    def _always_buy(self, feats, index, candle):
        return SIDE_BUY

    def test_no_lookahead_entry_after_signal(self):
        candles = self._rising()
        cfg = BacktestConfig(warmup=20, cost=ZERO_COST)
        result = run_backtest(candles, self._always_buy, cfg)
        self.assertGreater(result.metrics["num_trades"], 0)
        # 진입은 신호 봉(t) 이후(t+1 이상)에 체결되어야 한다 -> entry_index > warmup
        for tr in result.trades:
            self.assertGreater(tr.entry_index, cfg.warmup)
            self.assertGreater(tr.exit_index, tr.entry_index)

    def test_profit_on_uptrend_zero_cost(self):
        candles = self._rising()
        result = run_backtest(candles, self._always_buy, BacktestConfig(warmup=20, cost=ZERO_COST))
        self.assertGreater(result.metrics["net_pnl"], 0.0)
        self.assertEqual(result.metrics["total_cost"], 0.0)

    def test_cost_reduces_pnl(self):
        candles = self._rising()
        zero = run_backtest(candles, self._always_buy, BacktestConfig(warmup=20, cost=ZERO_COST))
        withcost = run_backtest(candles, self._always_buy, BacktestConfig(warmup=20, cost=CostConfig()))
        self.assertLess(withcost.metrics["net_pnl"], zero.metrics["net_pnl"])
        self.assertGreater(withcost.metrics["total_cost"], 0.0)

    def test_no_signal_no_trades(self):
        candles = self._rising()
        result = run_backtest(candles, lambda f, i, c: "HOLD", BacktestConfig(warmup=20))
        self.assertEqual(result.metrics["num_trades"], 0)
        self.assertEqual(result.metrics["net_pnl"], 0.0)


from core.risk.sizing import (
    SizingConfig,
    breakeven_probability,
    compute_position_size,
    kelly_fraction,
    volatility_target_shares,
)
from core.risk.guard import (
    GuardDecision,
    PortfolioState,
    RiskLimits,
    can_open_new_position,
    daily_loss_breached,
)


class SizingTestCase(SimpleTestCase):
    def test_breakeven_probability(self):
        # (lower+cost)/(upper+lower) = (0.004+0.0021)/0.008
        self.assertAlmostEqual(breakeven_probability(0.004, 0.004, 0.0021), 0.7625, places=4)

    def test_kelly_positive_for_strong_edge(self):
        self.assertGreater(kelly_fraction(0.9, 0.004, 0.004), 0.0)
        # 승률이 손익분기 이하이면 0 근처/음수 -> 클리핑 0
        self.assertEqual(kelly_fraction(0.2, 0.004, 0.004), 0.0)

    def test_volatility_target_shrinks_with_vol(self):
        low = volatility_target_shares(10_000_000, 70000, 0.002, 0.002)
        high = volatility_target_shares(10_000_000, 70000, 0.008, 0.002)
        self.assertGreater(low, high)

    def test_no_edge_returns_zero(self):
        res = compute_position_size(
            capital=10_000_000, price=70000, prob=0.5, instrument_vol=0.002,
            upper=0.004, lower=0.004, cost=0.0021,
        )
        self.assertEqual(res.shares, 0)

    def test_positive_size_with_edge(self):
        res = compute_position_size(
            capital=10_000_000, price=70000, prob=0.9, instrument_vol=0.002,
            upper=0.004, lower=0.004, cost=0.0021,
        )
        self.assertGreater(res.shares, 0)

    def test_liquidity_cap_binds(self):
        # 분당거래량 100주 * 참여율 0.1 = 10주 상한
        res = compute_position_size(
            capital=10_000_000, price=70000, prob=0.9, instrument_vol=0.002,
            upper=0.004, lower=0.004, cost=0.0021, minute_volume=100,
        )
        self.assertEqual(res.shares, 10)


class RiskGuardTestCase(SimpleTestCase):
    def _state(self, **kw):
        base = dict(equity=10_000_000.0, gross_exposure=0.0, num_positions=0, day_pnl=0.0)
        base.update(kw)
        return PortfolioState(**base)

    def test_allow_under_limits(self):
        d = can_open_new_position(self._state(), add_notional=1_000_000)
        self.assertTrue(d.allowed)

    def test_regime_block(self):
        d = can_open_new_position(self._state(regime_blocked=True), add_notional=1_000)
        self.assertFalse(d.allowed)
        self.assertIn("레짐", d.reason)

    def test_daily_loss_kill_switch(self):
        state = self._state(day_pnl=-400_000)  # -4% > 3% 한도
        self.assertTrue(daily_loss_breached(state, RiskLimits()))
        d = can_open_new_position(state, add_notional=1_000)
        self.assertFalse(d.allowed)
        self.assertIn("킬스위치", d.reason)

    def test_max_positions(self):
        d = can_open_new_position(self._state(num_positions=10), add_notional=1_000)
        self.assertFalse(d.allowed)

    def test_position_ratio_cap(self):
        # 3,000,000 / 10,000,000 = 30% > 20% 상한
        d = can_open_new_position(self._state(), add_notional=3_000_000)
        self.assertFalse(d.allowed)
        self.assertIn("종목당", d.reason)

    def test_gross_exposure_cap(self):
        state = self._state(gross_exposure=9_500_000)
        d = can_open_new_position(state, add_notional=1_000_000, limits=RiskLimits(max_position_ratio=1.0))
        self.assertFalse(d.allowed)
        self.assertIn("총노출", d.reason)
