"""
Strategy Runner

DB에 저장된 StrategyVersion의 module_path와 class_name을 이용해
실제 Python 클래스를 동적 로딩하고 실행한다.

Strategy 인터페이스 규약:
- __init__(self, config: dict)
- run(self, stock, candles, regime_snapshot) -> StrategyResult
"""

import importlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from apps.market.models import Candle, RegimeSnapshot
from apps.stock.models import Stock
from apps.trading.models import StrategyVersion

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """Strategy 실행 결과 (Strategy가 반환해야 하는 표준 구조)"""
    action: str  # "BUY", "SELL", "HOLD"
    confidence_score: Decimal  # 0.0 ~ 1.0
    reason: str = ""


class StrategyRunner:
    """
    StrategyVersion을 동적으로 로딩하여 실행하는 러너

    Strategy 클래스는 다음 인터페이스를 구현해야 한다:
    - __init__(self, config: dict)
    - run(self, stock: Stock, candles: List[Candle], regime_snapshot: Optional[RegimeSnapshot]) -> StrategyResult
    """

    def __init__(self, strategy_version: StrategyVersion, config: Optional[dict] = None):
        self.strategy_version = strategy_version
        self.config = config or strategy_version.default_config

    def load_strategy_class(self):
        """module_path에서 Strategy 클래스를 동적 로딩"""
        try:
            module = importlib.import_module(self.strategy_version.module_path)
            strategy_class = getattr(module, self.strategy_version.class_name)
            return strategy_class
        except (ImportError, AttributeError) as e:
            logger.error(
                "Strategy 로딩 실패: %s.%s - %s",
                self.strategy_version.module_path,
                self.strategy_version.class_name,
                e,
            )
            raise

    def run(
        self,
        stock: Stock,
        candles: List[Candle],
        regime_snapshot: Optional[RegimeSnapshot] = None,
    ) -> StrategyResult:
        """
        Strategy를 실행하여 결과를 반환한다.

        Strategy는 결정적(Deterministic)이어야 한다.
        동일한 입력에 동일한 결과를 반환한다.
        """
        strategy_class = self.load_strategy_class()
        strategy_instance = strategy_class(config=self.config)

        result = strategy_instance.run(
            stock=stock,
            candles=candles,
            regime_snapshot=regime_snapshot,
        )

        # 결과 타입 정규화
        if isinstance(result, StrategyResult):
            return result

        # dict 형태 결과도 허용 (호환성)
        if isinstance(result, dict):
            return StrategyResult(
                action=result.get("action", "HOLD"),
                confidence_score=Decimal(str(result.get("confidence_score", 0))),
                reason=result.get("reason", ""),
            )

        raise TypeError(f"Strategy가 알 수 없는 결과 타입을 반환했습니다: {type(result)}")
