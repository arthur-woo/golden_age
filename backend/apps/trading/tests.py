from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.account.models import (
    Account,
    ExecutionRun,
    BalanceSnapshot,
    CashLedger,
    PositionLedger,
)
from apps.stock.models import Stock
from apps.market.models import Candle
from apps.trading.models import (
    Trader,
    Strategy,
    StrategyVersion,
    TraderStrategy,
    TraderExecutionRun,
    StrategyDecisionLog,
    MLOutputLog,
    DecisionLog,
)
from apps.market.models import FeatureSnapshot
from apps.order.models import Order, TradeExecution
from core.pipeline.strategy_runner import StrategyResult, StrategyRunner
from core.pipeline.account_executor import execute_account_run

User = get_user_model()


class MockStrategy:
    """단위 테스트를 위한 Mock Strategy 클래스"""

    def __init__(self, config):
        self.config = config

    def run(self, stock, candles, regime_snapshot):
        action = self.config.get("action", "BUY")
        confidence_score = Decimal(str(self.config.get("confidence_score", "0.8")))
        return StrategyResult(
            action=action,
            confidence_score=confidence_score,
            reason="Mock Strategy Execution Successful",
        )


class TradingPipelineTestCase(TestCase):
    def setUp(self):
        # 1. 테스트용 기본 데이터 생성
        self.user = User.objects.create_user(
            username="trading_tester", password="password"
        )

        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="9876543201",
            name="Pipeline Test Account",
            app_key_encrypted="key",
            app_secret_encrypted="secret",
        )

        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI,
            symbol="005930",
            name="삼성전자",
            is_active=True,
        )

        # 시장 분석을 위한 20일치 일봉 캔들 채우기
        start_time = timezone.now() - timedelta(days=30)
        for i in range(25):
            Candle.objects.create(
                stock=self.stock,
                timeframe=Candle.Timeframe.DAY_1,
                opened_at=start_time + timedelta(days=i),
                open_price=Decimal("70000.00"),
                high_price=Decimal("71000.00"),
                low_price=Decimal("69000.00"),
                close_price=Decimal("70500.00"),
                volume=Decimal("1000000"),
                source="mock",
            )

        # 2. Trader 및 Strategy 생성
        self.trader = Trader.objects.create(
            account=self.account,
            name="Test Bot",
            code="TEST_BOT",
            status=Trader.Status.ACTIVE,
            position_size_ratio=Decimal("0.1"),  # 총 자산의 10%
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),  # 5%
            take_profit_ratio=Decimal("0.1"),  # 10%
            max_exposure_ratio=Decimal("0.3"),
        )

        self.strategy = Strategy.objects.create(
            owner=self.user,
            namespace="tester",
            name="Mock Test Strategy",
            code="MOCK_STRAT",
        )

        self.strategy_version = StrategyVersion.objects.create(
            strategy=self.strategy,
            version="v1.0.0",
            module_path="apps.trading.tests",
            class_name="MockStrategy",
            status=StrategyVersion.Status.ACTIVE,
        )

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_pipeline_buy_flow(self, mock_get_broker_acc, mock_get_broker_trd):
        # Broker Mock 설정
        mock_broker = MagicMock()

        # balance mock
        mock_balance = MagicMock()
        mock_balance.cash_balance = Decimal("10000000")  # 1천만 원
        mock_balance.total_asset_value = Decimal("10000000")
        mock_balance.raw_payload = {"mock": True}
        mock_broker.get_balance.return_value = mock_balance

        # price mock
        mock_price = MagicMock()
        mock_price.price = Decimal("70000")  # 주당 70,000원
        mock_broker.get_current_price.return_value = mock_price

        # order mock
        mock_order_res = MagicMock()
        mock_order_res.success = True
        mock_order_res.order_id = "ORDER12345"
        mock_order_res.raw_payload = {"rt_cd": "0"}
        mock_broker.create_order.return_value = mock_order_res

        mock_get_broker_acc.return_value = mock_broker
        mock_get_broker_trd.return_value = mock_broker

        # Trader에 Strategy 연결 (Slot 1, BUY 신호 방출되도록 세팅)
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

        # 실행!
        execute_account_run(self.account.id)

        # 검증 1: ExecutionRun 상태 성공 확인
        self.assertEqual(ExecutionRun.objects.count(), 1)
        run = ExecutionRun.objects.first()
        self.assertEqual(run.status, ExecutionRun.Status.SUCCESS)

        # 검증 2: TraderExecutionRun 성공 확인
        self.assertEqual(TraderExecutionRun.objects.count(), 1)
        tr_run = TraderExecutionRun.objects.first()
        self.assertEqual(tr_run.status, TraderExecutionRun.Status.SUCCESS)

        # 검증 3: 전략 의사결정 로그 생성 확인
        self.assertEqual(StrategyDecisionLog.objects.count(), 1)
        strat_log = StrategyDecisionLog.objects.first()
        self.assertEqual(strat_log.action, "BUY")
        self.assertEqual(strat_log.confidence_score, Decimal("0.8"))

        # 검증 4: 트레이더 최종 의사결정 로그 확인
        self.assertEqual(DecisionLog.objects.count(), 1)
        dec_log = DecisionLog.objects.first()
        self.assertEqual(dec_log.final_action, "BUY")
        # 10% position size이나 SIDEWAYS 국면으로 0.7 곱해짐 -> 7% (700,000원) -> 700,000 / 70,000 = 10주 매수
        self.assertEqual(dec_log.target_quantity, Decimal("10"))

        # 검증 5: 데이터베이스 주문 및 체결 레코드 점검
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.first()
        self.assertEqual(order.side, Order.Side.BUY)
        self.assertEqual(order.quantity, Decimal("10"))
        self.assertEqual(order.status, Order.Status.FILLED)

        self.assertEqual(TradeExecution.objects.count(), 1)
        exec_record = TradeExecution.objects.first()
        self.assertEqual(exec_record.executed_price, Decimal("70000"))
        self.assertEqual(exec_record.executed_quantity, Decimal("10"))

        # 검증 6: 현금 원장 및 포지션 원장 업데이트 확인
        self.assertEqual(CashLedger.objects.count(), 1)
        cash = CashLedger.objects.first()
        self.assertEqual(cash.amount, Decimal("-700000.00"))  # 10주 * 70,000원

        self.assertEqual(PositionLedger.objects.count(), 1)
        pos = PositionLedger.objects.first()
        self.assertEqual(pos.quantity_delta, Decimal("10"))

        # 검증 7: 계좌 잔고 스냅샷 확인
        self.assertEqual(BalanceSnapshot.objects.count(), 1)

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_pipeline_risk_stop_loss(self, mock_get_broker_acc, mock_get_broker_trd):
        # 1. 초기 보유 포지션 설정 (삼성전자 10주 평단 70,000원에 매수해둔 상태)
        # PositionLedger에 인위적으로 기록 삽입
        PositionLedger.objects.create(
            account=self.account,
            stock=self.stock,
            quantity_delta=Decimal("10"),
            price=Decimal("70000"),
            occurred_at=timezone.now() - timedelta(hours=1),
            reason="이전 거래 기록",
        )

        # Broker Mock 설정
        mock_broker = MagicMock()

        # balance mock
        mock_balance = MagicMock()
        mock_balance.cash_balance = Decimal("5000000")
        mock_balance.total_asset_value = Decimal("5650000")  # 주식 평가액 포함
        mock_broker.get_balance.return_value = mock_balance

        # price mock: 현재가가 65,000원으로 급락 (평단 70,000원 대비 7.1% 하락 -> 5% 손절선 터치)
        mock_price = MagicMock()
        mock_price.price = Decimal("65000")
        mock_broker.get_current_price.return_value = mock_price

        # order mock
        mock_order_res = MagicMock()
        mock_order_res.success = True
        mock_order_res.order_id = "ORDER555"
        mock_broker.create_order.return_value = mock_order_res

        mock_get_broker_acc.return_value = mock_broker
        mock_get_broker_trd.return_value = mock_broker

        # Trader에 Strategy 연결 (전략은 HOLD를 방출하지만 리스크 매니지먼트가 우선 실행되어야 함)
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "HOLD", "confidence_score": "0.0"},
            is_active=True,
        )

        # 실행!
        execute_account_run(self.account.id)

        # 검증 1: 최종 매매 판단이 SELL(손절)이어야 함
        self.assertEqual(DecisionLog.objects.count(), 1)
        dec_log = DecisionLog.objects.first()
        self.assertEqual(dec_log.final_action, DecisionLog.FinalAction.SELL)
        self.assertIn("[Stop Loss]", dec_log.reason)
        self.assertEqual(dec_log.target_quantity, Decimal("10"))  # 전량 매도

        # 검증 2: 주문이 나갔는지 검증
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.first()
        self.assertEqual(order.side, Order.Side.SELL)
        self.assertEqual(order.quantity, Decimal("10"))

        # 검증 3: 원장(Ledger)에 매도 반영 확인 (-10주)
        # setUp에서 1개 + test에서 1개 = 총 2개
        self.assertEqual(PositionLedger.objects.count(), 2)
        latest_pos = PositionLedger.objects.order_by("-occurred_at").first()
        self.assertEqual(latest_pos.quantity_delta, Decimal("-10"))

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_ml_filter_blocks_buy(self, mock_get_broker_acc, mock_get_broker_trd):
        """ML Filter가 활성화되고 리스크 점수가 높으면 BUY 신호가 HOLD로 차단되어야 한다."""
        # ML Filter 활성화
        self.trader.ml_filter_enabled = True
        self.trader.save(update_fields=["ml_filter_enabled"])

        # Broker Mock (매수 flow와 동일)
        mock_broker = MagicMock()
        mock_balance = MagicMock()
        mock_balance.cash_balance = Decimal("10000000")
        mock_balance.total_asset_value = Decimal("10000000")
        mock_balance.raw_payload = {"mock": True}
        mock_broker.get_balance.return_value = mock_balance

        mock_price = MagicMock()
        mock_price.price = Decimal("70000")
        mock_broker.get_current_price.return_value = mock_price

        mock_order_res = MagicMock()
        mock_order_res.success = True
        mock_order_res.order_id = "ORDER_ML"
        mock_order_res.raw_payload = {"rt_cd": "0"}
        mock_broker.create_order.return_value = mock_order_res

        mock_get_broker_acc.return_value = mock_broker
        mock_get_broker_trd.return_value = mock_broker

        # 전략은 강한 BUY 신호를 방출 (ML 필터가 없다면 반드시 매수됨)
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

        execute_account_run(self.account.id)

        # 검증 1: Feature 스냅샷이 캡처되었는지 (setUp의 캔들은 종가가 일정 -> RSI=100 -> 고위험)
        self.assertEqual(FeatureSnapshot.objects.count(), 1)

        # 검증 2: ML 판단 로그가 생성되고 고위험 값이 기록되었는지
        self.assertEqual(MLOutputLog.objects.count(), 1)
        ml_log = MLOutputLog.objects.first()
        self.assertEqual(ml_log.risk_score, Decimal("0.75"))
        self.assertEqual(ml_log.trade_probability, Decimal("0.45"))

        # 검증 3: 최종 판단이 BUY가 아니라 HOLD로 오버라이드 되었는지
        self.assertEqual(DecisionLog.objects.count(), 1)
        dec_log = DecisionLog.objects.first()
        self.assertEqual(dec_log.final_action, DecisionLog.FinalAction.HOLD)
        self.assertIn("[ML Filter]", dec_log.reason)
        self.assertEqual(dec_log.ml_output_log_id, ml_log.id)

        # 검증 4: 전략 자체는 BUY를 방출했음 (필터 이전 단계 기록은 유지)
        strat_log = StrategyDecisionLog.objects.first()
        self.assertEqual(strat_log.action, "BUY")
        self.assertEqual(
            strat_log.feature_snapshot_id, FeatureSnapshot.objects.first().id
        )

        # 검증 5: 매수가 차단되어 주문/체결이 발생하지 않았는지
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(TradeExecution.objects.count(), 0)

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_advanced_sizing_liquidity_cap(self, mock_acc, mock_trd):
        """advanced_sizing 활성화 시 core.risk.sizing이 주문 수량을 산정하고 유동성 상한이 적용된다."""
        # ADV 50주 * 참여율 0.1 = 5주 상한이 최종 수량을 지배하도록 구성
        self.trader.config_payload = {
            "advanced_sizing": True,
            "sizing": {"adv": 50, "adv_participation": 0.1},
        }
        self.trader.save(update_fields=["config_payload"])

        broker = MagicMock()
        bal = MagicMock()
        bal.cash_balance = Decimal("10000000")
        bal.total_asset_value = Decimal("10000000")
        bal.raw_payload = {}
        broker.get_balance.return_value = bal
        price = MagicMock()
        price.price = Decimal("70000")
        broker.get_current_price.return_value = price
        order_res = MagicMock()
        order_res.success = True
        order_res.order_id = "O1"
        order_res.raw_payload = {}
        broker.create_order.return_value = order_res
        mock_acc.return_value = broker
        mock_trd.return_value = broker

        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

        execute_account_run(self.account.id)

        dec = DecisionLog.objects.first()
        self.assertEqual(dec.final_action, DecisionLog.FinalAction.BUY)
        self.assertEqual(dec.target_quantity, Decimal("5"))  # 유동성 상한 지배
        self.assertEqual(Order.objects.first().quantity, Decimal("5"))

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_risk_guard_blocks_oversized_buy(self, mock_acc, mock_trd):
        """종목당 노출 상한(기본 20%)을 초과하는 큰 진입은 리스크 가드가 BUY를 차단한다."""
        self.trader.position_size_ratio = Decimal("0.5")
        self.trader.max_exposure_ratio = Decimal("0.5")
        self.trader.save(update_fields=["position_size_ratio", "max_exposure_ratio"])

        broker = MagicMock()
        bal = MagicMock()
        bal.cash_balance = Decimal("10000000")
        bal.total_asset_value = Decimal("10000000")
        bal.raw_payload = {}
        broker.get_balance.return_value = bal
        price = MagicMock()
        price.price = Decimal("70000")
        broker.get_current_price.return_value = price
        ores = MagicMock()
        ores.success = True
        ores.order_id = "O"
        ores.raw_payload = {}
        broker.create_order.return_value = ores
        mock_acc.return_value = broker
        mock_trd.return_value = broker

        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )
        execute_account_run(self.account.id)

        dec = DecisionLog.objects.first()
        self.assertEqual(dec.final_action, DecisionLog.FinalAction.HOLD)
        self.assertIn("[Risk Guard]", dec.reason)
        self.assertEqual(Order.objects.count(), 0)

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    @patch("core.pipeline.account_executor.get_broker_for_account")
    def test_daily_loss_kill_switch_blocks_buy(self, mock_acc, mock_trd):
        """당일 시작 자본 대비 손실이 한도(3%)를 넘으면 신규 진입이 차단된다."""
        # 당일 시작 잔고 스냅샷(10.5M) 선기록 → 현재 10M이면 -4.8% 손실
        BalanceSnapshot.objects.create(
            account=self.account,
            cash_balance=Decimal("10500000"),
            total_asset_value=Decimal("10500000"),
            snapshot_payload={},
            snapshotted_at=timezone.now() - timedelta(minutes=5),
        )
        broker = MagicMock()
        bal = MagicMock()
        bal.cash_balance = Decimal("10000000")
        bal.total_asset_value = Decimal("10000000")
        bal.raw_payload = {}
        broker.get_balance.return_value = bal
        price = MagicMock()
        price.price = Decimal("70000")
        broker.get_current_price.return_value = price
        ores = MagicMock()
        ores.success = True
        ores.order_id = "O"
        ores.raw_payload = {}
        broker.create_order.return_value = ores
        mock_acc.return_value = broker
        mock_trd.return_value = broker

        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.strategy_version,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )
        execute_account_run(self.account.id)

        dec = DecisionLog.objects.first()
        self.assertEqual(dec.final_action, DecisionLog.FinalAction.HOLD)
        self.assertIn("킬스위치", dec.reason)
        self.assertEqual(Order.objects.count(), 0)


class StrategyIsolationTestCase(TestCase):
    """개발자별(Hong/Kim) 격리 전략이 StrategyRunner로 로딩되어 결정적으로 동작하는지 검증."""

    def setUp(self):
        self.user = User.objects.create_user(username="dev_isolation", password="pw")
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000660", name="SK하이닉스", is_active=True
        )

    def _make_version(self, namespace, code, module_path, class_name):
        strategy = Strategy.objects.create(
            owner=self.user, namespace=namespace, name=code, code=code
        )
        return StrategyVersion.objects.create(
            strategy=strategy,
            version="v1.0.0",
            module_path=module_path,
            class_name=class_name,
            status=StrategyVersion.Status.ACTIVE,
        )

    def _candles(self, closes):
        """최신 -> 과거 순으로 정렬된 (미저장) Candle 리스트를 만든다."""
        return [Candle(close_price=Decimal(str(c))) for c in closes]

    def test_hong_moving_average_golden_cross(self):
        sv = self._make_version(
            "hong",
            "MA_CROSS",
            "strategies.hong.moving_average",
            "HongMovingAverageStrategy",
        )
        # 최신 캔들만 급등(200) + 나머지 20개는 100 -> 직전 fast<=slow, 현재 fast>slow = 골든크로스
        candles = self._candles([200] + [100] * 20)

        runner = StrategyRunner(sv, {})
        result = runner.run(self.stock, candles, None)
        self.assertEqual(result.action, "BUY")

        # 결정적(Deterministic): 동일 입력 -> 동일 결과
        again = runner.run(self.stock, candles, None)
        self.assertEqual(again.action, result.action)
        self.assertEqual(again.confidence_score, result.confidence_score)

    def test_kim_rsi_overbought(self):
        sv = self._make_version(
            "kim",
            "RSI",
            "strategies.kim.rsi_strategy",
            "KimRsiStrategy",
        )
        # 최신 -> 과거 순으로 계속 하락값 = 시간순으로는 계속 상승 -> RSI 100 -> 과매수 -> SELL
        candles = self._candles(
            [
                115,
                114,
                113,
                112,
                111,
                110,
                109,
                108,
                107,
                106,
                105,
                104,
                103,
                102,
                101,
                100,
            ]
        )

        runner = StrategyRunner(sv, {})
        result = runner.run(self.stock, candles, None)
        self.assertEqual(result.action, "SELL")

        again = runner.run(self.stock, candles, None)
        self.assertEqual(again.action, result.action)


class RegimeGuardTestCase(TestCase):
    """레짐 킬스위치가 BUY 신호를 HOLD로 차단하는지 검증."""

    def setUp(self):
        self.user = User.objects.create_user(username="regime_tester", password="pw")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="111",
            name="Regime Acc",
            app_key_encrypted="k",
            app_secret_encrypted="s",
        )
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000660", name="SK하이닉스"
        )
        self.trader = Trader.objects.create(
            account=self.account,
            name="Bot",
            code="BOT",
            position_size_ratio=Decimal("0.1"),
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),
            take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
        )
        strategy = Strategy.objects.create(
            owner=self.user, namespace="tester", name="Mock", code="MOCK"
        )
        self.sv = StrategyVersion.objects.create(
            strategy=strategy,
            version="v1.0.0",
            module_path="apps.trading.tests",
            class_name="MockStrategy",
            status=StrategyVersion.Status.ACTIVE,
        )
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.sv,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    def test_regime_blocks_buy(self, mock_broker_fn):
        from apps.market.models import RegimeSnapshot
        from apps.order.models import Order
        from core.pipeline.trader_executor import execute_trader_for_stock

        broker = MagicMock()
        bal = MagicMock()
        bal.cash_balance = Decimal("10000000")
        bal.total_asset_value = Decimal("10000000")
        broker.get_balance.return_value = bal
        price = MagicMock()
        price.price = Decimal("70000")
        broker.get_current_price.return_value = price
        mock_broker_fn.return_value = broker

        account_run = ExecutionRun.objects.create(
            account=self.account,
            run_type=ExecutionRun.RunType.SCHEDULED,
            status=ExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        trader_run = TraderExecutionRun.objects.create(
            account_run=account_run,
            trader=self.trader,
            status=TraderExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        regime = RegimeSnapshot.objects.create(
            stock=self.stock,
            regime=RegimeSnapshot.Regime.BEAR,
            confidence_score=Decimal("0.9"),
            parameter_payload={"block_new_entries": True},
            analyzed_at=timezone.now(),
        )

        execute_trader_for_stock(self.trader, trader_run, self.stock, regime)

        dec = DecisionLog.objects.first()
        self.assertEqual(dec.final_action, DecisionLog.FinalAction.HOLD)
        self.assertIn("[Regime Guard]", dec.reason)
        self.assertEqual(Order.objects.count(), 0)


class BacktestReconciliationTestCase(TestCase):
    """trader_executor를 BacktestBroker로 구동했을 때 체결가/비용이
    리서치 엔진과 동일한 비용 모델(core.backtest.costs.estimate_fill)로 기록되는지 검증."""

    def setUp(self):
        self.user = User.objects.create_user(username="recon", password="pw")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="222",
            name="Recon",
            app_key_encrypted="k",
            app_secret_encrypted="s",
        )
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        self.trader = Trader.objects.create(
            account=self.account,
            name="Bot",
            code="BOT",
            position_size_ratio=Decimal("0.1"),
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),
            take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
        )
        strategy = Strategy.objects.create(
            owner=self.user, namespace="tester", name="Mock", code="MOCK"
        )
        self.sv = StrategyVersion.objects.create(
            strategy=strategy,
            version="v1.0.0",
            module_path="apps.trading.tests",
            class_name="MockStrategy",
            status=StrategyVersion.Status.ACTIVE,
        )
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=self.sv,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

    def test_fill_matches_cost_model(self):
        from core.backtest.broker import BacktestBroker
        from core.backtest.costs import CostConfig, SIDE_BUY, estimate_fill
        from core.pipeline.trader_executor import execute_trader_for_stock
        from apps.order.models import Order, TradeExecution

        broker = BacktestBroker(Decimal("10000000"))
        broker.set_market(self.stock.symbol, Decimal("70000"), Decimal("100000"))

        account_run = ExecutionRun.objects.create(
            account=self.account,
            run_type=ExecutionRun.RunType.SCHEDULED,
            status=ExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        trader_run = TraderExecutionRun.objects.create(
            account_run=account_run,
            trader=self.trader,
            status=TraderExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )

        with patch(
            "core.pipeline.trader_executor.get_broker_for_account", return_value=broker
        ):
            execute_trader_for_stock(self.trader, trader_run, self.stock, None)

        order = Order.objects.first()
        self.assertIsNotNone(order)
        execution = TradeExecution.objects.get(order=order)

        # 동일 비용 모델로 기대 체결 계산 (리서치 엔진과 같은 estimate_fill)
        expected = estimate_fill(
            SIDE_BUY, Decimal("70000"), order.quantity, Decimal("100000"), CostConfig()
        )
        self.assertEqual(execution.executed_price, expected.fill_price)
        self.assertEqual(execution.fee_amount, expected.commission)
        self.assertEqual(execution.tax_amount, Decimal("0"))  # 매수 무세금
        # 슬리피지로 체결가가 기준가보다 높다
        self.assertGreater(execution.executed_price, Decimal("70000"))


class ProbeStrategy:
    """look-ahead 검증용: 매 호출 시 받은 캔들 수를 기록하고 HOLD 반환."""

    seen: list = []

    def __init__(self, config):
        self.config = config

    def run(self, stock, candles, regime_snapshot):
        ProbeStrategy.seen.append(len(candles))
        return StrategyResult(
            action="HOLD", confidence_score=Decimal("0"), reason="probe"
        )


class MultiBarBacktestTestCase(TestCase):
    """trader_executor 다봉 백테스트: as_of 필터가 미래 캔들을 차단하는지 검증."""

    def setUp(self):
        from datetime import datetime, timezone as dt_tz

        self.user = User.objects.create_user(username="mb", password="pw")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="333",
            name="MB",
            app_key_encrypted="k",
            app_secret_encrypted="s",
        )
        self.stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        self.base = datetime(2024, 1, 2, 9, 0, tzinfo=dt_tz.utc)
        self.prices = [70000 + i * 100 for i in range(10)]
        for i, price in enumerate(self.prices):
            Candle.objects.create(
                stock=self.stock,
                timeframe=Candle.Timeframe.MIN_1,
                opened_at=self.base + timedelta(minutes=i),
                open_price=Decimal(str(price)),
                high_price=Decimal(str(price)),
                low_price=Decimal(str(price)),
                close_price=Decimal(str(price)),
                volume=Decimal("100000"),
                source="test",
            )
        self.trader = Trader.objects.create(
            account=self.account,
            name="Bot",
            code="BOT",
            position_size_ratio=Decimal("0.1"),
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),
            take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
            config_payload={"candle_timeframe": "1m"},
        )
        self.strategy = Strategy.objects.create(
            owner=self.user, namespace="tester", name="Probe", code="PROBE"
        )

    def _attach(self, class_name, cfg):
        sv = StrategyVersion.objects.create(
            strategy=self.strategy,
            version=f"v-{class_name}",
            module_path="apps.trading.tests",
            class_name=class_name,
            status=StrategyVersion.Status.ACTIVE,
        )
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=sv,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload=cfg,
            is_active=True,
        )

    def test_no_lookahead_candle_counts(self):
        from core.backtest.runner import run_trader_backtest

        ProbeStrategy.seen = []
        self._attach("ProbeStrategy", {})
        bars = [
            (self.base + timedelta(minutes=i), self.prices[i], 100000)
            for i in range(10)
        ]
        result = run_trader_backtest(self.trader, self.stock, bars, Decimal("10000000"))

        # 각 봉에서 as_of 이전 캔들만 → 1,2,...,10 (미래 미포함)
        self.assertEqual(ProbeStrategy.seen, list(range(1, 11)))
        self.assertEqual(result["num_bars"], 10)
        # HOLD만 있었으므로 자본 변화 없음
        self.assertEqual(result["final_equity"], Decimal("10000000"))

    def test_buy_strategy_executes_over_bars(self):
        from core.backtest.runner import run_trader_backtest

        self._attach("MockStrategy", {"action": "BUY", "confidence_score": "0.8"})
        bars = [
            (self.base + timedelta(minutes=i), self.prices[i], 100000) for i in range(5)
        ]
        result = run_trader_backtest(self.trader, self.stock, bars, Decimal("10000000"))

        self.assertGreater(Order.objects.filter(account=self.account).count(), 0)
        self.assertGreater(
            result["positions"].get(self.stock.symbol, Decimal("0")), Decimal("0")
        )
        self.assertLess(result["cash"], Decimal("10000000"))  # 매수+비용으로 현금 감소

        # TCA 지표/자본곡선 검증 (C-11)
        m = result["metrics"]
        for key in (
            "net_pnl",
            "total_cost",
            "cost_drag",
            "max_drawdown",
            "sharpe",
            "num_fills",
        ):
            self.assertIn(key, m)
        self.assertGreater(m["num_fills"], 0)
        self.assertGreater(m["total_cost"], 0.0)  # 실거래 비용 모델이 반영됨
        self.assertEqual(len(result["equity_curve"]), result["num_bars"] + 1)


class UniverseBacktestTestCase(TestCase):
    """멀티종목 드라이버: A-3 후보필터가 비유동 종목을 제외하고 포트폴리오 TCA를 낸다."""

    def setUp(self):
        from datetime import datetime, timezone as dt_tz

        self.user = User.objects.create_user(username="uni", password="pw")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="444",
            name="U",
            app_key_encrypted="k",
            app_secret_encrypted="s",
        )
        self.base = datetime(2024, 1, 2, tzinfo=dt_tz.utc)
        # 유동 2종목(대량 거래) + 비유동 1종목(소량)
        self.liquid = [
            Stock.objects.create(market=Stock.Market.KOSPI, symbol=s, name=s)
            for s in ("000001", "000002")
        ]
        self.illiquid = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="000003", name="illiquid"
        )
        for stock in self.liquid + [self.illiquid]:
            vol = 1_000_000 if stock in self.liquid else 100
            for i in range(10):
                p = Decimal(str(70000 + i * 100))
                Candle.objects.create(
                    stock=stock,
                    timeframe=Candle.Timeframe.DAY_1,
                    opened_at=self.base + timedelta(days=i),
                    open_price=p,
                    high_price=p,
                    low_price=p,
                    close_price=p,
                    volume=Decimal(str(vol)),
                    source="test",
                )
        self.trader = Trader.objects.create(
            account=self.account,
            name="Bot",
            code="BOT",
            position_size_ratio=Decimal("0.05"),
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),
            take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
            config_payload={"candle_timeframe": "1d"},
        )
        strat = Strategy.objects.create(
            owner=self.user, namespace="t", name="M", code="M"
        )
        sv = StrategyVersion.objects.create(
            strategy=strat,
            version="v1",
            module_path="apps.trading.tests",
            class_name="MockStrategy",
            status=StrategyVersion.Status.ACTIVE,
        )
        TraderStrategy.objects.create(
            trader=self.trader,
            strategy_version=sv,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

    def test_candidate_filter_excludes_illiquid(self):
        from core.backtest.universe_runner import run_universe_backtest
        from core.universe.filter import CandidateConfig
        from apps.order.models import Order

        cfg = CandidateConfig(min_turnover=1e8, vol_low=0.0, vol_high=1.0, top_k=5)
        bars = [self.base + timedelta(days=8), self.base + timedelta(days=9)]
        result = run_universe_backtest(
            self.trader,
            self.liquid + [self.illiquid],
            bars,
            Decimal("100000000"),
            candidate_config=cfg,
        )

        # 비유동 종목은 한 번도 후보에 못 들어 주문 없음
        self.assertEqual(Order.objects.filter(stock=self.illiquid).count(), 0)
        # 유동 종목은 거래 발생
        self.assertGreater(Order.objects.filter(stock__in=self.liquid).count(), 0)
        # 매 봉 후보 2종목(유동)만 선택
        self.assertTrue(all(c == 2 for c in result["selected_counts"]))
        self.assertIn("net_pnl", result["metrics"])
        self.assertGreater(result["metrics"]["num_fills"], 0)


class ContextFeatureFlowTestCase(TestCase):
    """A-4: context가 trader_executor를 거쳐 FeatureSnapshot에 조인되는지 검증."""

    @patch("core.pipeline.trader_executor.get_broker_for_account")
    def test_context_reaches_feature_snapshot(self, mock_broker_fn):
        from datetime import datetime, timezone as dt_tz
        from apps.market.models import FeatureSnapshot
        from core.pipeline.trader_executor import execute_trader_for_stock

        user = User.objects.create_user(username="ctx", password="pw")
        account = Account.objects.create(
            user=user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="555",
            name="C",
            app_key_encrypted="k",
            app_secret_encrypted="s",
        )
        stock = Stock.objects.create(
            market=Stock.Market.KOSPI, symbol="005930", name="삼성전자"
        )
        base = datetime(2024, 1, 2, 9, 0, tzinfo=dt_tz.utc)
        for i in range(20):
            p = Decimal(str(70000 + i * 10))
            Candle.objects.create(
                stock=stock,
                timeframe=Candle.Timeframe.MIN_1,
                opened_at=base + timedelta(minutes=i),
                open_price=p,
                high_price=p,
                low_price=p,
                close_price=p,
                volume=Decimal("1000"),
                source="test",
            )
        trader = Trader.objects.create(
            account=account,
            name="Bot",
            code="BOT",
            ml_filter_enabled=True,
            position_size_ratio=Decimal("0.1"),
            entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"),
            take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
            config_payload={"candle_timeframe": "1m"},
        )
        strat = Strategy.objects.create(owner=user, namespace="t", name="M", code="M")
        sv = StrategyVersion.objects.create(
            strategy=strat,
            version="v1",
            module_path="apps.trading.tests",
            class_name="MockStrategy",
            status=StrategyVersion.Status.ACTIVE,
        )
        TraderStrategy.objects.create(
            trader=trader,
            strategy_version=sv,
            slot=TraderStrategy.Slot.FIRST,
            weight=Decimal("1.0"),
            config_payload={"action": "BUY", "confidence_score": "0.8"},
            is_active=True,
        )

        broker = MagicMock()
        bal = MagicMock()
        bal.cash_balance = Decimal("10000000")
        bal.total_asset_value = Decimal("10000000")
        bal.raw_payload = {}
        broker.get_balance.return_value = bal
        price = MagicMock()
        price.price = Decimal("70190")
        broker.get_current_price.return_value = price
        mock_broker_fn.return_value = broker

        account_run = ExecutionRun.objects.create(
            account=account,
            run_type=ExecutionRun.RunType.SCHEDULED,
            status=ExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        run = TraderExecutionRun.objects.create(
            account_run=account_run,
            trader=trader,
            status=TraderExecutionRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        execute_trader_for_stock(
            trader,
            run,
            stock,
            None,
            context={"index_ret_1": 0.0005, "cs_return_rank": 0.7},
        )

        fs = FeatureSnapshot.objects.first()
        self.assertIsNotNone(fs)
        self.assertEqual(fs.feature_payload["cs_return_rank"], 0.7)
        self.assertIn("excess_ret_1", fs.feature_payload)  # index_ret_1 + ret_1 조인


class FillConfirmationTestCase(TestCase):
    """체결 확인: 브로커 실제 체결분만 원장에 반영, 상태/이벤트 추적."""

    def setUp(self):
        self.user = User.objects.create_user(username="fc", password="pw")
        self.account = Account.objects.create(
            user=self.user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="777",
            name="FC", app_key_encrypted="k", app_secret_encrypted="s",
        )
        self.stock = Stock.objects.create(market=Stock.Market.KOSPI, symbol="005930", name="삼성전자")
        run = ExecutionRun.objects.create(
            account=self.account, run_type=ExecutionRun.RunType.SCHEDULED,
            status=ExecutionRun.Status.RUNNING, started_at=timezone.now(),
        )
        trader = Trader.objects.create(
            account=self.account, name="B", code="B",
            position_size_ratio=Decimal("0.1"), entry_threshold=Decimal("0.5"),
            stop_loss_ratio=Decimal("0.05"), take_profit_ratio=Decimal("0.1"),
            max_exposure_ratio=Decimal("0.3"),
        )
        self.trader = trader
        tr_run = TraderExecutionRun.objects.create(
            account_run=run, trader=trader,
            status=TraderExecutionRun.Status.RUNNING, started_at=timezone.now(),
        )
        self.decision = DecisionLog.objects.create(
            trader_run=tr_run, stock=self.stock,
            final_action=DecisionLog.FinalAction.BUY, decided_at=timezone.now(),
        )

    def _broker(self, execs):
        broker = MagicMock()
        res = MagicMock()
        res.success = True; res.order_id = "X1"; res.error_message = None
        res.raw_payload = {}  # fill_price 없음 → 체결조회 경로
        broker.create_order.return_value = res
        broker.get_order_execution.return_value = execs
        return broker

    def test_partial_fill_reflected(self):
        from core.pipeline.trader_executor import execute_order
        from apps.order.models import Order, OrderEvent

        broker = self._broker([
            {"order_no": "X1", "filled_qty": Decimal("6"), "avg_price": Decimal("70100")},
        ])
        execute_order(self.trader, self.decision, self.stock, Order.Side.BUY,
                      Decimal("10"), Decimal("70000"), broker)

        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        ex = TradeExecution.objects.get()
        self.assertEqual(ex.executed_quantity, Decimal("6"))
        self.assertEqual(ex.executed_price, Decimal("70100"))
        self.assertEqual(PositionLedger.objects.first().quantity_delta, Decimal("6"))
        self.assertEqual(OrderEvent.objects.filter(order=order).count(), 2)  # ACCEPTED+PARTIAL

    def test_pending_when_no_fill(self):
        from core.pipeline.trader_executor import execute_order
        from apps.order.models import Order

        broker = self._broker([])  # 체결 없음(미체결)
        execute_order(self.trader, self.decision, self.stock, Order.Side.BUY,
                      Decimal("10"), Decimal("70000"), broker)

        order = Order.objects.get()
        self.assertEqual(order.status, Order.Status.ACCEPTED)  # 대기
        self.assertEqual(TradeExecution.objects.count(), 0)
        self.assertEqual(PositionLedger.objects.count(), 0)
