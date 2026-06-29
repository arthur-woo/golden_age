from decimal import Decimal
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class AccountBalanceDTO:
    """계좌 잔고 및 평가 금액 정보"""
    cash_balance: Decimal
    total_asset_value: Decimal
    raw_payload: Dict[str, Any]


@dataclass
class PriceDTO:
    """현재가 정보"""
    symbol: str
    price: Decimal
    volume: Decimal
    raw_payload: Dict[str, Any]


@dataclass
class OrderResultDTO:
    """주문 요청 결과"""
    success: bool
    order_id: Optional[str]
    error_message: Optional[str]
    raw_payload: Dict[str, Any]
