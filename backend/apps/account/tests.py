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
