from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from .dtos import AccountBalanceDTO, PriceDTO, OrderResultDTO


class BaseBroker(ABC):
    """
    증권사 API 통신을 위한 공용 인터페이스
    """

    @abstractmethod
    def get_balance(self) -> AccountBalanceDTO:
        """계좌 잔고 조회"""
        pass

    @abstractmethod
    def get_current_price(self, symbol: str) -> PriceDTO:
        """종목 현재가 조회"""
        pass

    @abstractmethod
    def create_order(
        self,
        symbol: str,
        side: str,  # 'BUY', 'SELL'
        quantity: Decimal,
        price: Optional[Decimal] = None,  # None이면 시장가
    ) -> OrderResultDTO:
        """주문 생성"""
        pass
