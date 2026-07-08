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
            "token_type": "Bearer"
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
        mock_post.return_value.json.return_value = {"access_token": "mock", "expires_in": 3600}
        
        # balance mock
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "output2": [{
                "dnca_tot_amt": "1000000",
                "tot_evlu_amt": "1500000"
            }]
        }
        mock_request.return_value = mock_response

        broker = KoreaInvestmentBroker(self.account)
        balance = broker.get_balance()

        self.assertEqual(balance.cash_balance, Decimal("1000000"))
        self.assertEqual(balance.total_asset_value, Decimal("1500000"))
        
    @patch("core.broker.kis.client.requests.post")
    @patch("core.broker.kis.client.requests.request")
    def test_create_order(self, mock_request, mock_post):
        mock_post.return_value.json.return_value = {"access_token": "mock", "expires_in": 3600}
        
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "rt_cd": "0",
            "msg1": "정상처리되었습니다.",
            "output": {"ODNO": "987654321"}
        }
        mock_request.return_value = mock_response

        broker = KoreaInvestmentBroker(self.account)
        order = broker.create_order(symbol="005930", side="BUY", quantity=Decimal("10"))

        self.assertTrue(order.success)
        self.assertEqual(order.order_id, "987654321")


from core.broker.kis.broker import parse_minute_candles


class KISDataCollectionTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="kisdata", password="pw")
        self.account = Account.objects.create(
            user=self.user, broker=Account.Broker.KIS,
            account_type=Account.AccountType.PAPER, account_number="1234567801",
            name="A", app_key_encrypted="ak", app_secret_encrypted="sk",
        )

    def test_parse_minute_candles_ascending(self):
        data = {"output2": [
            {"stck_bsop_date": "20240102", "stck_cntg_hour": "090100",
             "stck_oprc": "70000", "stck_hgpr": "70200", "stck_lwpr": "69900",
             "stck_prpr": "70100", "cntg_vol": "1000"},
            {"stck_bsop_date": "20240102", "stck_cntg_hour": "090000",
             "stck_oprc": "69900", "stck_hgpr": "70000", "stck_lwpr": "69800",
             "stck_prpr": "70000", "cntg_vol": "500"},
        ]}
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
        mock_post.return_value.json.return_value = {"access_token": "t", "expires_in": 3600}
        mock_request.return_value.json.return_value = {"output2": [
            {"stck_bsop_date": "20240102", "stck_cntg_hour": "090000",
             "stck_oprc": "69900", "stck_hgpr": "70000", "stck_lwpr": "69800",
             "stck_prpr": "70000", "cntg_vol": "500"},
        ]}
        candles = KoreaInvestmentBroker(self.account).get_minute_candles("005930")
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]["close"], Decimal("70000"))
