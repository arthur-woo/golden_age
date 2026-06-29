import pandas as pd
from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    """
    모든 전략이 상속받아야 하는 기본 클래스입니다.
    빠른 백테스트를 위해 Vectorized(벡터화) 방식으로 전체 기간의 신호를 한 번에 생성합니다.
    """
    def __init__(self, name: str, **kwargs):
        self.name = name
        self.params = kwargs

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        입력: 'date', 'open', 'high', 'low', 'close', 'volume' 컬럼이 있는 DataFrame
        출력: 원본 인덱스와 동일한 길이를 가지며, 
              'decision' (1=BUY, -1=SELL, 0=HOLD), 
              'score' (0~100 확신도) 컬럼이 포함된 DataFrame
        """
        pass
