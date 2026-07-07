from abc import ABC, abstractmethod
from typing import List, Optional
from apps.stock.models import Stock
from apps.market.models import Candle, RegimeSnapshot
from core.pipeline.strategy_runner import StrategyResult


class BaseStrategy(ABC):
    """
    모든 Rule-based 전략의 공통 추상 베이스 클래스 (Base Class)
    """

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def run(
        self,
        stock: Stock,
        candles: List[Candle],
        regime_snapshot: Optional[RegimeSnapshot] = None,
    ) -> StrategyResult:
        """
        전략 실행 진입점.
        입력값에 대한 판단 결과(BUY/SELL/HOLD 및 신뢰도 점수)를 반환해야 합니다.
        동일한 입력값에 대해 항상 동일한 결과를 출력하는 결정적(Deterministic) 구조여야 합니다.
        """
        pass
