import logging
from datetime import timedelta
import requests
from django.utils import timezone
from django.conf import settings

from apps.account.models import Account, BrokerToken

logger = logging.getLogger(__name__)


class KISClient:
    """한국투자증권 Open API HTTP Client"""

    LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
    PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"

    def __init__(self, account: Account):
        self.account = account
        self.base_url = self.LIVE_BASE_URL if account.account_type == Account.AccountType.LIVE else self.PAPER_BASE_URL

    def _get_app_key(self) -> str:
        # 실제로는 암호화 해제 로직 적용 (현재는 그대로 반환으로 임시 구현)
        return self.account.app_key_encrypted

    def _get_app_secret(self) -> str:
        return self.account.app_secret_encrypted

    def get_access_token(self) -> str:
        """
        유효한 Access Token 반환
        만료된 경우 자동으로 재발급 후 DB에 저장
        """
        valid_token = self.account.tokens.filter(
            token_type="Bearer",
            revoked_at__isnull=True,
            expires_at__gt=timezone.now()
        ).order_by("-expires_at").first()

        if valid_token:
            return valid_token.access_token_encrypted

        return self.issue_access_token()

    def issue_access_token(self) -> str:
        """새로운 Access Token 발급 요청"""
        url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._get_app_key(),
            "appsecret": self._get_app_secret()
        }
        headers = {"content-type": "application/json"}
        
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 86400)

        # 토큰 DB 저장
        BrokerToken.objects.create(
            account=self.account,
            access_token_encrypted=access_token,
            token_type=data.get("token_type", "Bearer"),
            expires_at=timezone.now() + timedelta(seconds=int(expires_in)),
            issued_at=timezone.now(),
        )

        return access_token

    def request(self, method: str, path: str, headers: dict = None, **kwargs) -> requests.Response:
        """인증이 포함된 HTTP 요청 수행"""
        if headers is None:
            headers = {}

        headers.update({
            "authorization": f"Bearer {self.get_access_token()}",
            "appkey": self._get_app_key(),
            "appsecret": self._get_app_secret(),
        })

        url = f"{self.base_url}{path}"
        response = requests.request(method, url, headers=headers, **kwargs)
        
        # 만료 등에 대한 공통 예외 처리 추가 가능
        return response
