from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.account.models import Account, BrokerToken
from core.broker.kis.client import KISClient
from core.broker.kis.broker import KoreaInvestmentBroker

User = get_user_model()


class BrokerTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="1234567801",
            name="Test Paper Account",
            app_key_encrypted="mocked_app_key",
            app_secret_encrypted="mocked_app_secret",
        )

    @patch("core.broker.kis.client.requests.post")
    def test_issue_access_token(self, mock_post):
        # mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "mocked_access_token",
            "expires_in": 86400,
            "token_type": "Bearer",
        }
        mock_post.return_value = mock_response

        client = KISClient(self.account)
        token = client.get_access_token()

        self.assertEqual(token, "mocked_access_token")
        self.assertEqual(BrokerToken.objects.count(), 1)
        db_token = BrokerToken.objects.first()
        self.assertEqual(db_token.access_token_encrypted, "mocked_access_token")

    @patch("core.broker.kis.client.requests.post")
    @patch("core.broker.kis.client.requests.request")
    def test_get_balance(self, mock_request, mock_post):
        # token mock
        mock_post.return_value.json.return_value = {
            "access_token": "mock",
            "expires_in": 3600,
        }

        # balance mock
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1500000"}]
        }
        mock_request.return_value = mock_response

        broker = KoreaInvestmentBroker(self.account)
        balance = broker.get_balance()

        self.assertEqual(balance.cash_balance, Decimal("1000000"))
        self.assertEqual(balance.total_asset_value, Decimal("1500000"))

    @patch("core.broker.kis.client.requests.post")
    @patch("core.broker.kis.client.requests.request")
    def test_create_order(self, mock_request, mock_post):
        # requests.post는 토큰 발급과 hashkey 발급에 모두 쓰이므로 두 키를 함께 제공
        mock_post.return_value.json.return_value = {
            "access_token": "mock",
            "expires_in": 3600,
            "HASH": "hash123",
        }

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "rt_cd": "0",
            "msg1": "정상처리되었습니다.",
            "output": {"ODNO": "987654321"},
        }
        mock_request.return_value = mock_response

        broker = KoreaInvestmentBroker(self.account)
        order = broker.create_order(symbol="005930", side="BUY", quantity=Decimal("10"))

        self.assertTrue(order.success)
        self.assertEqual(order.order_id, "987654321")
        # 주문 요청에 hashkey/custtype 헤더가 포함됐는지
        sent_headers = mock_request.call_args[1]["headers"]
        self.assertEqual(sent_headers["hashkey"], "hash123")
        self.assertEqual(sent_headers["custtype"], "P")


from core.broker.kis.broker import parse_minute_candles


class KISDataCollectionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="kisdata", password="pw")
        self.account = Account.objects.create(
            user=self.user,
            broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER,
            account_number="1234567801",
            name="A",
            app_key_encrypted="ak",
            app_secret_encrypted="sk",
        )

    def test_parse_minute_candles_ascending(self):
        data = {
            "output2": [
                {
                    "stck_bsop_date": "20240102",
                    "stck_cntg_hour": "090100",
                    "stck_oprc": "70000",
                    "stck_hgpr": "70200",
                    "stck_lwpr": "69900",
                    "stck_prpr": "70100",
                    "cntg_vol": "1000",
                },
                {
                    "stck_bsop_date": "20240102",
                    "stck_cntg_hour": "090000",
                    "stck_oprc": "69900",
                    "stck_hgpr": "70000",
                    "stck_lwpr": "69800",
                    "stck_prpr": "70000",
                    "cntg_vol": "500",
                },
            ]
        }
        candles = parse_minute_candles(data)
        self.assertEqual(len(candles), 2)
        # 오름차순 정렬: 09:00 먼저
        self.assertEqual(candles[0]["opened_at"].minute, 0)
        self.assertEqual(candles[0]["close"], Decimal("70000"))
        self.assertEqual(candles[1]["opened_at"].minute, 1)
        self.assertEqual(candles[1]["volume"], Decimal("1000"))

    @patch("core.broker.kis.client.requests.post")
    def test_get_approval_key(self, mock_post):
        mock_post.return_value.json.return_value = {"approval_key": "AK123"}
        key = KISClient(self.account).get_approval_key()
        self.assertEqual(key, "AK123")
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertIn("/oauth2/Approval", url)
        self.assertEqual(kwargs["json"]["secretkey"], "sk")

    @patch("core.broker.kis.client.requests.post")
    @patch("core.broker.kis.client.requests.request")
    def test_get_minute_candles(self, mock_request, mock_post):
        mock_post.return_value.json.return_value = {
            "access_token": "t",
            "expires_in": 3600,
        }
        mock_request.return_value.json.return_value = {
            "output2": [
                {
                    "stck_bsop_date": "20240102",
                    "stck_cntg_hour": "090000",
                    "stck_oprc": "69900",
                    "stck_hgpr": "70000",
                    "stck_lwpr": "69800",
                    "stck_prpr": "70000",
                    "cntg_vol": "500",
                },
            ]
        }
        candles = KoreaInvestmentBroker(self.account).get_minute_candles("005930")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["close"], Decimal("70000"))


from core.broker.kis.broker import parse_executions


class KISOrderExecutionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="kisord", password="pw")
        self.account = Account.objects.create(
            user=self.user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1234567801",
            name="A", app_key_encrypted="ak", app_secret_encrypted="sk",
        )

    def test_parse_executions(self):
        data = {"output1": [
            {"odno": "987654321", "pdno": "005930", "ord_qty": "10",
             "tot_ccld_qty": "7", "avg_prvs": "70050", "rmn_qty": "3"},
        ]}
        execs = parse_executions(data)
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0]["order_no"], "987654321")
        self.assertEqual(execs[0]["filled_qty"], Decimal("7"))
        self.assertEqual(execs[0]["avg_price"], Decimal("70050"))
        self.assertEqual(execs[0]["remaining_qty"], Decimal("3"))

    @patch("core.broker.kis.client.requests.post")
    @patch("core.broker.kis.client.requests.request")
    def test_get_order_execution(self, mock_request, mock_post):
        mock_post.return_value.json.return_value = {"access_token": "t", "expires_in": 3600}
        mock_request.return_value.json.return_value = {"output1": [
            {"odno": "987654321", "pdno": "005930", "ord_qty": "10",
             "tot_ccld_qty": "10", "avg_prvs": "70000", "rmn_qty": "0"},
        ]}
        execs = KoreaInvestmentBroker(self.account).get_order_execution(order_no="987654321")
        self.assertEqual(len(execs), 1)
        self.assertEqual(execs[0]["filled_qty"], Decimal("10"))
        # 조회 파라미터에 주문번호가 실렸는지
        self.assertEqual(mock_request.call_args[1]["params"]["ODNO"], "987654321")


from django.core.management import call_command
from io import StringIO


class RegisterScheduleTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sched", password="pw")
        self.account = Account.objects.create(
            user=self.user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1234567801",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )

    def test_creates_minute_schedule(self):
        from django_q.models import Schedule

        call_command("register_schedule", "--account-id", str(self.account.id),
                     "--minutes", "1", stdout=StringIO())
        s = Schedule.objects.get(name=f"daytrading-account-{self.account.id}")
        self.assertEqual(s.func, "django.core.management.call_command")
        self.assertEqual(s.schedule_type, Schedule.MINUTES)
        self.assertEqual(s.minutes, 1)
        self.assertIn("run_account_pipeline", s.args)

    def test_idempotent(self):
        from django_q.models import Schedule

        for _ in range(2):
            call_command("register_schedule", "--account-id", str(self.account.id),
                         stdout=StringIO())
        self.assertEqual(
            Schedule.objects.filter(name=f"daytrading-account-{self.account.id}").count(), 1
        )

    def test_missing_account_errors(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("register_schedule", "--account-id", "99999", stdout=StringIO())


class RegisterBackfillScheduleTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bfsched", password="pw")
        self.account = Account.objects.create(
            user=self.user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1234567801",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )

    def test_creates_backfill_schedule(self):
        from django.core.management import call_command
        from io import StringIO
        from django_q.models import Schedule

        call_command("register_backfill_schedule", "--account-id", str(self.account.id),
                     "--minutes", "5", stdout=StringIO())
        s = Schedule.objects.get(name=f"backfill-universe-{self.account.id}")
        self.assertEqual(s.func, "django.core.management.call_command")
        self.assertEqual(s.schedule_type, Schedule.MINUTES)
        self.assertEqual(s.minutes, 5)
        self.assertIn("backfill_universe", s.args)


class ReconcileTestCase(TestCase):
    def test_reconcile_applies_adjustment(self):
        from decimal import Decimal as D
        from apps.stock.models import Stock
        from apps.account.models import PositionLedger
        from core.backtest.broker import BacktestBroker
        from core.pipeline.reconcile import reconcile_account
        from django.utils import timezone as tz

        user = User.objects.create_user("recon", password="pw")
        account = Account.objects.create(
            user=user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )
        stock = Stock.objects.create(market=Stock.Market.KOSPI, symbol="000001", name="A")
        # 내부 원장 7주
        PositionLedger.objects.create(
            account=account, stock=stock, quantity_delta=D("7"), price=D("100"),
            reason="init", occurred_at=tz.now(),
        )
        # 브로커 실보유 10주
        broker = BacktestBroker(D("0"))
        broker.positions["000001"] = D("10")

        # 대조만
        report = reconcile_account(account, broker, apply=False)
        self.assertEqual(report["positions"]["000001"]["diff"], 3.0)
        self.assertEqual(report["adjusted"], 0)

        # 보정 적용 → 내부가 브로커(10)에 맞춰짐
        applied = reconcile_account(account, broker, apply=True)
        self.assertEqual(applied["adjusted"], 1)
        from django.db.models import Sum
        net = PositionLedger.objects.filter(account=account).aggregate(q=Sum("quantity_delta"))["q"]
        self.assertEqual(net, D("10"))


class RiskMonitorTestCase(TestCase):
    def test_detects_stop_breach(self):
        from decimal import Decimal as D
        from unittest.mock import MagicMock as MM
        from django.utils import timezone as tz
        from apps.stock.models import Stock
        from apps.account.models import PositionLedger
        from core.pipeline.risk_monitor import find_breached_positions

        user = User.objects.create_user("rm", password="pw")
        account = Account.objects.create(
            user=user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )
        stock = Stock.objects.create(market=Stock.Market.KOSPI, symbol="005930", name="삼성전자")
        # 평단 70000, 5주 보유
        PositionLedger.objects.create(
            account=account, stock=stock, quantity_delta=D("5"), price=D("70000"),
            reason="init", occurred_at=tz.now(),
        )
        broker = MM()
        px = MM(); px.price = D("65000")  # -7.1% → 5% 손절선 이탈
        broker.get_current_price.return_value = px

        breached = find_breached_positions(account, broker, stop_ratio=0.05, take_ratio=0.1)
        self.assertEqual(len(breached), 1)
        self.assertEqual(breached[0][2], "STOP")
        self.assertEqual(breached[0][1], D("5"))

    def test_no_breach_within_bands(self):
        from decimal import Decimal as D
        from unittest.mock import MagicMock as MM
        from django.utils import timezone as tz
        from apps.stock.models import Stock
        from apps.account.models import PositionLedger
        from core.pipeline.risk_monitor import find_breached_positions

        user = User.objects.create_user("rm2", password="pw")
        account = Account.objects.create(
            user=user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="2",
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )
        stock = Stock.objects.create(market=Stock.Market.KOSPI, symbol="005930", name="삼성전자")
        PositionLedger.objects.create(
            account=account, stock=stock, quantity_delta=D("5"), price=D("70000"),
            reason="init", occurred_at=tz.now(),
        )
        broker = MM()
        px = MM(); px.price = D("70500")  # 밴드 내
        broker.get_current_price.return_value = px
        self.assertEqual(find_breached_positions(account, broker, 0.05, 0.1), [])


from core.broker.kis.broker import parse_market_status


class MarketStatusTestCase(TestCase):
    def test_normal_is_tradeable(self):
        st = parse_market_status({"stck_prpr": "70000", "stck_mxpr": "91000", "stck_llam": "49000", "trht_yn": "N"})
        self.assertTrue(st["tradeable"])
        self.assertFalse(st["at_limit"])

    def test_upper_limit(self):
        st = parse_market_status({"stck_prpr": "91000", "stck_mxpr": "91000", "stck_llam": "49000"})
        self.assertTrue(st["at_upper_limit"])
        self.assertFalse(st["tradeable"])

    def test_halted(self):
        st = parse_market_status({"stck_prpr": "70000", "stck_mxpr": "91000", "stck_llam": "49000", "trht_yn": "Y"})
        self.assertTrue(st["halted"])
        self.assertFalse(st["tradeable"])


from core.broker.kis.broker import parse_orderbook


class OrderbookTestCase(TestCase):
    def test_parse_orderbook(self):
        ob = parse_orderbook({
            "bidp1": "69900", "askp1": "70000",
            "bidp_rsqn1": "300", "askp_rsqn1": "100",
        })
        self.assertEqual(ob["bid1"], Decimal("69900"))
        self.assertEqual(ob["ask1"], Decimal("70000"))
        self.assertGreater(ob["spread_bps"], 0)
        self.assertAlmostEqual(ob["imbalance"], 0.5, places=6)  # (300-100)/400

    def test_empty_orderbook(self):
        ob = parse_orderbook({})
        self.assertIsNone(ob["spread_bps"])
        self.assertEqual(ob["imbalance"], 0.0)


class HealthCheckTestCase(TestCase):
    def _account(self, n):
        user = User.objects.create_user(f"h{n}", password="pw")
        return Account.objects.create(
            user=user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number=str(n),
            name="A", app_key_encrypted="k", app_secret_encrypted="s",
        )

    def test_fresh_data_is_healthy(self):
        from decimal import Decimal as D
        from apps.stock.models import Stock
        from apps.market.models import Candle
        from core.pipeline.health import system_health

        account = self._account(1)
        stock = Stock.objects.create(market=Stock.Market.KOSPI, symbol="005930", name="삼성")
        Candle.objects.create(
            stock=stock, timeframe=Candle.Timeframe.MIN_1, opened_at=timezone.now(),
            open_price=D("1"), high_price=D("1"), low_price=D("1"), close_price=D("1"),
            volume=D("1"), source="test",
        )
        h = system_health(account, stale_minutes=5)
        self.assertTrue(h["healthy"])
        self.assertFalse(h["data_stale"])

    def test_stale_data_is_unhealthy(self):
        from core.pipeline.health import system_health
        account = self._account(2)
        h = system_health(account, stale_minutes=5)  # 캔들 없음 → stale
        self.assertFalse(h["healthy"])
        self.assertTrue(h["data_stale"])
